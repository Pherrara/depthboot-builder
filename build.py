#!/usr/bin/env python3

import os
from shutil import rmtree as rmdir
from shutil import copy as cp
from shutil import copytree as cpdir
from pathlib import Path
import sys
import argparse
from urllib.request import urlretrieve
import subprocess as sp
from os import system as bash
from threading import Thread
from urllib.error import URLError
from time import sleep

import user_input


# parse arguments from the cli. Only for testing/advanced use. 95% of the arguments are handled by the user_input script
def process_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--local-path', dest="local_path",
                        help="Local path for kernel files, to use instead of downloading from github." +
                             "(Unsigned kernels only)")
    parser.add_argument("--dev", action="store_true", dest="dev_build", default=False,
                        help="Use latest dev build. May be unstable.")
    parser.add_argument("--alt", action="store_true", dest="alt", default=False,
                        help="Use alt kernel. Only for older devices.")
    parser.add_argument("--exp", action="store_true", dest="exp", default=False,
                        help="Use experimental 5.15 kernel.")
    parser.add_argument("--mainline", action="store_true", dest="mainline", default=False,
                        help="Use mainline linux kernel instead of modified chromeos kernel.")
    parser.add_argument("-v", "--verbose", action="store_true", dest="verbose", default=False, help="Print more output")
    return parser.parse_args()


# Clean /tmp from eupnea files
def prepare_host(de_name: str) -> None:
    print("\033[96m" + "Preparing host system" + "\033[0m")

    print("Creating /tmp/eupnea-build")
    rmdir("/tmp/eupnea-build", ignore_errors=True)
    Path("/tmp/eupnea-build/rootfs").mkdir(parents=True)

    print("Creating mnt point")
    bash("umount -lf /mnt/eupnea 2>/dev/null")  # just in case
    rmdir("/mnt/eupnea", ignore_errors=True)
    Path("/mnt/eupnea").mkdir(parents=True, exist_ok=True)

    print("Remove old files if they exist")
    try:
        os.remove("eupnea.img")
    except FileNotFoundError:
        pass
    try:
        os.remove("kernel.flags")
    except FileNotFoundError:
        pass

    print("Installing necessary packages")
    # install cgpt and futility
    if os.path.exists("/usr/bin/apt"):
        bash("apt install cgpt vboot-kernel-utils -y")
    elif os.path.exists("/usr/bin/pacman"):
        bash("pacman -S cgpt vboot-utils --noconfirm")
    elif os.path.exists("/usr/bin/dnf"):
        bash("dnf install cgpt vboot-utils --assumeyes")
    else:
        print("\033[91m" + "cgpt and futility not found, please install them using your disotros package manager"
              + "\033[0m")
        exit(1)

    # install debootstrap for debian
    if de_name == "debian":
        if os.path.exists("/usr/bin/apt"):
            bash("apt install debootstrap -y")
        elif os.path.exists("/usr/bin/pacman"):
            bash("pacman -S debootstrap --noconfirm")
        elif os.path.exists("/usr/bin/dnf"):
            bash("dnf install debootstrap --assumeyes")
        else:
            print("\033[91m" + "Debootstrap not found, please install it using your disotros package manager or select"
                  + " another distro instead of debian" + "\033[0m")
            exit(1)

    # install arch-chroot for arch
    elif de_name == "arch":
        if os.path.exists("/usr/bin/apt"):
            bash("apt install arch-install-scripts -y")
        elif os.path.exists("/usr/bin/pacman"):
            bash("pacman -S arch-install-scripts --noconfirm")
        elif os.path.exists("/usr/bin/dnf"):
            bash("dnf install arch-install-scripts --assumeyes")
        else:
            print(
                "\033[91m" + "Arch-install-scripts not found, please install it using your disotros package manager" +
                " or select another distro instead of arch" + "\033[0m")
            exit(1)


# download kernel files from GitHub
def download_kernel() -> None:
    print("\033[96m" + "Downloading kernel binaries from github" + "\033[0m")
    # select correct link
    if args.dev_build:
        url = "https://github.com/eupnea-linux/kernel/releases/download/dev-build/"
    else:
        url = "https://github.com/eupnea-linux/kernel/releases/latest/download/"

    # download kernel files
    try:
        if args.mainline:
            url = "https://github.com/eupnea-linux/mainline-kernel/releases/latest/download/"
            urlretrieve(f"{url}bzImage", filename="/tmp/eupnea-build/bzImage")
            urlretrieve(f"{url}modules.tar.xz", filename="/tmp/eupnea-build/modules.tar.xz")
        else:
            if args.alt:
                print("Downloading alt kernel")
                urlretrieve(f"{url}bzImage-alt", filename="/tmp/eupnea-build/bzImage")
                urlretrieve(f"{url}modules-alt.tar.xz", filename="/tmp/eupnea-build/modules.tar.xz")
            elif args.exp:
                print("Downloading experimental 5.15 kernel")
                urlretrieve(f"{url}bzImage-exp", filename="/tmp/eupnea-build/bzImage")
                urlretrieve(f"{url}modules-exp.tar.xz", filename="/tmp/eupnea-build/modules.tar.xz")
            else:
                urlretrieve(f"{url}bzImage", filename="/tmp/eupnea-build/bzImage")
                urlretrieve(f"{url}modules.tar.xz", filename="/tmp/eupnea-build/modules.tar.xz")
    except URLError:
        print("\033[91m" + "Failed to reach github. Check your internet connection and try again" + "\033[0m")
        bash(f"kill {main_thread_pid}")


# Prepare USB, usb is not yet fully implemented
def prepare_usb(device) -> str:
    print("\033[96m" + "Preparing USB" + "\033[0m")

    # fix device name if needed
    if device.endswith("/") or device.endswith("1") or device.endswith("2"):
        mnt_point = device[:-1]
    # add /dev/ to device name, if needed
    if not device.startswith("/dev/"):
        device = f"/dev/{device}"

    # unmount all partitions
    bash(f"umount -lf {device}* 2>/dev/null")
    return partition(device, True)


# Create, mount, partition the img and flash the eupnea kernel
def prepare_img() -> str:
    print("\033[96m" + "Preparing img" + "\033[0m")

    print("Allocating space for image, might take a while")
    # try fallocate, if it fails use dd
    # TODO: determine img size
    img_size = 10  # 10 for now
    if not sp.run(f"fallocate -l {img_size}G eupnea.img", shell=True, capture_output=True).stderr.decode(
            "utf-8").strip() == "":
        bash("dd if=/dev/zero of=eupnea.img status=progress bs=12884 count=1000070")

    print("Mounting empty image")
    mnt_point = sp.run("losetup -f --show eupnea.img", shell=True, capture_output=True).stdout.decode("utf-8").strip()
    print("Image mounted at" + mnt_point)
    return partition(mnt_point, False)


def partition(mnt_point: str, write_usb: bool) -> str:
    # format as per depthcharge requirements,
    # READ: https://wiki.gentoo.org/wiki/Creating_bootable_media_for_depthcharge_based_devices
    print("Partitioning mounted image and adding flags")
    bash(f"parted -s {mnt_point} mklabel gpt")
    bash(f"parted -s -a optimal {mnt_point} unit mib mkpart Kernel 1 65")  # kernel partition
    bash(f"parted -s -a optimal {mnt_point} unit mib mkpart Root 65 100%")  # rootfs partition
    bash(f"cgpt add -i 1 -t kernel -S 1 -T 5 -P 15 {mnt_point}")  # depthcharge flags

    # get uuid of rootfs partition
    if write_usb:
        # if writing to usb, then no p in partition name
        rootfs_partuuid = sp.run(f"blkid -o value -s PARTUUID {mnt_point}2", shell=True,
                                 capture_output=True).stdout.decode("utf-8").strip()
    else:
        rootfs_partuuid = sp.run(f"blkid -o value -s PARTUUID {mnt_point}p2", shell=True,
                                 capture_output=True).stdout.decode("utf-8").strip()

    # read and modify kernel flags
    with open("configs/kernel.flags", "r") as file:
        temp = file.read().replace("${USB_ROOTFS}", rootfs_partuuid).strip()
    with open("kernel.flags", "w") as file:
        file.write(temp)

    print("Signing kernel")
    bash("futility vbutil_kernel --arch x86_64 --version 1 --keyblock /usr/share/vboot/devkeys/kernel.keyblock"
         + " --signprivate /usr/share/vboot/devkeys/kernel_data_key.vbprivk --bootloader kernel.flags" +
         " --config kernel.flags --vmlinuz /tmp/eupnea-build/bzImage --pack /tmp/eupnea-build/bzImage.signed")

    print("Flashing kernel")
    if write_usb:
        # if writing to usb, then no p in partition name
        bash(f"dd if=/tmp/eupnea-build/bzImage.signed of={mnt_point}1")
    else:
        bash(f"dd if=/tmp/eupnea-build/bzImage.signed of={mnt_point}p1")

    print("Formating rootfs")
    if write_usb:
        # if writing to usb, then no p in partition name
        bash(f"yes 2>/dev/null | mkfs.ext4 {mnt_point}2")  # 2>/dev/null is to supress yes broken pipe warning
    else:
        bash(f"yes 2>/dev/null | mkfs.ext4 {mnt_point}p2")  # 2>/dev/null is to supress yes broken pipe warning

    print("Mounting rootfs to /mnt/eupnea")
    if write_usb:
        # if writing to usb, then no p in partition name
        bash(f"mount {mnt_point}2 /mnt/eupnea")
    else:
        bash(f"mount {mnt_point}p2 /mnt/eupnea")
    return mnt_point  # return loop device, so it can be unmounted at the end


# download the distro rootfs
def download_rootfs(distro_name: str, distro_version: str, distro_link: str) -> None:
    print("\033[96m" + "Downloading rootfs." + "\033[0m")
    try:
        match distro_name:
            case "ubuntu":
                print(f"Downloading ubuntu rootfs {distro_version}")
                urlretrieve(
                    f"https://cloud-images.ubuntu.com/releases/{distro_version}/release/ubuntu-{distro_version}"
                    f"-server-cloudimg-amd64-root.tar.xz",
                    filename="/tmp/eupnea-build/rootfs/ubuntu-rootfs.tar.xz")
            case "debian":
                print("Downloading debian with debootstrap")
                # Debian sometimes fails for no apparent reason, so we try 2 times
                debian_result = sp.run("debootstrap stable /tmp/eupnea-build/rootfs https://deb.debian.org/debian/",
                                       shell=True, capture_output=True).stdout.decode("utf-8")
                print("Result: " + str(debian_result))  # print results for debugging
                if debian_result.__contains__("Couldn't download packages:"):
                    print("\033[91m\nDebootstrap failed, retrying once\n\033[0m")
                    # delete the failed rootfs
                    rmdir("/tmp/eupnea-build/rootfs", ignore_errors=True)
                    Path("/tmp/eupnea-build/rootfs").mkdir(parents=True)
                    debian_result = sp.run("debootstrap stable /tmp/eupnea-build/rootfs https://deb.debian.org/debian/",
                                           shell=True, capture_output=True).stdout.decode("utf-8")
                    print("Result: " + str(debian_result))  # print results for debugging
                    if debian_result.__contains__("Couldn't download packages:"):
                        print("\033[91m\nDebootstrap failed again, check your internet connection or try again later" +
                              "\033[0m")
                        bash(f"kill {main_thread_pid}")
            case "arch":
                print("Downloading latest arch rootfs")
                urlretrieve("https://mirror.rackspace.com/archlinux/iso/latest/archlinux-bootstrap-x86_64.tar.gz",
                            filename="/tmp/eupnea-build/rootfs/arch-rootfs.tar.gz")
            case "fedora":
                print(f"Downloading fedora rootfs version: {distro_version}")
                urlretrieve(distro_link, filename="/tmp/eupnea-build/rootfs/fedora-rootfs.tar.xz")
    except URLError:
        print(
            "\033[91m" + "Couldnt download rootfs. Check your internet connection and try again. If the error" +
            " persists, create an issue with the distro and version in the name" + "\033[0m")
        bash(f"kill {main_thread_pid}")  # kill main thread, as this function running in a different thread


# extract the rootfs to the img
def extract_rootfs(distro: str) -> None:
    print("\033[96m" + "Extracting rootfs" + "\033[0m")
    match distro:
        case "ubuntu":
            print("Extracting ubuntu rootfs")
            bash("tar xfp /tmp/eupnea-build/rootfs/ubuntu-rootfs.tar.xz -C " + "/mnt/eupnea --checkpoint=.10000")
        case "debian":
            print("Copying debian rootfs")
            bash("cp -r -p /tmp/eupnea-build/rootfs/* /mnt/eupnea/")
        case "arch":
            print("Extracting arch rootfs")
            # TODO: Figure out how to extract arch rootfs without cd
            # temp chdir into mounted image, then go back to original dir
            temp_path = os.getcwd()
            os.chdir("/mnt/eupnea")
            bash("tar xpfz /tmp/eupnea-build/rootfs/arch-rootfs.tar.gz root.x86_64/ --strip-components=1" +
                 " --numeric-owner --checkpoint=.10000")
            os.chdir(temp_path)
        case "fedora":
            print("Extracting fedora rootfs")
            # extract to temp location, find rootfs and extract it to mounted image
            Path("/tmp/eupnea-build/fedora-temp").mkdir()
            bash(
                "tar xfp /tmp/eupnea-build/rootfs/fedora-rootfs.tar.xz -C /tmp/eupnea-build/fedora-temp " +
                "--checkpoint=.10000")
            with os.scandir("/tmp/eupnea-build/fedora-temp") as scan:
                for entry in scan:
                    if entry.is_dir():
                        temp_rootfs_path = entry.path
                        break
            print("\nCopying fedora rootfs to /mnt/eupnea")
            bash(f"tar xpf {temp_rootfs_path}/layer.tar -C /mnt/eupnea --checkpoint=.10000")


# Configure distro agnostic options
def post_extract(username: str, password: str, hostname: str, distro: str, de_name: str) -> None:
    print("\n\033[96m" + "Configuring Eupnea" + "\033[0m")

    print("Copying resolv.conf")
    bash("cp --remove-destination /etc/resolv.conf /mnt/eupnea/etc/resolv.conf")

    print("Extracting kernel modules")
    rmdir("/mnt/eupnea/lib/modules", ignore_errors=True)
    Path("/mnt/eupnea/lib/modules").mkdir(parents=True, exist_ok=True)
    # modules tar contains /lib/modules, so it's extracted to / and --skip-old-files is used to prevent overwriting
    # other files in /lib
    bash("tar xpf /tmp/eupnea-build/modules.tar.xz --skip-old-files -C /mnt/eupnea/ --checkpoint=.10000")
    print("")  # break line after tar

    if not (distro == "ubuntu" and de_name == "gnome"):  # Ubuntu + gnome has first time setup
        print("Configuring user")
        chroot(f"useradd --create-home {username}")
        chroot(f'echo "{username}:{password}" | chpasswd')
        match distro:
            case "ubuntu" | "debian":
                chroot(f"usermod -aG sudo {username}")
            case "arch" | "fedora":
                chroot(f"usermod -aG wheel {username}")

    print("Extracting kernel headers")
    # TODO: extract kernel headers

    print("Setting hostname")
    with open("/mnt/eupnea/etc/hostname", "w") as hostname_file:
        hostname_file.write(hostname)

    print("Copying eupnea utils")
    bash("cp postinstall-scripts/* /mnt/eupnea/usr/local/bin/")
    cpdir("configs", "/mnt/eupnea/usr/local/eupnea-configs")

    print("Configuring sleep")
    # disable hibernation aka S4 sleep, READ: https://eupnea-linux.github.io/docs.html#/bootlock
    # TODO: Fix sleep
    Path("/mnt/eupnea/etc/systemd/").mkdir(exist_ok=True)  # just in case systemd path doesn't exist
    with open("/mnt/eupnea/etc/systemd/sleep.conf", "a") as file:
        file.write("SuspendState=freeze\nHibernateState=freeze")

    print("Adding kernel modules")
    # open kernel-modules.txt and then append its contents to the Eupnea file
    with open("configs/kernel-modules.txt", "r") as repo_file:
        with open("/mnt/eupnea/etc/modules", "a") as file:
            file.write(repo_file.read())

    # disable ssh service, as it fails to start
    # TODO: Fix ssh
    chroot("systemctl disable ssh.service")


# chroot command
def chroot(command: str) -> str:
    output = sp.run(f'chroot /mnt/eupnea /bin/sh -c "{command}"', shell=True, capture_output=True).stdout.decode(
        "utf-8").strip()
    if args.verbose:
        print(output)
    return output


if __name__ == "__main__":
    # Elevate script to root
    if os.geteuid() != 0:
        args = ['sudo', sys.executable] + sys.argv + [os.environ]
        os.execlpe('sudo', *args)
    args = process_args()
    main_thread_pid = os.getpid()  # for threads to kill mainthread

    if args.dev_build:
        print("\033[93m" + "Using dev release" + "\033[0m")
    if args.alt:
        print("\033[93m" + "Using alt kernel" + "\033[0m")
    if args.exp:
        print("\033[93m" + "Using experimental kernel" + "\033[0m")
    if args.mainline:
        print("\033[93m" + "Using mainline kernel" + "\033[0m")
    if args.local_path:
        print("\033[93m" + "Using local path" + "\033[0m")
    if args.verbose:
        print("\033[93m" + "Verbosity increased" + "\033[0m")

    user_input = user_input.user_input()  # get user input
    prepare_host(user_input[0])

    if args.local_path is None:
        # Print download progress in terminal
        t = Thread(target=download_kernel, daemon=True)
        t.start()
        sleep(1)  # wait for thread to print info
        while t.is_alive():
            sys.stdout.flush()
            print(".", end="")
            sleep(1)
        print("")  # break line
    else:  # if local path is specified, copy kernel from there
        if not args.local_path.endswith("/"):
            kernel_path = f"{args.local_path}/"
        else:
            kernel_path = args.local_path
        print("\033[96m" + "Using local kernel files" + "\033[0m")
        cp(f"{kernel_path}bzImage", "/tmp/eupnea-build/bzImage")
        cp(f"{kernel_path}modules.tar.xz", "/tmp/eupnea-build/modules.tar.xz")

    if user_input[9]:
        img_mnt = prepare_img()
    else:
        img_mnt = prepare_usb(user_input[4])

    # Print download progress in terminal
    t = Thread(target=download_rootfs, args=(user_input[0], user_input[1], user_input[2],), daemon=True)
    t.start()
    sleep(1)  # wait for thread to print info
    while t.is_alive():
        sys.stdout.flush()
        print(".", end="")
        sleep(1)
    print("")  # break line

    extract_rootfs(user_input[0])
    post_extract(user_input[5], user_input[6], user_input[7], user_input[0], user_input[3])

    match user_input[0]:
        case "ubuntu":
            import distro.ubuntu as distro
        case "debian":
            import distro.debian as distro
        case "arch":
            import distro.arch as distro
        case "fedora":
            import distro.fedora as distro
        case _:
            print("\033[91m" + "Something went **really** wrong!!! (Distro name not found)" + "\033[0m")
            exit(1)
    distro.config(user_input[3], user_input[1], args.verbose)

    # Add chromebook layout. Needs to be done after install Xorg/Wayland
    print("Backing up default keymap and setting Chromebook layout")
    cp("/mnt/eupnea/usr/share/X11/xkb/symbols/pc", "/mnt/eupnea/usr/share/X11/xkb/symbols/pc.default")
    cp("configs/xkb/xkb.chromebook", "/mnt/eupnea/usr/share/X11/xkb/symbols/pc")
    if user_input[8]:  # rebind search key to caps lock
        print("Rebinding search key to Caps Lock")
        cp("/mnt/eupnea/usr/share/X11/xkb/keycodes/evdev", "/mnt/eupnea/usr/share/X11/xkb/keycodes/evdev.default")

    # Hook postinstall script, needs to be done after preping system
    print("Adding postinstall service")
    bash("cp configs/postinstall.service /mnt/eupnea/etc/systemd/system/postinstall.service")
    chroot("systemctl enable postinstall.service")

    # Unmount everything
    print("\033[96m" + "Finishing setup" + "\033[0m")
    print("Unmounting rootfs")
    bash("umount -f /mnt/eupnea")
    if user_input[9]:
        print("Unmounting img")
        bash(f"losetup -d {img_mnt}")
        print("\033[95m" + f"The ready Eupnea image is located at {str(os.getcwd())}/eupnea.img" + "\033[0m")
    else:
        print("\033[95m" + "The USB is ready to boot Eupnea. " + "\033[0m")

#!/usr/bin/env python3
# This script will later become the gui. For now, it's a simple wrapper for the build script.

import argparse
import atexit
import os
import sys

from functions import *

global user_cancelled
user_cancelled = False


# parse arguments from the cli. Only for testing/advanced use. All other parameters are handled by cli_input.py
def process_args():
    parser = argparse.ArgumentParser()
    # action="store_true" makes the arg a flag and sets it to True if the argument is passed, without needing a value
    parser.add_argument('-p', dest="local_path",
                        help="Use files from provided path before downloading from the internet")
    parser.add_argument('--device', dest="device_override",
                        help="Specify device to direct write. Skips the device selection question.")
    parser.add_argument("--show-device-selection", dest="device_selection", action="store_true",
                        help="Show device selection menu instead of automatically building image")
    parser.add_argument("-v", "--verbose", dest="verbose", help="Print more output", action="store_true")
    parser.add_argument("--no-download-progress", dest="download_progress", action="store_true",
                        help="Do not print download/extraction progress")
    parser.add_argument("--no-shrink", dest="no_shrink", help="Do not shrink image", action="store_true")
    parser.add_argument("--verbose-kernel", dest="verbose_kernel", action="store_true",
                        help="Set loglevel=15 in cmdline for visible kernel logs on boot")
    parser.add_argument("--skip-size-check", dest="skip_size_check", action="store_true",
                        help="Do not check available disk space")
    parser.add_argument("-i", dest="image_size", type=int, nargs=1, default=[10],
                        help="Override image size(default: 10GB)")
    parser.add_argument("--dev", dest="dev_build", action="store_true", help="Use latest dev build. May be unstable.")
    parser.add_argument("--skip-commit-check", dest="skip_commit_check", action="store_true",
                        help="Do not check if local commit hash matches remote commit hash")
    return parser.parse_args()


class ExitHooks(object):
    def __init__(self):
        self.exit_code = None

    def hook(self):
        self._orig_exit = sys.exit
        self._orig_excepthook = sys.excepthook
        self._orig_exc_handler = self.exc_handler
        sys.exit = self.exit
        sys.excepthook = self.exc_handler

    def exit(self, code=0):
        self.exit_code = code
        self._orig_exit(code)

    def exc_handler(self, exc_type, exc, *args):
        if exc_type == KeyboardInterrupt:
            global user_cancelled
            user_cancelled = True
        else:
            sys.__excepthook__(exc_type, exc, *args)


def exit_handler():
    if user_cancelled:
        print_error("\nUser cancelled, exiting")
        return
    if hooks.exit_code not in [0, 1]:  # ignore normal exit codes
        print_error("Script exited unexpectedly, please open an issue on GitHub/Discord/Revolt")
        print_question('Run "./main.py -v" to restart with more verbose output\n'
                       'Run "./main.py --help" for more options')


if __name__ == "__main__":
    # override sys.exit to catch exit codes
    hooks = ExitHooks()
    hooks.hook()
    atexit.register(exit_handler)
    # set locale env var to supress locale warnings in package managers
    os.environ["LC_ALL"] = "C"

    args = process_args()
    if args.dev_build:
        print_error("Dev builds are not supported currently")
        sys.exit(1)

    # Restart script as root
    if os.geteuid() != 0:
        print_header("The script requires root privileges to mount the image/device and write to it, "
                     "as well as for installing dependencies on the build system")
        print_status("Requesting root privileges...")
        sudo_args = ['sudo', sys.executable] + sys.argv + [os.environ]
        os.execlpe('sudo', *sudo_args)

    # PATH vars are inherited in chroots -> check if the current path has /usr/sbin, as some systems dont have that var
    # but some chroot distros expect them to be set
    if not os.environ.get("PATH").__contains__("/usr/sbin"):
        os.environ["PATH"] += ":/usr/sbin"

    # check script dependencies are already installed with which
    try:
        bash("which pv xz parted cgpt futility")
        print_status("Dependencies already installed, skipping")
    except subprocess.CalledProcessError:
        print_status("Installing dependencies")
        with open("/etc/os-release", "r") as os:
            distro = os.read()
        if distro.lower().__contains__(
                "arch"):  # might accidentally catch architecture stuff, but needed to catch arch derivatives
            bash("pacman -Sy")  # sync repos
            # Download prepackaged cgpt + vboot from arch-repo releases as its not available in the official repos
            # Makepkg is too much of a hassle to use here as it requires a non-root user
            urlretrieve("https://github.com/eupnea-linux/arch-repo/releases/latest/download/cgpt-vboot"
                        "-utils.pkg.tar.gz", filename="/tmp/cgpt-vboot-utils.pkg.tar.gz")
            # Install downloaded package
            bash("pacman --noconfirm -U /tmp/cgpt-vboot-utils.pkg.tar.gz")
            # Install other dependencies
            bash("pacman --noconfirm -S pv xz parted")
        elif distro.lower().__contains__("void"):
            bash("xbps-install -y --sync")
            bash("xbps-install -y pv xz parted cgpt vboot-utils")
        elif distro.lower().__contains__("ubuntu") or distro.lower().__contains__("debian"):
            bash("apt-get update -y")  # sync repos
            bash("apt-get install -y pv xz-utils parted cgpt vboot-kernel-utils")
        elif distro.lower().__contains__("suse"):
            bash("zypper --non-interactive refresh")  # sync repos
            bash("zypper --non-interactive install vboot parted pv xz")  # cgpt is included in vboot-utils on fedora
        elif distro.lower().__contains__("fedora"):
            bash("dnf update -y")  # sync repos
            bash("dnf install -y vboot-utils parted pv xz")  # cgpt is included in vboot-utils on fedora
        else:
            print_warning("Script dependencies not found, please install the following packages with your package "
                          "manager: which pv xz parted cgpt futility")
            sys.exit(1)

    # Check python version
    if sys.version_info < (3, 10):  # python 3.10 or higher is required
        # Check if running under crostini and ask user to update python
        # Do not give this option on regular systems, as it may break the system
        try:
            with open("/sys/devices/virtual/dmi/id/product_name", "r") as file:
                product_name = file.read().strip()
        except FileNotFoundError:
            product_name = ""
        if product_name == "crosvm" and path_exists("/usr/bin/apt"):
            user_answer = input("\033[92m" + "Python 3.10 or higher is required. Attempt to install? (Y/n)\n" +
                                "\033[0m").lower()
            if user_answer in ["y", ""]:
                print_status("Switching to unstable channel")
                # switch to unstable channel
                with open("/etc/apt/sources.list", "r") as file:
                    original_sources = file.readlines()
                sources = original_sources
                sources[1] = sources[1].replace("bullseye", "unstable")
                sources[1] = sources[1].replace("buster", "unstable")  # Some crostinis are on buster
                with open("/etc/apt/sources.list", "w") as file:
                    file.writelines(sources)

                # update and install python
                print_status("Installing python 3.10")
                bash("apt-get update -y")
                bash("apt-get install -y python3")
                print_status("Python 3.10 installed")

                # revert to stable channel
                with open("/etc/apt/sources.list", "w") as file:
                    file.writelines(original_sources)

                bash("apt-get update -y")  # update cache back to stable channel

                print_header('Please restart the script with: "./main.py"')
                sys.exit(0)
        print_error("Please run the script with python 3.10 or higher")
        sys.exit(1)
    # import other scripts after python version check is successful
    import build
    import cli_input

    # check if running the latest version fo the script
    if not args.skip_commit_check and bash("git rev-parse HEAD") != bash("git ls-remote origin HEAD").split("\t")[0]:
        print_error("You are not running the latest version of the script. Please update with 'git pull'")
        print_status("If you are a developer, you can skip this with the '--skip-commit-check' flag")
        sys.exit(1)

    # Check if running under crostini
    try:
        with open("/sys/devices/virtual/dmi/id/product_name", "r") as file:
            product_name = file.read().strip()
    except FileNotFoundError:
        product_name = ""  # WSL has no dmi data
    if product_name == "crosvm":
        print_warning("Crostini detected. Preparing Crostini")
        # TODO: Translate to python
        try:
            bash("bash configs/crostini/setup-crostini.sh")
        except subprocess.CalledProcessError:
            print_error("Failed to prepare Crostini")
            print_error("Please run the Crostini specific instructions before running this script")
            print("https://eupnea-linux.github.io/docs/extra/crostini")
            sys.exit(1)

    # clear terminal, but keep any previous output so the user can scroll up to see it
    print("\033[H\033[2J", end="")

    # parse arguments
    if args.dev_build:
        print_warning("Using dev release")
    if args.local_path:
        print_warning("Using local files")
    if args.verbose:
        print_warning("Verbosity increased")
    if args.no_shrink:
        print_warning("Image will not be shrunk")
    if args.image_size[0] != 10:
        print_warning(f"Image size overridden to {args.image_size[0]}GB")

    # override device if specified
    if not args.device_selection:
        user_input = cli_input.get_user_input(skip_device=True)  # get user input
        user_input["device"] = "image"
        if args.device_override is not None:
            user_input["device"] = args.device_override  # override device
    else:
        user_input = cli_input.get_user_input()  # get normal user input

    # Clean system from previous depthboot builds
    print_status("Removing old depthboot build files")
    rmdir("/tmp/depthboot-build")
    mkdir("/tmp/depthboot-build", create_parents=True)

    print_status("Unmounting old depthboot mounts if present")
    try:
        bash("umount -lf /mnt/depthboot")  # just in case
        sleep(5)  # wait for umount to finish
        bash("umount -lf /mnt/depthboot")  # umount a second time, coz first time might not work
    except subprocess.CalledProcessError:
        print("Failed to unmount /mnt/depthboot, ignore")
    rmdir("/mnt/depthboot")
    mkdir("/mnt/depthboot", create_parents=True)

    rmfile("depthboot.img")
    rmfile("kernel.flags")
    rmfile(".stop_download_progress")

    # Check if there is enough space in /tmp
    avail_space = int(bash("BLOCK_SIZE=m df --output=avail /tmp").split("\n")[1][:-1])  # read tmp size in MB
    restore_tmp = False

    if user_input["device"] == "image" and avail_space < 13000 and not args.skip_size_check:
        print_warning("Not enough space in /tmp to build image. At least 13GB is required")
        # check if /tmp is a tmpfs mount
        if bash("df --output=fstype /tmp").__contains__("tmpfs"):
            user_answer = input("\033[92m" + "Remount /tmp to increase its size? (Y/n)\n" + "\033[0m").lower()
            if user_answer in ["y", ""]:
                print_status("Increasing size of /tmp")
                bash("mount -o remount,size=13G /tmp")
                print_status("Size of /tmp increased")
                restore_tmp = True
            else:
                print_error("Please free up space in /tmp")
                print("Use --skip-size-check to ignore this check")
                sys.exit(1)
        else:
            print_error("Allocate more storage to the container/vm if possible or clear /tmp or use another system")
            print("Use --skip-size-check to ignore this check")
            sys.exit(1)

    build.start_build(build_options=user_input, args=args)
    if restore_tmp:  # restore /tmp size if it was changed
        print_status("Restoring size of /tmp")
        bash(f"mount -o remount,size={avail_space}M /tmp")
    sys.exit(0)

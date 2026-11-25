#!/bin/sh
# Sets up the native Linux build (Vulkan, no Wine) from your disc image:
#
#   linux/setup.sh                         the installer window (linux/native/install.sh): build tools, every
#                                          build step, the menu entries
#   linux/setup.sh image.iso [--key HEX | --key file.dkey] [--jobs N]
#                                          the same build steps in this terminal (linux/native/build.sh with
#                                          DES_ISO / DES_KEY / JOBS); the build tools must be installed already
#
# The Linux kits each have their own folder, so choose:
#   linux/native/   the native build (recommended)                         (docs/LINUX_NATIVE.md)
#   linux/proton/   the Windows build through Proton: setup.sh, the AppImage (docs/LINUX_PROTON.md)
#
# Until both had their own folder this file was the Proton kit's setup: that is linux/proton/setup.sh now
# (its --rpcs3 option has no use here: the native build decrypts the EBOOT itself).
D=$(cd "$(dirname "$0")/.." && pwd)
[ $# -eq 0 ] && exec bash "$D/linux/native/install.sh"
while [ $# -gt 0 ]; do
  case "$1" in
    --key) DES_KEY=${2:-}; shift 2 || shift ;;
    --jobs) JOBS=${2:-}; shift 2 || shift ;;
    --rpcs3) echo "(--rpcs3 is for the Proton kit, linux/proton/setup.sh: the native build needs no RPCS3)"; shift 2 || shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    -*) echo "unknown option $1 (see linux/setup.sh --help)"; exit 2 ;;
    *) DES_ISO=$1; shift ;;
  esac
done
export DES_ISO DES_KEY JOBS
exec bash "$D/linux/native/build.sh"

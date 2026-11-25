#!/bin/sh
# Plays the native Linux build: linux/native/play.sh (Vulkan, no Wine), with the same arguments and settings.
#
# The Linux kits each have their own folder, so choose:
#   linux/native/   the native build (recommended): install.sh, launcher.py, play.sh   (docs/LINUX_NATIVE.md)
#   linux/proton/   the Windows build through Proton: setup.sh, play.sh, the AppImage (docs/LINUX_PROTON.md)
#
# Until both had their own folder this file was the Proton kit's play script. A copy that has only the
# Proton build (port/build_opt, no port/build_linux) gets told where it went instead of a native start.
D=$(cd "$(dirname "$0")/.." && pwd)
if [ ! -x "$D/port/build_linux/DemonsSoulsRecomp" ] && [ -f "$D/port/build_opt/DemonsSoulsRecomp.exe" ]; then
  echo "linux/play.sh now plays the native Linux build, and this folder has only the Proton (Windows) build."
  echo "  Proton:  sh linux/proton/play.sh   (update a Steam shortcut to that path)"
  echo "  Native:  linux/native/install.sh   (recommended: no Wine, docs/LINUX_NATIVE.md)"
  exit 1
fi
exec bash "$D/linux/native/play.sh" "$@"

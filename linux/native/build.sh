#!/bin/bash
# Build the native Linux port (Vulkan, no Wine) from your own disc, step by step (docs/LINUX_NATIVE.md):
#
#   linux/native/build.sh [step ...]
#
# Steps, in order (default: all of them): deps venv game decrypt toolkit lift build
#
#   deps     checks the build tools: clang, lld, cmake, ninja, SDL2, glslang / SPIRV-Tools, the Vulkan
#            headers (FFmpeg for the movies: only a warning without it)
#   venv     .venv from Python 3.10+ with setup/requirements.txt and pycryptodome (kept once complete)
#   game     your disc image (DES_ISO; DES_KEY for a Redump image) -> game/ (setup/steps.py game).
#            Skipped once game/ is extracted, so the image is needed only then
#   decrypt  EBOOT.BIN -> EBOOT.elf without RPCS3 (tools/fetch_keys.py + tools/decrypt_self.py, as on the
#            Mac), checked against setup/setup.py's EBOOT_ELF_SHA256 (DES_ANY_EBOOT=1: a warning only)
#   toolkit  deps/ps3recomp at the tested commit with patches/ps3recomp-local.patch
#   lift     the recompiled PPU and SPU code (port/recompiled, port/spu) and patches/lifted-code.patch
#   build    the patches a source update changed (setup/steps.py sync), then cmake (Ninja, clang, Vulkan)
#            + ninja -> port/build_linux/DemonsSoulsRecomp; incremental
#
# Each step prints "==> <step>" first (the launcher's progress reads them) and "==> done" at the end.
# Everything it writes (game/, port/, deps/, .venv/) is game-derived or fetched, and .gitignore'd.
# The steps themselves are setup/native_build.py's, shared with mac/rebuild.sh; this picks the Python.
#
# Environment:
#   DES_ISO, DES_KEY  your disc image and (Redump) disc key, as for mac/rebuild.sh
#   DES_KEYS          the key file decrypt writes (default ~/.local/share/demons-souls-recomp/keys/self_keys.txt)
#   DES_BUILD         the build folder (default port/build_linux)
#   DES_LTO=1         ThinLTO across the whole program (Steam Deck, Boletaria: +3.5% on top of PGO; a longer
#                     link). Changing it reconfigures the build folder, which then rebuilds everything
#   DES_PGO=<file>    an llvm-profdata profile to optimize with (-fprofile-use); see docs/LINUX_NATIVE.md.
#                     Default: port/pgo/des.profdata when it exists (DES_PGO=0: none)
#   DES_MARCH=0       build for plain x86-64 + AVX2 instead of x86-64-v3 (used when the CPU has all of v3)
#   DES_DISTROBOX=<name>  the distrobox container the build step compiles in. On SteamOS (no compiler on the
#                     host, and an older glibc than a current toolchain builds for) the default is "des-build"
#                     when that container exists; the game then also runs inside it (linux/native/play.sh)
#   JOBS              parallel compile jobs (ninja's -j; default: ninja's own)
#   PY                the Python 3.10+ the venv is made from (default: the newest python3.1x on PATH)
set -eo pipefail
here=$(cd "$(dirname "$0")" && pwd)
DES_ROOT=$(cd "$here/../.." && pwd)
if [ -z "${PY:-}" ]; then
  for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    command -v "$c" >/dev/null && { PY=$(command -v "$c"); break; }
  done
fi
[ -n "${PY:-}" ] || { echo "==> deps (the build tools)"; echo "ERROR: missing: python3 (3.10 or newer)"; exit 1; }
exec "$PY" -u "$DES_ROOT/setup/native_build.py" --profile linux "$@"

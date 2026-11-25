#!/bin/zsh
# Build the native macOS port from your own disc, step by step (docs/MACOS.md has the same commands):
#
#   mac/rebuild.sh [step ...]
#
# Steps, in order (default: all of them): deps venv game decrypt toolkit lift build
#
#   deps     checks the build tools: Xcode's command-line tools, and Homebrew's cmake, ninja, sdl2,
#            glslang and spirv-cross (ffmpeg for the movies: only a warning without it)
#   venv     .venv from Python 3.10+ with setup/requirements.txt and pycryptodome (kept once complete)
#   game     your disc image (DES_ISO; DES_KEY for a Redump image) -> game/: tools/iso_extract.py
#            --all, tools/udf_rename.py, game/.extracted. Skipped once game/ is extracted, so the
#            image is needed only then
#   decrypt  game/PS3_GAME/USRDIR/EBOOT.BIN -> EBOOT.elf without RPCS3: tools/fetch_keys.py writes the
#            key file once (RPCS3's public key table, downloaded and checked by its SHA-256) and
#            tools/decrypt_self.py decrypts. Skipped while EBOOT.elf is there; either way the ELF is
#            checked against setup/setup.py's EBOOT_ELF_SHA256 (DES_ANY_EBOOT=1: a warning only)
#   toolkit  setup.step_ps3recomp(): deps/ps3recomp at the tested commit with patches/ps3recomp-local.patch
#   lift     setup.step_lift(): the recompiled PPU and SPU code (port/recompiled, port/spu) and
#            patches/lifted-code.patch. Both skip what is current, as SETUP.bat does
#   build    the patches a source update changed (setup/steps.py sync), then cmake (Ninja,
#            -DPS3RECOMP_FFMPEG_RUNTIME=ON) + ninja -> port/build_mac/DemonsSoulsRecomp and
#            DemonsSoulsRecomp_window (the window mac/play.sh runs); incremental
#
# Each step prints "==> <step>" first (the launcher's progress bar reads them) and "==> done" at the end.
# Everything it writes (game/, port/, deps/, .venv/) is game-derived or fetched, and .gitignore'd.
# Environment: mac/env.sh (DES_ISO, DES_KEY, DES_KEYS, JOBS, PY).
# The steps themselves are setup/native_build.py's, shared with linux/native/build.sh; this picks the
# Python and puts Homebrew's tools first on PATH (mac/env.sh).
set -e
here=${0:A:h}
source "$here/env.sh"
[ -n "$PY" ] || PY=$(command -v python3) ||
    { print -r -- "==> deps (the build tools)"; print -r -- "ERROR: missing: python@3.12. Install it with: brew install python@3.12"; exit 1; }
export DES_ROOT DES_VENV DES_BUILD DES_KEYS DES_BREW
exec "$PY" -u "$DES_ROOT/setup/native_build.py" --profile mac "$@"

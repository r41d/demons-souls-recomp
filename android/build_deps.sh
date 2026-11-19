#!/bin/bash
# Builds SDL2 2.30.12 and glslang 15.4.0 for arm64-v8a (Android API 29) into one prefix, for
# android/build_so.sh. glslang without SPIRV-Tools (ENABLE_OPT=OFF): the Vulkan backend's shaders do not
# need its optimizer (rsx_shader_spirv.cpp). Sources are downloaded once into ~/Android/src
# (macOS: ~/Library/Android/src). Runs on Linux, macOS and Windows (Git Bash): android/env.sh.
#
#   android/build_deps.sh
#
# Environment: android/env.sh (ANDROID_HOME, ANDROID_NDK, DES_ANDROID_PREFIX, DES_ANDROID_SRC).
set -eo pipefail
here=$(cd "$(dirname "$0")" && pwd)
. "$here/env.sh"
SRC=$SRC_DIR
SDL_V=2.30.12
GLSLANG_V=15.4.0
[ -f "$NDK/build/cmake/android.toolchain.cmake" ] || { echo "ERROR: no NDK at '$NDK' (ANDROID_NDK)" >&2; exit 1; }
mkdir -p "$SRC"
cd "$SRC"
[ -d SDL2-$SDL_V ] || curl -fsSL https://github.com/libsdl-org/SDL/releases/download/release-$SDL_V/SDL2-$SDL_V.tar.gz | tar xz
[ -d glslang-$GLSLANG_V ] || curl -fsSL https://github.com/KhronosGroup/glslang/archive/refs/tags/$GLSLANG_V.tar.gz | tar xz
X=(-G Ninja -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" -DANDROID_ABI=arm64-v8a
   -DANDROID_PLATFORM=29 -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PREFIX")
cmake -S SDL2-$SDL_V -B build-sdl "${X[@]}" -DSDL_SHARED=ON -DSDL_STATIC=OFF -DSDL_TEST=OFF
ninja -C build-sdl install
cmake -S glslang-$GLSLANG_V -B build-glslang "${X[@]}" -DENABLE_OPT=OFF -DGLSLANG_TESTS=OFF \
      -DENABLE_GLSLANG_BINARIES=OFF -DBUILD_SHARED_LIBS=OFF -DCMAKE_POSITION_INDEPENDENT_CODE=ON
ninja -C build-glslang install
echo "SDL2 $SDL_V + glslang $GLSLANG_V for arm64-v8a in $PREFIX"
# The Java side of SDL (org.libsdl.app) in android/app/src/main/java is SDL2-$SDL_V/android-project's, unchanged.

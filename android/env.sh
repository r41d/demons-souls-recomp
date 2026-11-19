# Shared settings for android/build_deps.sh, build_so.sh, build_apk.sh and push_game.sh. Sourced, not run
# (bash 3.2 or newer: macOS's /bin/bash is 3.2). The APK is built on Linux, macOS or Windows (Git Bash /
# MSYS2); only where the Android SDK lives and the name of the NDK's host tools differ.
#
#   ANDROID_HOME         the Android SDK. Default: ~/Android/sdk on Linux, ~/Library/Android/sdk on macOS
#                        (Android Studio's own place there), %LOCALAPPDATA%\Android\Sdk on Windows
#   ANDROID_NDK          the NDK (default: the newest under $ANDROID_HOME/ndk)
#   DES_ANDROID_PREFIX   SDL2 + glslang for arm64 (default: prefix-arm64 beside the SDK folder)
#   DES_ANDROID_SRC      their sources (default: src beside the SDK folder)
#   JAVA_HOME            a JDK 17 for Gradle. macOS, when unset: Homebrew's openjdk@17, else the one
#                        /usr/libexec/java_home -v 17 finds (macOS's own /usr/bin/java is only a stub)
case "$(uname -s)" in
  Darwin)               DES_HOST_OS=darwin;  DES_NDK_HOST=darwin-x86_64;  DES_EXE=;     _sdk=$HOME/Library/Android/sdk ;;
  MINGW*|MSYS*|CYGWIN*) DES_HOST_OS=windows; DES_NDK_HOST=windows-x86_64; DES_EXE=.exe
                        _sdk=$(cygpath -u "${LOCALAPPDATA:-$USERPROFILE/AppData/Local}")/Android/Sdk ;;
  *)                    DES_HOST_OS=linux;   DES_NDK_HOST=linux-x86_64;   DES_EXE=;     _sdk=$HOME/Android/sdk ;;
esac
export ANDROID_HOME=${ANDROID_HOME:-$_sdk}
# The newest NDK by version number. Not "sort -V": macOS before 13 has no -V.
des_newest() { ls "$1" 2>/dev/null | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1; }   # names like 28.2.13676358
NDK=${ANDROID_NDK:-$ANDROID_HOME/ndk/$(des_newest "$ANDROID_HOME/ndk")}
_base=$(dirname "$ANDROID_HOME")
PREFIX=${DES_ANDROID_PREFIX:-$_base/prefix-arm64}
SRC_DIR=${DES_ANDROID_SRC:-$_base/src}
NDK_BIN=$NDK/toolchains/llvm/prebuilt/$DES_NDK_HOST/bin
if [ "$DES_HOST_OS" = darwin ] && [ -z "${JAVA_HOME:-}" ]; then
  for _j in /opt/homebrew/opt/openjdk@17 /usr/local/opt/openjdk@17; do
    [ -x "$_j/bin/java" ] && { export JAVA_HOME=$_j; break; }
  done
  [ -n "${JAVA_HOME:-}" ] || JAVA_HOME=$(/usr/libexec/java_home -v 17 2>/dev/null) || true
  [ -n "${JAVA_HOME:-}" ] && export JAVA_HOME
fi
# How the lifted code is made on this system (for the messages).
case $DES_HOST_OS in
  darwin)  DES_LIFT_HINT="mac/rebuild.sh venv game decrypt toolkit lift" ;;
  windows) DES_LIFT_HINT="SETUP.bat" ;;
  *)       DES_LIFT_HINT="linux/native/build.sh venv game decrypt toolkit lift" ;;
esac

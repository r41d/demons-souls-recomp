#!/bin/zsh
# DEV TEST for co-op step 1 on macOS (docs/COOP_DEV.md), the counterpart of run_coop_test.ps1: starts our local
# test server (tools/coop/mini_psnr.py) and one or two sandboxed copies of the game, each with its own save
# folders under <work> (never the player's gamedata), and stops the copies (by their own PIDs) after <seconds>.
#
#   tools/coop/run_coop_test.sh <work folder> [both|host|guest] [seconds] [a gamedata folder to copy into each sandbox]
#
# Host = copy A (DES_COOP=host, NP name Host1, P2P port 3658); guest = copy B (DES_COOP=guest, Guest1, 3659).
# Both use DES_COOP_AUTO=1 and a shared mailbox folder. Logs: <work>/A/logs, <work>/B/logs, <work>/psnr.log.
# To play it yourself: seconds = 0 runs until you close both windows, and COOP_PLAY=1 gives the host the
# controller and the guest the keyboard and mouse (click a window to give it the keyboard), with sound on the
# host and god mode off. Each copy still starts by loading the first save slot of its own copy of the save.
# Extra environment (COOP_PAD_SCRIPT, COOP_PAD_SCRIPT_B, DES_WARP_AT, ...) is passed through to the copies;
# COOP_FRAME_DUMP=1 keeps each copy's latest picture in <work>/<A|B>/frame.ppm.
set -u
ROOT=${0:A:h:h:h}
WORK=${1:?usage: run_coop_test.sh <work folder> [both|host|guest] [seconds] [gamedata folder]}
WHO=${2:-both}; SECS=${3:-180}; SAVE=${4:-}
BIN=$ROOT/port/build_mac/DemonsSoulsRecomp
WIN=$ROOT/port/build_mac/DemonsSoulsRecomp_window
# COOP_GAME=game_coop runs the copy tools/make_coop.py builds (the co-op script module); the default is game/.
ELF=$ROOT/${COOP_GAME:-game}/PS3_GAME/USRDIR/EBOOT.elf
# COOP_ENV_A / COOP_ENV_B="NAME=value NAME=value" are settings for the host copy / the guest copy only.
# COOP_STOP_HOST_AT=<seconds> stops the host copy then (the guest's way home), with a fixed run length.
# COOP_TEST_KILL=<character id>:<seconds> builds it with make_coop.py's --test-kill (Phalanx is 800).
if [ "${COOP_GAME:-}" = game_coop ]; then
  "$ROOT/.venv/bin/python" "$ROOT/tools/make_coop.py" ${COOP_TEST_KILL:+--test-kill} ${COOP_TEST_KILL:-} || exit 1
fi
[ -x "$BIN" ] && [ -x "$WIN" ] && [ -f "$ELF" ] || { echo "build or game files missing under $ROOT"; exit 1; }
WORK=${WORK:A}
case "$WORK" in "$ROOT"/gamedata*|"$ROOT"/bossrush*|"$ROOT"/hdd*) echo "pick a work folder away from the game's own saves"; exit 1 ;; esac
if pgrep -f "$ROOT/port/.*/DemonsSoulsRecomp " >/dev/null; then echo "a game is already running: not starting a test"; exit 1; fi
mkdir -p "$WORK/mailbox"; rm -f "$WORK"/mailbox/*(N)
PY=$ROOT/.venv/bin/python; [ -x "$PY" ] || PY=python3
"$PY" -I "$ROOT/tools/coop/mini_psnr.py" "$WORK/psnr.log" >"$WORK/psnr_stdout.txt" 2>"$WORK/psnr_stderr.txt" &
SRV=$!
sleep 2
echo "test server pid $SRV"
PAD_A=${COOP_PAD_SCRIPT:-22:0x0008,26:0x0008,30:0x4000,33:0x4000,36:0x4000,40:0x4000,44:0x4000,48:0x4000}
PAD_B=${COOP_PAD_SCRIPT_B:-$PAD_A}
PIDS=()
if [ -n "${COOP_PLAY:-}" ]; then IN_A=controller; IN_B=keyboard; MUTE_A=0; GOD=0; SIZE=1280x720
else IN_A=controller; IN_B=controller; MUTE_A=1; GOD=1; SIZE=960x540; fi
start_copy() {   # name mode np-name p2p-port pad-script input-mode mute "NAME=value ..."
  local T=$WORK/$1
  mkdir -p "$T"/{logs,hdd0,hdd1,no_flash}
  if [ -n "$SAVE" ] && [ ! -d "$T/gamedata" ]; then cp -R "$SAVE" "$T/gamedata"; fi
  mkdir -p "$T/gamedata"
  ( cd "$T" || exit 1
    export PS3_GAMEDATA_ROOT="$T/gamedata" PS3_HDD0_ROOT="$T/hdd0" PS3_HDD1_ROOT="$T/hdd1" PS3_DEV_FLASH="$T/no_flash"
    [ "$PS3_GAMEDATA_ROOT" = "$T/gamedata" ] || exit 1
    export GCM_NO_TITLE_RECYCLE=1 SPU_INTERP_UNLIFTED=1 SPURS_JC_EVENTS=0 PS3_PRIO_START=1 PAD_KEYMAP=souls
    export PS3_INPUT_MAP="$ROOT/mac/input.toml" PS3RECOMP_MSL_CACHE="${PS3RECOMP_MSL_CACHE:-$WORK/msl}" PS3_VERBOSE=0
    export SPU_DISPQ_WAIT=1 PS3_INPUT_MODE=$6 OSK_TEXT=$3
    if [ "$7" = 1 ]; then export AUDIO_MUTE=1; else unset AUDIO_MUTE; fi
    export LD_TEX_DIR="$ROOT/textures"
    export DES_SKIP_INTRO=1 DES_GOD=${COOP_GOD:-$GOD} DES_LUA_PRINT=1 PS3RECOMP_HOST_STATS=1
    unset DES_60FPS PS3_VBLANK_HZ PS3_CLOCK_SCALE PS3_NP_STRICT_OFFLINE      # 30 fps first (COOP_DEV.md)
    export PS3_NET_ONLINE=1 PSNR_SERVER=127.0.0.1 PS3_NP_ONLINE_ID=$3 PS3_NET_P2P_PORT=$4
    export DES_COOP=$2 DES_COOP_AUTO=1 DES_COOP_MAILBOX="$WORK/mailbox" PAD_SCRIPT=$5
    export PS3RECOMP_FFMPEG_AVCODEC=$(ls /opt/homebrew/lib/libavcodec.[0-9]*.dylib 2>/dev/null | grep -E 'libavcodec\.[0-9]+\.dylib$' | head -n 1)
    export PS3RECOMP_FFMPEG_AVUTIL=$(ls /opt/homebrew/lib/libavutil.[0-9]*.dylib 2>/dev/null | grep -E 'libavutil\.[0-9]+\.dylib$' | head -n 1)
    mkdir -p "$PS3RECOMP_MSL_CACHE"
    for kv in ${=8}; do export "$kv"; done      # this copy's own settings (COOP_ENV_A / COOP_ENV_B)
    [ -n "${COOP_FRAME_DUMP:-}" ] && export PS3RECOMP_METAL_FRAME_DUMP="$T/frame.ppm"   # the last frame shown, every 120 frames
    exec "$WIN" --title "Demon's Souls - CO-OP TEST $2" --size $SIZE -- "$BIN" "$ELF" >"$T/logs/out.txt" 2>"$T/logs/err.txt"
  ) &
  PIDS+=($!)
  echo "$2 copy pid $! in $T"
}
[ "$WHO" != guest ] && start_copy A host Host1 3658 "$PAD_A" $IN_A $MUTE_A "${COOP_ENV_A:-}"
[ "$WHO" = both ] && sleep 20
[ "$WHO" != host ] && start_copy B guest Guest1 3659 "$PAD_B" $IN_B 1 "${COOP_ENV_B:-}"
if [ -n "${COOP_STOP_HOST_AT:-}" ] && [ "$SECS" != 0 ]; then    # DEV TEST: the host goes away, the guest plays on
  caffeinate -dims -t $SECS &
  sleep $COOP_STOP_HOST_AT
  kill $PIDS[1] 2>/dev/null && echo "stopped the host (pid $PIDS[1]) at $COOP_STOP_HOST_AT s"
  sleep $(( SECS - COOP_STOP_HOST_AT ))
  for p in $PIDS; do kill $p 2>/dev/null && echo "stopped pid $p"; done
  wait $PIDS 2>/dev/null
elif [ "$SECS" = 0 ]; then
  echo "running until both game windows are closed"
  caffeinate -dims -w $PIDS[1] &
  wait $PIDS 2>/dev/null
else
  caffeinate -dims -t $SECS &
  sleep $SECS
  for p in $PIDS; do kill $p 2>/dev/null && echo "stopped pid $p"; done
  wait $PIDS 2>/dev/null
fi
kill $SRV 2>/dev/null
echo "done. co-op lines:"
for f in "$WORK"/*/logs/err.txt(N); do echo "== $f"; grep -a '\[coop\]' "$f" | head -40; done

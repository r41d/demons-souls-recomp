# Co-op (work in progress, branch `coop`)

The goal is co-op like the "seamless co-op" mods for the later Souls games: friends join your world without soul
signs, stay through boss fights, and follow you between areas. It is built on the game's own white-phantom summon
and session code. FROM's servers are gone, so nothing here talks to them. Everything below is **developer-only and
untested end to end**.

**Run game tests for this on the RTX 3070 / 5800X PC.** On the 4090 PC, test runs keep tripping the power supply
(NOTES fixes 203 / 207). Never use the player's own install or saves: the runner copies a save into its own folders.

## How step 1 works

Goal: two copies of the game on one PC, where one summons the other.

- **Login.** With `DES_COOP` set, login state 6 (func_00199EF8) skips StartLogin and the "Logging into the server"
  box and goes straight to state 0x14 (logged in, online mode on). IsLoggedIn (func_00568400) answers yes and logs
  each caller once. The HTTP request queue's own gate (I+0x19C) stays shut, so the game sends no HTTP.
- **Host.** The per-step hook (func_0001B8D0 -> des_coop_step in `patches/lifted-code.patch`, ppu_recomp_011.cpp)
  does two things. First, with no session yet (netmgr+8 == 0), it calls func_00540D90(netmgr, f1 = *(TOC table -
  0x7F90)), the same call the sign-touch handler makes at 0x3A1218. Second, once the session key is valid
  (func_0052C2D8 -> func_00E00D10 -> func_00DFE110), it serialises the key exactly as the host branch of
  func_003A1700 does (join type -1, then func_00DFE2F0) and writes it to `<mailbox>\host_key.bin`
  (project/ds_coop.cpp).
- **Guest.** The same hook reads that file and runs the guest branch of func_003A1700:
  - a reader over the bytes (func_00A8B770 / func_00A8B580) reads the s32 join type;
  - SetJoinType func_002107B8;
  - func_00DFE9F8 + func_00DFF0D0 rebuild the key;
  - func_0053CFE8(netmgr, key) joins;
  - func_00DFE768 frees the key.
- **The network manager pointer** is `*(*(*(TOC - 0x79AC) - 0x7F98))`.
- **Rooms and P2P** go through the runtime's sceNpMatching2-over-psnr client to **our own test server**,
  `tools/coop/mini_psnr.py`. It handles rooms, members, room data, messages and members' P2P endpoints, written
  from our client code. `psnr_selftest.py` (57 checks) proves the byte layouts.
- **Switches:**
  - `DES_COOP=host|guest`
  - `DES_COOP_AUTO=1`: the host writes its key every second; the guest joins when a new key appears and retries
    every 3 s.
  - `DES_COOP_MAILBOX=<folder>`
  - F6 (host) / F7 (guest) do the same by hand.
  - The usual `PS3_NET_ONLINE=1 PSNR_SERVER=127.0.0.1 PS3_NP_ONLINE_ID=<unique> PS3_NET_P2P_PORT=<unique>`.

## Where it stands (2026-11-15, 4090 PC; runs cut short by power cuts)

- **Checked in a host-only run:**
  - the login is skipped, with no server box and no Offline Mode box;
  - the network comes up (cellNetCtl IPObtained, NP ONLINE, Matching2 context);
  - the copy says HELLO to mini_psnr;
  - the game loads and plays at 30 fps;
  - netmgr was found (0x11110870 in that run).
- **IsLoggedIn callers:** 0x0053E904, 0x0053E5E0, 0x0052E3F0, 0x003A5CC0, 0x0052E008.
- **The host had no session** (netmgr+8 = 0, state 0). That fits the original game, where the host only creates
  a session when it touches a sign. This is what the func_00540D90 call above is for. It is **built but never run**:
  the build that included it lost the edit, and the next run ended in a power cut.

## Step 1 works (2026-11-16, MacBook M2 Pro, two copies on one Mac)

`tools/coop/run_coop_test.sh <work> both 185 <a copy of gamedata>` (the macOS runner; same switches as the
PowerShell one, `COOP_FRAME_DUMP=1` keeps each copy's last picture): the host sees a blue phantom "Guest1" beside
it, the guest sees "Host1", each with name plate and health bar, and both stay in the room until the run ends.
Three things were missing:

- **P2P vports (runtime, libs/network/sysNet.c).** The game binds three SOCK_DGRAM_P2P sockets (vports 6001,
  6002 and one more) on the one P2P port. The runtime gave each its own UDP socket on that port, so the second
  bind failed, and the game left its room 20 ms after creating it, once a second (CREATE_ROOM / LEAVE_ROOM pairs
  in psnr.log, "host key not valid yet" for ever). Now one host UDP socket per copy (the "hub") owns the P2P
  port; datagrams go out as `dst vport | src vport | payload`, and a thread forwards what arrives to the guest
  socket bound to that vport (each has a loopback port of its own, so poll / select / nonblocking reads are
  unchanged). psnr's own probes and punches are taken out first, as before. Both ends must run this build.
  **Not built or run on Windows**: the thread and socket calls have `_WIN32` branches written blind.
- **Session creation** (func_00540D90, already on the branch) does return 1 once the sockets bind:
  `[coop] host create session -> 1`, `host key valid`, `host key written: 169 bytes`.
- **Where the guest arrives.** The join ran the game's own sequence on the guest (Lua: OnJoinMutiplay,
  OnBeJoinStart_White, SummonReloadStart, OnBeSummoned_White; then the map's scripts again as a client), but
  the reload put it at the map's origin, (0, 1.5, 0), outside the level: a real summon arrives at its sign, and
  this join has none. The host now writes its block and position to `<mailbox>/host_pos.txt` four times a second
  (coop_host_pos); a guest within 3 m of the origin while a join is under way is put 1 m beside it
  (des_player_place, in des_coop_step). It fires several times around the reload (15 in one run), until the
  guest is in the level.

Seen in the logs on the way, harmless so far: before both copies are in the game the guest joins and both leave
again three or four times (rooms 1-5 in psnr.log); the room made once both are in 1-1 is the one that stays.
The host's own Lua prints nothing for the join (no SummonSuccess_White).

Not checked: anything past standing next to each other -- movement and attacks as seen by the other side,
enemies, damage, death, a boss, leaving the area, 60 fps, two machines, a character other than a copy of the
same save in both copies (both loaded the user's first slot, body form; the guest became a phantom anyway).

## The seamless rules: a script module (2026-11-16, MacBook)

The user's goal: the player who joins is an equal player, not a phantom, and a boss killed together counts in both
saves. The game's multiplayer rules are all in its event scripts (global_event.lua), so they are changed there, the
way boss rush changes them: `tools/make_coop.py` builds `game_coop/` (hard links to game/) with
`tools/coop/coop.lua` appended to global_event.lua in every map's script bundle; the last definition of a Lua global
wins. Run it with `COOP_GAME=game_coop` in front of the test runner.

- **A. Body form (works).** InGameStart is wrapped: a client that arrives as a white phantom gets
  `proxy:SetChrType(10000, CHR_TYPE_LivePlayer)`. Both screens then show the guest in body form with a full HP bar
  (soul-form effect 101 is no longer applied), and the scripts' IsWhiteGhost() branches no longer apply to it.
- **B. Everybody stays after a boss (works).** BlockClear2_1 is replaced: no rating menu, no LeaveSession, no walk
  home; a soul-form player is revived as the lone-player version does, and after 3 s everyone takes
  GetSoloClearBonus and the death / reload locks are released. Tested with
  `COOP_TEST_KILL=800:50` (make_coop.py --test-kill: the host ForceDeads Phalanx 50 s after the guest is in its
  party, from the 1-1 start): OnEvent_4096 and BlockClear2 ran on both copies, souls 4102 -> 6384 on the host and
  -> 5738 on the guest, the host (soul form in this save) came back to body form, no LEAVE_ROOM in psnr.log, and
  both still saw each other 70 s later. If the kill comes while the guest is still loading in, its picture was
  black at the end of that run (one run, not looked into).
- **C. The kill in the guest's own save (designed, not built).** While in the host's world the guest's event flags
  are the session's; every way home in the scripts calls `proxy:SetFlagInitState(2)`, which rolls them back (13
  call sites, all Lua). Plan: when the guest first arrives, note which boss kill flags the host's world already
  had (in spare event flags, which live through reloads inside the session); on the way home, read which kill
  flags are set now and were not then, roll back as the game does, then set those in the guest's own state.
  The boss table with each boss's kill flags is in tools/boss_rush/bossrush.lua (BR_B). Open: whether any way
  home rolls back in native code, whether the demon's soul taken in the session stays in the inventory, what the
  guest's save holds if the game is closed while still in the session.
- Not looked at yet: following the host through an area change or a fog gate, the session lock BlockClear2 sets
  (LockSession: it may refuse a new join after a boss), a guest dying, the host dying, black phantoms.

## Next steps

1. Host alone: `tools\coop\run_coop_test.ps1 -Work C:\coop_test -Who host -Save <a copy of a gamedata folder>`.
   Look for `[coop] host create session -> 1`, CREATE_ROOM in psnr.log, then `host key written: N bytes`.
   - If func_00540D90 returns 0, it stopped at func_00E43640 (online check) or func_00DFC008(netmgr+8). Trace
     which one.
2. Both copies, `-Who both`. The host should be in body form and the guest in soul form (only a soul can be a
   white phantom), both in the same world, e.g. 1-1. The default PAD_SCRIPT only loads the first save slot; drive
   the menus or pick the save so the two copies load different characters.
   - **Guest log:** `guest join: key N bytes`, then `join call returned 1`.
   - **psnr.log:** JOIN_ROOM, with MEMBER_JOINED pushed to the host.
   - **Lua prints:** OnBeSummoned_White / SummonSuccess_White.
3. Then the seamless rules, one at a time:
   - summon without a sign or soul-form limits;
   - stay after a boss;
   - re-summon automatically after the host changes area.
4. Then a launcher Host / Join, two real PCs (NAT; relay?), and separate co-op saves.

**Risks:**
- The guest may already hold an NR2 handle (func_0053CFE8 refuses then); see the refusal checks at 0x53D0BC-0x53D0F4.
- The Lua summon handlers may expect sign-server fields filled in (this+0x50 / +0x60).
- 60 fps sync: test at 30 fps first.

## Editing the lifted code: the order matters

1. `python tools\coop\lifted_patch_tool.py pristine ppu_recomp_NNN.cpp` saves the file as lifted.
2. Edit `port\recompiled\ppu_recomp_NNN.cpp`.
3. `python tools\coop\lifted_patch_tool.py regen ppu_recomp_NNN.cpp` writes the section into
   patches/lifted-code.patch and checks it.
4. BUILD.bat.

Do step 3 **before** building. When the patch differs from what was applied, BUILD re-applies it and overwrites
any edit that is not in it. That is how the session-create edit was lost once. Run the tool from the repo root;
its working files (lifted game code) are git-ignored and must never be committed.

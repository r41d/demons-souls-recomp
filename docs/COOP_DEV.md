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

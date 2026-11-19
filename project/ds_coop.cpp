/* Demon's Souls co-op, step 1 (DES_COOP=host|guest): a white-phantom summon between two copies of the game with
 * FROM's sign server gone (docs/NOTES.md, co-op).
 *
 * The game's own session code does the work at both ends; this module only carries the host's session key from
 * one copy to the other through a file (the "mailbox", DES_COOP_MAILBOX, default <PS3_HDD0_ROOT>\..\..\coop):
 *   host   the per-step hook (func_0001B8D0, patches/lifted-code.patch) serialises the host's session key the way
 *          the sign-summon branch of func_003A1700 does (join type -1, then func_00DFE2F0's key) and hands the bytes
 *          to coop_export(), which writes host_key.bin when they change.
 *   guest  the hook reads them back with coop_import() and runs the guest branch of func_003A1700: SetJoinType,
 *          func_00DFF0D0 rebuilds the key, func_0053CFE8 joins the host's room.
 * When: F6 (host: write the key now) / F7 (guest: join now), or by themselves with DES_COOP_AUTO=1 (host: every
 * second; guest: when a new key appears, again every 3 s until a join is under way).
 * The login is skipped while DES_COOP is set (login state 6 goes straight to "logged in", func_00199EF8) and
 * IsLoggedIn (func_00568400) answers yes; coop_logged_in_from() logs who asks. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <string>
#include <time.h>
#ifdef _WIN32
#include <windows.h>
#define COOP_SEP "\\"
#else
#include <sys/stat.h>
#define COOP_SEP "/"
#endif

extern "C" unsigned long long ps3_qpc_us(void);

namespace {
int g_mode = -1;          /* 0 off, 1 host, 2 guest */
int g_auto = 0;
std::string g_dir;
unsigned char g_last[0x200];
unsigned g_last_n = 0;
unsigned long long g_next_us = 0;
unsigned long long g_key_mtime = 0;    /* guest: the key file's time when it was last used */
int g_join_state = 0;                  /* guest: 0 waiting, 1 join called (rc 1), 2 failed (retry) */

void init()
{
    if (g_mode >= 0) return;
    const char* m = getenv("DES_COOP");
    g_mode = !m || !*m ? 0 : (m[0] == 'h' || m[0] == 'H') ? 1 : (m[0] == 'g' || m[0] == 'G') ? 2 : 0;
    const char* a = getenv("DES_COOP_AUTO");
    g_auto = a && *a == '1';
    const char* d = getenv("DES_COOP_MAILBOX");
    if (d && *d) g_dir = d;
    else {
        const char* h = getenv("PS3_HDD0_ROOT");
        g_dir = std::string(h && *h ? h : ".") + COOP_SEP ".." COOP_SEP ".." COOP_SEP "coop";
    }
#ifdef _WIN32
    CreateDirectoryA(g_dir.c_str(), NULL);
#else
    mkdir(g_dir.c_str(), 0755);
#endif
    if (g_mode)
        fprintf(stderr, "[coop] %s, mailbox %s%s\n", g_mode == 1 ? "host" : "guest", g_dir.c_str(),
                g_auto ? ", automatic (DES_COOP_AUTO=1)" : ", F6 = host writes its key, F7 = guest joins");
}

std::string key_path() { return g_dir + COOP_SEP "host_key.bin"; }

int key_edge(int vk)
{
#ifdef _WIN32
    static unsigned short was[256];
    const unsigned short now = (GetAsyncKeyState(vk) & 0x8000) ? 1 : 0;
    const int edge = now && !was[vk & 255];
    was[vk & 255] = now;
    return edge;
#else
    (void)vk;
    return 0;
#endif
}

unsigned long long file_time(const std::string& p)
{
#ifdef _WIN32
    WIN32_FILE_ATTRIBUTE_DATA a;
    if (!GetFileAttributesExA(p.c_str(), GetFileExInfoStandard, &a)) return 0;
    return ((unsigned long long)a.ftLastWriteTime.dwHighDateTime << 32) | a.ftLastWriteTime.dwLowDateTime;
#else
    struct stat st;
    if (stat(p.c_str(), &st)) return 0;
    return (unsigned long long)st.st_mtime;   /* seconds: a new key a second later is still seen on the next poll */
#endif
}
}  // namespace

namespace {
extern int g_guest_blk;
extern int g_keep_state;
extern int g_press;
int host_fresh(int* blk, float* xyz);
int same_map(int a, int b);
}

extern "C" int coop_mode(void)
{
    init();
    return g_mode;
}

/* 1 when the step hook should run its part now (host: serialise the key; guest: join). */
extern "C" int coop_poll(void)
{
    init();
    if (!g_mode) return 0;
    const unsigned long long t = ps3_qpc_us();
    if (g_mode == 1) {
        if (key_edge(0x75 /* F6 */)) return 1;
        if (g_auto && t >= g_next_us) { g_next_us = t + 1000000ull; return 1; }
        return 0;
    }
    if (key_edge(0x76 /* F7 */)) { g_join_state = 0; return 1; }
    if (!g_auto || t < g_next_us) return 0;
    g_next_us = t + 3000000ull;
    { int hb = -1;      /* not while the host is known to be in another map (see "Following the host") */
      if (host_fresh(&hb, 0) && g_guest_blk >= 0 && !same_map(hb, g_guest_blk)) return 0; }
    const unsigned long long ft = file_time(key_path());
    if (!ft) return 0;
    if (ft != g_key_mtime) { g_key_mtime = ft; g_join_state = 0; }
    return g_join_state != 1;
}

extern "C" void coop_export(const unsigned char* p, unsigned n, unsigned room_hi, unsigned room_lo)
{
    if (n > sizeof g_last) n = sizeof g_last;
    if (n == g_last_n && !memcmp(p, g_last, n)) return;
    memcpy(g_last, p, n);
    g_last_n = n;
    const std::string fin = key_path(), tmp = fin + ".tmp";
    FILE* f = fopen(tmp.c_str(), "wb");
    if (!f) { fprintf(stderr, "[coop] cannot write %s\n", tmp.c_str()); return; }
    fwrite(&n, 4, 1, f);
    fwrite(p, 1, n, f);
    fclose(f);
#ifdef _WIN32
    MoveFileExA(tmp.c_str(), fin.c_str(), MOVEFILE_REPLACE_EXISTING);
#else
    rename(tmp.c_str(), fin.c_str());
#endif
    fprintf(stderr, "[coop] host key written: %u bytes, room %08X%08X\n", n, room_hi, room_lo);
}

/* the host's key bytes, or 0 when there are none yet */
extern "C" unsigned coop_import(unsigned char* p, unsigned cap)
{
    FILE* f = fopen(key_path().c_str(), "rb");
    if (!f) { fprintf(stderr, "[coop] no host key yet (%s)\n", key_path().c_str()); return 0; }
    unsigned n = 0;
    const size_t got = fread(&n, 4, 1, f) == 1 && n <= cap ? fread(p, 1, n, f) : 0;
    fclose(f);
    if (!got || got != n) { fprintf(stderr, "[coop] host key file unreadable\n"); return 0; }
    return n;
}

extern "C" void coop_join_result(int rc)
{
    g_join_state = rc == 1 ? 1 : 2;
    fprintf(stderr, "[coop] join call returned %d%s\n", rc, rc == 1 ? " (join under way)" : " (will retry)");
}

/* Where the guest arrives (the step hook, des_coop_step): the host leaves its map block and position in
 * <mailbox>/host_pos.txt four times a second; the guest reads it while its join is under way. */
extern "C" void coop_host_pos(int blk, const float* xyz)
{
    static unsigned long long next;
    const unsigned long long t = ps3_qpc_us();
    if (g_mode != 1 || t < next) return;
    next = t + 250000ull;
    const std::string fin = g_dir + COOP_SEP "host_pos.txt", tmp = fin + ".tmp";
    FILE* f = fopen(tmp.c_str(), "w");
    if (!f) return;
    fprintf(f, "%d %.4f %.4f %.4f\n", blk, (double)xyz[0], (double)xyz[1], (double)xyz[2]);
    fclose(f);
#ifdef _WIN32
    MoveFileExA(tmp.c_str(), fin.c_str(), MOVEFILE_REPLACE_EXISTING);
#else
    rename(tmp.c_str(), fin.c_str());
#endif
}

/* Following the host between maps. A join only works with both players in the same map (in another one the game's
 * own join fails: OnFailedJoinSession, over and over, and the host re-creates its room each time). So the guest
 * joins only when the host's position file is fresh and names the guest's own map; the co-op script module moves
 * the guest to the host's map first (coop.lua, COOP_Follow*), asking through coop_lua_query:
 *   get follow      0 = wait, 1 = the host is in another map (host_area / host_block say which),
 *                   2 = no sign of the host for a minute since "lost" was set
 *   get host_area, get host_block */
namespace {
int g_guest_blk = -1;                   /* the guest's own map block, from the step hook */
unsigned long long g_lost_us = 0;       /* when the script said the session was lost */

/* the host's block and position, when the host wrote them within the last 4 seconds */
int host_fresh(int* blk, float* xyz)
{
    const std::string p = g_dir + COOP_SEP "host_pos.txt";
#ifdef _WIN32
    WIN32_FILE_ATTRIBUTE_DATA a;
    FILETIME now;
    if (!GetFileAttributesExA(p.c_str(), GetFileExInfoStandard, &a)) return 0;
    GetSystemTimeAsFileTime(&now);
    const unsigned long long t0 = ((unsigned long long)a.ftLastWriteTime.dwHighDateTime << 32) | a.ftLastWriteTime.dwLowDateTime;
    const unsigned long long t1 = ((unsigned long long)now.dwHighDateTime << 32) | now.dwLowDateTime;
    if (t1 - t0 > 40000000ull) return 0;
#else
    struct stat st;
    if (stat(p.c_str(), &st) || time(NULL) - st.st_mtime > 4) return 0;
#endif
    FILE* f = fopen(p.c_str(), "r");
    if (!f) return 0;
    float v[3];
    const int n = fscanf(f, "%d %f %f %f", blk, &v[0], &v[1], &v[2]);
    fclose(f);
    if (n != 4) return 0;
    if (xyz) memcpy(xyz, v, sizeof v);
    return 1;
}
int same_map(int a, int b) { return (a & 0xFFFF) == (b & 0xFFFF); }
}  // namespace

extern "C" void coop_guest_blk(int blk) { g_guest_blk = blk; }

/* The guest's souls across the map load it makes by itself to follow the host. That load added souls nobody
 * earned (4102 -> 8611 in the tests, 6361 in one; no script of ours ran in between, with or without the switch to
 * soul form; g_Initialize's AddBlockClearBonus, "souls not obtained yet", is the suspect). The script says
 * souls_keep 1 before it starts the warp and 2 once the map is in; the step hook passes the current count and
 * writes back what this returns when it is not -1. */
namespace { int g_keep_state = 0, g_keep = -1, g_regroup = 0; }

/* "<name> has left." on the guest (event text 20001020) is a box that waits for OK, shown every time the host
 * changes map. It is not opened by the scripts (OnLeavePlayer never runs) and proxy:CloseMenu() leaves it up, so
 * the script asks for the button instead: "set press_ok 1" while it sees an info box at the moments it expects
 * that one. The step hook then holds CROSS for a few steps, lets go, and says when it is done.
 * Returns the pad mask to inject, or ~0u when there is nothing to do. */
namespace { int g_press = 0; }
extern "C" unsigned coop_pad_inject(void)
{
    if (g_press <= 0) return ~0u;
    g_press--;
    return g_press >= 12 ? 0x4000u : 0u;     /* 6 steps down (CROSS), 12 steps up */
}
extern "C" int coop_souls_fix(int now)
{
    if (g_keep_state == 1) { g_keep = now; g_keep_state = 3; return -1; }      /* remembered, waiting for the load */
    if (g_keep_state == 2) {
        g_keep_state = 0;
        const int was = g_keep;
        g_keep = -1;
        return was >= 0 && now != was ? was : -1;
    }
    return -1;
}

extern "C" int coop_guest_host_pos(int* blk, float* xyz)
{
    static unsigned long long next;
    const unsigned long long t = ps3_qpc_us();
    if (g_mode != 2 || t < next) return 0;
    if (g_regroup) {             /* "set regroup 1": a guest that went down gets up next to the host (returns 2) */
        next = t + 250000ull;
        if (!host_fresh(blk, xyz) || !same_map(*blk, g_guest_blk)) return 0;
        g_regroup = 0;
        return 2;
    }
    if (g_join_state != 1) return 0;
    next = t + 250000ull;
    FILE* f = fopen((g_dir + COOP_SEP "host_pos.txt").c_str(), "r");
    if (!f) return 0;
    const int n = fscanf(f, "%d %f %f %f", blk, &xyz[0], &xyz[1], &xyz[2]);
    fclose(f);
    return n == 4;
}

/* What the co-op script module (tools/coop/coop.lua) keeps across map loads: named numbers. Its script globals
 * are gone after every load, and the event flags it could use are rolled back on the guest's way home.
 *   print("COOPQ set <name> <number>")   -> the number
 *   print("COOPQ get <name>")            -> the number, 0 when never set
 * (the scripts' print returns the answer: func_00C02C78 in patches/lifted-code.patch). In memory only: a game
 * closed in the middle of a session loses it. */
extern "C" double coop_lua_query(const char* q)
{
    static struct { char name[40]; double v; } kv[128];
    static int n;
    char op[8] = "", name[40] = "";
    double v = 0;
    const int got = sscanf(q, "%7s %39s %lf", op, name, &v);
    if (got < 2) return 0;
    if (!strcmp(op, "get") && (!strcmp(name, "follow") || !strcmp(name, "host_area") || !strcmp(name, "host_block"))) {
        int hb = -1;
        const int fresh = host_fresh(&hb, 0);
        if (name[0] == 'h') return !fresh ? -1 : name[5] == 'a' ? (double)((hb >> 8) & 0xFF) : (double)(hb & 0xFF);
        if (fresh) { g_lost_us = ps3_qpc_us(); return g_guest_blk >= 0 && !same_map(hb, g_guest_blk) ? 1 : 0; }
        return g_lost_us && ps3_qpc_us() - g_lost_us > 60000000ull ? 2 : 0;
    }
    if (!strcmp(op, "set") && !strcmp(name, "lost") && v == 1) g_lost_us = ps3_qpc_us();
    if (!strcmp(op, "get") && !strcmp(name, "mode")) return g_mode;       /* 1 host, 2 guest */
    if (!strcmp(op, "set") && !strcmp(name, "press_ok")) { if (g_press <= 0) g_press = 18; return v; }   /* coop_pad_inject */
    if (!strcmp(op, "set") && !strcmp(name, "regroup")) { g_regroup = v != 0; return v; }   /* coop_guest_host_pos */
    if (!strcmp(op, "set") && !strcmp(name, "souls_keep")) {            /* see coop_souls_fix */
        if (v == 1 || (v == 2 && g_keep_state == 3)) g_keep_state = (int)v;   /* 2 only after a count was remembered */
        return v;
    }
    if (!strcmp(op, "get") && !strncmp(name, "env_", 4)) {                /* DEV: get env_X = the number in COOP_X */
        const std::string e = std::string("COOP_") + (name + 4);
        const char* x = getenv(e.c_str());
        return x ? atof(x) : 0;
    }
    int i = 0;
    while (i < n && strcmp(kv[i].name, name)) i++;
    if (!strcmp(op, "set") && got == 3) {
        if (i == n) { if (n == 128) return 0; strcpy(kv[n++].name, name); }
        kv[i].v = v;
        return v;
    }
    return i < n ? kv[i].v : 0;
}

/* IsLoggedIn (func_00568400) callers, once each */
extern "C" void coop_logged_in_from(unsigned lr)
{
    static unsigned seen[64];
    static int n;
    for (int i = 0; i < n; i++)
        if (seen[i] == lr) return;
    if (n < 64) seen[n++] = lr;
    fprintf(stderr, "[coop] IsLoggedIn asked from 0x%08X\n", lr);
}

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

extern "C" int coop_guest_host_pos(int* blk, float* xyz)
{
    static unsigned long long next;
    const unsigned long long t = ps3_qpc_us();
    if (g_mode != 2 || g_join_state != 1 || t < next) return 0;
    next = t + 250000ull;
    FILE* f = fopen((g_dir + COOP_SEP "host_pos.txt").c_str(), "r");
    if (!f) return 0;
    const int n = fscanf(f, "%d %f %f %f", blk, &xyz[0], &xyz[1], &xyz[2]);
    fclose(f);
    return n == 4;
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

"""Co-op (work in progress, docs/COOP_DEV.md): build game_coop/ from your own game/, as make_boss_rush.py builds
game_bossrush/ -- a copy made of hard links, with tools/coop/coop.lua appended to global_event.lua in every map's
script bundle. coop.lua redefines the game's own multiplayer handlers (the last definition of a Lua global wins).

  python tools/make_coop.py          builds or refreshes game_coop/
  python tools/make_coop.py --test-kill <character id>:<seconds>   DEV TEST: the host kills that character (a boss:
                                     Phalanx is 800) that long after a guest joins; a plain run removes it again
"""
import hashlib, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_boss_rush as br

MODULE = os.path.join(br.HERE, 'coop', 'coop.lua')
DST = os.path.join(br.ROOT, 'game_coop')
STAMP = 'COOP_BUILT.txt'
TEST = b''        # Lua put after the module (--test-kill)


def bundle(src):
    raw, info = br.sf.dcx_decompress(src)
    bnd = br.sf.Bnd3(raw)
    e = br.bnd_entry(bnd, '\\global_event.lua')
    ge = e['data']
    nl = b'\r\n' if b'\r\n' in ge else b'\n'
    mod = br.rd(MODULE).replace(b'\r\n', b'\n').replace(b'\n', nl)
    e['data'] = ge + nl + mod + nl + TEST.replace(b'\n', nl)
    out = br.sf.dcx_compress(bnd.write(), info)
    assert br.bnd_entry(br.sf.Bnd3(br.sf.dcx_decompress(out)[0]), '\\global_event.lua')['data'] == e['data']
    return out


def stamp_text():
    return 'built by tools/make_coop.py from game/\n' + hashlib.sha256(br.rd(MODULE) + br.rd(__file__) + TEST).hexdigest() + '\n'


def main():
    global TEST
    if '--test-kill' in sys.argv:
        chr_id, secs = sys.argv[sys.argv.index('--test-kill') + 1].split(':')
        TEST = ('COOP_TEST_KILL = %d;\nCOOP_TEST_AFTER = %g;\n' % (int(chr_id), float(secs))).encode()
    if not os.path.isdir(os.path.join(br.GAME, br.USR)):
        sys.exit('no game/ yet -- run the setup first')
    p = os.path.join(DST, STAMP)
    if os.path.isfile(p) and br.rd(p).decode() == stamp_text():
        print('  game_coop is up to date')
        return
    if os.path.isfile(p):
        os.remove(p)
    print('  game_coop: hard links to game/ ...')
    br.link_tree(br.GAME, DST)
    sd = os.path.join(br.USR, 'SCRIPT')
    for m in br.SCRIPT_BUNDLES:
        br.put(DST, os.path.join(sd, m + '.luabnd.dcx.sdat'), bundle(br.rd(os.path.join(br.GAME, sd, m + '.luabnd.dcx'))))
    print('  event scripts: the co-op module in %d bundles' % len(br.SCRIPT_BUNDLES))
    with open(p, 'w', newline='\n') as f:
        f.write(stamp_text())
    print('  game_coop ready')


if __name__ == '__main__':
    main()

"""Build the Arabic files from YOUR OWN game files (no game data is shipped with this script).

  python apply_ar.py <USRDIR> <out_dir>

<USRDIR> = .../PS3_GAME/USRDIR of an extracted US game (read only). Writes to <out_dir>:
  FONT/NA_ENGLISH/{dsfont24,talkfont24}.{ccm,tpf}[.dcx]   fonts with the Arabic letter forms added
  MSG/NA_ENGLISH/{menu,item}.msgbnd[.dcx]                 all game text from ar_text.json
  MENU/menu.drb                                           menu layout with the text right-aligned (drb_rtl.py)
  MENU/menu.tpf[.dcx]                                     the title logo in Arabic (make_ar_title.py)
  MOVIE/ds_title.pam                                      the title movie with the Arabic logo (needs ffmpeg)
Numbers are shown in Arabic-Indic digits (the fonts draw 0-9 as U+0660-U+0669); --latin-digits keeps 0-9.
ar_menu.json holds normal (logical-order) Arabic. Each line is shaped into presentation forms and put in
visual (left-to-right drawing) order here, because the game draws code points one by one, left to right.
To try it, copy those files over a COPY of the game (unlink hard links first; never write into the real game).
"""
import argparse
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_ar_font  # noqa: E402  (also puts the repo's tools/ on sys.path)
import drb_rtl       # noqa: E402
import arshape       # noqa: E402
import souls_formats as sf  # noqa: E402
import fmg           # noqa: E402
from ccm import Ccm  # noqa: E402

TAG = re.compile(r'<\?[^?]*\?>')
ARABIC = re.compile('[\u0621-\u064A]')
# atomic left-to-right units inside Arabic lines: game tags, and a weapon's upgrade level ("+3")
TOKENS = r'<\?[^?>]*\?>|\+\d+'
# FMGs whose long English text is hard-wrapped by the writers: the Arabic is re-wrapped here, in logical order,
# to the widest English line of the same FMG (measured with the same font), so lines stay inside the box.
# Blank lines (paragraph breaks) are kept. (bnd, fmg) -> font that draws it
REWRAP = {('menu', 1): 'talkfont24', ('menu', 3): 'talkfont24', ('menu', 30): 'dsfont24', ('menu', 78): 'dsfont24',
          ('item', 16): 'dsfont24', ('item', 17): 'dsfont24', ('item', 23): 'dsfont24', ('item', 24): 'dsfont24',
          ('item', 25): 'dsfont24', ('item', 26): 'dsfont24', ('item', 27): 'dsfont24', ('item', 29): 'dsfont24'}
ICON_W = 26
# strings the game does not have, added for menu.drb shapes that drb_rtl.RETARGET points at: (bnd, fmg) -> {id: Arabic}
EXTRA = {('menu', 70): {6104: 'عناصر الشاشة'}}     # options "HUD" (reviewer Zack)


def load(path):
    if os.path.exists(path):
        return open(path, 'rb').read()
    return sf.dcx_decompress(open(path + '.dcx', 'rb').read())[0]


def save(path, payload, dcx_template):
    open(path, 'wb').write(payload)
    _, info = sf.dcx_decompress(open(dcx_template, 'rb').read())
    d = sf.dcx_compress(payload, info)
    assert sf.dcx_decompress(d)[0] == payload
    open(path + '.dcx', 'wb').write(d)


def width_fn(font):
    """Pixel width of a string as the engine steps it: advance + pre per glyph; a tag counts as an icon."""
    def chars(s):
        w = 0
        for ch in s:
            gi = font.index(ord(ch))
            if gi is not None:
                w += font.glyphs[gi][6] + font.glyphs[gi][4]
        return w

    def fn(s):
        w = 0
        for u in arshape.tokenize(s, TOKENS):
            w += ICON_W if isinstance(u, arshape.Token) and u.startswith('<?') else chars(u)
        return w
    return fn


def to_game(text, wrap_font=None, max_w=None):
    """Logical Arabic -> what the FMG stores: per line, shaped and in visual order, outer spaces trimmed;
    with wrap_font, each non-empty line is first broken (logical order) to max_w pixels."""
    out = []
    for line in text.split('\n'):
        line = line.strip(' ')
        if wrap_font is not None and line:
            out.extend(arshape.wrap(line, width_fn(wrap_font), max_w, token_re=TOKENS))
        else:
            out.append(arshape.visual(arshape.shape(line, token_re=TOKENS), token_re=TOKENS))
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('usrdir')
    ap.add_argument('out')
    ap.add_argument('--text', default=os.path.join(HERE, 'ar_text.json'))
    ap.add_argument('--latin-digits', action='store_true',
                    help='keep 0-9 as they are (default: numbers are shown in Arabic-Indic digits)')
    ap.add_argument('--only', default='', help='comma list of FMG ids to translate (default: all in the JSON)')
    a = ap.parse_args()
    assert os.path.abspath(a.out) not in os.path.abspath(a.usrdir), 'out must not be inside the game folder'
    src_font = os.path.join(a.usrdir, 'FONT', 'NA_ENGLISH')
    src_msg = os.path.join(a.usrdir, 'MSG', 'NA_ENGLISH')
    dst_font = os.path.join(a.out, 'FONT', 'NA_ENGLISH')
    dst_msg = os.path.join(a.out, 'MSG', 'NA_ENGLISH')
    os.makedirs(dst_font, exist_ok=True)
    os.makedirs(dst_msg, exist_ok=True)

    used = make_ar_font.used_codepoints(a.usrdir)
    tmp = tempfile.mkdtemp()
    fonts = {}
    for f in ('dsfont24', 'talkfont24'):
        for ext in ('ccm', 'tpf'):
            open(os.path.join(tmp, f'{f}.{ext}'), 'wb').write(load(os.path.join(src_font, f'{f}.{ext}')))
        n, left = make_ar_font.build(os.path.join(tmp, f + '.ccm'), os.path.join(tmp, f + '.tpf'),
                                     os.path.join(tmp, f + '_ar.ccm'), os.path.join(tmp, f + '_ar.tpf'), f, used,
                                     digits=not a.latin_digits)
        for ext in ('ccm', 'tpf'):
            save(os.path.join(dst_font, f'{f}.{ext}'), open(os.path.join(tmp, f'{f}_ar.{ext}'), 'rb').read(),
                 os.path.join(src_font, f'{f}.{ext}.dcx'))
        fonts[f] = Ccm(open(os.path.join(dst_font, f + '.ccm'), 'rb').read())
        print(f, n, 'Arabic glyphs added,', left, 'spare cells left')

    ar = json.load(open(a.text, encoding='utf-8'))
    only = set(a.only.split(',')) if a.only else None
    orig = {f: Ccm(load(os.path.join(src_font, f + '.ccm'))) for f in fonts}
    for bname in ('menu', 'item'):
        tr_b = ar.get(bname, {})
        if not tr_b:
            continue
        bnd = sf.Bnd3(load(os.path.join(src_msg, bname + '.msgbnd')))
        changed = kept = skipped = 0
        for e in bnd.entries:
            tr = tr_b.get(str(e['id']))
            if not tr or (only and str(e['id']) not in only):
                continue
            m = fmg.read(e['data'])
            wf = REWRAP.get((bname, e['id']))
            max_w = None
            if wf:
                ew = width_fn(orig[wf])
                max_w = max([ew(l) for v in m.values() if v for l in v.split('\n')] or [600])
            for k, v in tr.items():
                k = int(k)
                en = m.get(k)
                if en is None or not ARABIC.search(v):
                    kept += 1
                    continue
                g = to_game(v, fonts[wf] if wf else None, max_w)
                lines_ok = (g.count('\n') == en.count('\n')) if not wf else True
                ok = (sorted(TAG.findall(g)) == sorted(TAG.findall(en)) and lines_ok
                      and all(fonts[f].index(ord(ch)) is not None for f in fonts for ch in TAG.sub('', g) if ch != '\n' and ch not in en))
                if not ok:
                    skipped += 1
                    print('  skipped (check failed):', bname, e['id'], k)
                    continue
                if wf and g.count('\n') > en.count('\n') + 1:
                    print(f'  note: {bname} {e["id"]} {k}: {g.count(chr(10)) + 1} lines (English {en.count(chr(10)) + 1})')
                m[k] = g
                changed += 1
            for k, v in EXTRA.get((bname, e['id']), {}).items():
                assert k not in m, (bname, e['id'], k)
                m[k] = to_game(v)
            e['data'] = fmg.write(m)
        save(os.path.join(dst_msg, bname + '.msgbnd'), bnd.write(), os.path.join(src_msg, bname + '.msgbnd.dcx'))
        print(f'{bname}: strings in Arabic: {changed}, left as they are: {kept}, skipped: {skipped}')
    layout(a.usrdir, a.out)


def layout(usrdir, out):
    os.makedirs(os.path.join(out, 'MENU'), exist_ok=True)
    drb_rtl.main(os.path.join(usrdir, 'MENU', 'menu.drb'), os.path.join(out, 'MENU', 'menu.drb'))
    # the title-screen logo in Arabic, drawn into the player's own menu.tpf (make_ar_title.py)
    import make_ar_title
    src = os.path.join(usrdir, 'MENU', 'menu.tpf')
    tmp = os.path.join(tempfile.mkdtemp(), 'menu.tpf')
    open(tmp, 'wb').write(load(src))
    make_ar_title.build(tmp, tmp + '.ar')
    save(os.path.join(out, 'MENU', 'menu.tpf'), open(tmp + '.ar', 'rb').read(), src + '.dcx')
    print('menu.tpf: title logo in Arabic')
    # the title movie (the logo forming out of smoke) remade for the Arabic logo; needs ffmpeg (make_ar_title_movie.py)
    import shutil as _sh
    if _sh.which('ffmpeg'):
        import make_ar_title_movie
        os.makedirs(os.path.join(out, 'MOVIE'), exist_ok=True)
        make_ar_title_movie.build(os.path.join(usrdir, 'MOVIE', 'ds_title.pam'), os.path.join(out, 'MOVIE', 'ds_title.pam'))
    else:
        print('MOVIE/ds_title.pam: skipped (no ffmpeg): the title movie still draws the English logo')


if __name__ == '__main__':
    main()

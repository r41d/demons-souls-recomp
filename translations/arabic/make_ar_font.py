"""Add Arabic presentation forms (U+FE80-U+FEFC, lam-alef ligatures included) and Arabic punctuation to the
Demon's Souls NA_ENGLISH fonts (dsfont24 = menus, talkfont24 = dialogue / subtitles).

The glyphs are rendered from Noto Sans Arabic (SIL OFL 1.1). Where they go on the glyph page, in order:
  1. the empty bottom of the text page (below the last ink row), packed in rows;
  2. then cells of characters no US English string uses (Cyrillic, Greek, ... : see REUSE_BLOCKS), cleared and
     reused; those characters' own CCM entries keep pointing at the cell (they never appear in US text).
Only the BC3 blocks (and mip blocks) that change are re-encoded; the rest of the page stays byte-identical.

Each joining form is drawn so its joining stroke reaches the cell edge, and its advance is the cell width minus
OVERLAP pixels, so neighbouring letters overlap and the joins have no gap.

With digits=True (the default) the Arabic-Indic digits U+0660-U+0669 (as used in Saudi Arabia) are added too, and the
ASCII digits 0-9 are redrawn as them, in their own cells (no extra room needed), so every number the game prints
(souls, stats, counts, written by code with ASCII digits) is shown in Arabic-Indic digits. Each keeps its ASCII
digit's cell, width and advance (centred in it), so numbers the menus line up stay lined up. Multi-digit numbers keep their order (most significant digit on the left), which
is also how Arabic-Indic numbers are written.
"""
import os
import struct
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools'))
sys.path.insert(0, HERE)
import make_font as mf            # noqa: E402  (BC3 helpers, souls_formats via its sys.path)
from ccm import Ccm               # noqa: E402

FONT = os.path.join(HERE, 'fonts', 'NotoSansArabic.ttf')
ARABIC = list(range(0xFE80, 0xFEFD)) + [0x060C, 0x061B, 0x061F, 0x0640]
DIGITS = range(0x0660, 0x066A)      # Arabic-Indic digits 0-9
# per font: pixel size, weight, baseline row in the 26-px cell, overlap of joining strokes
STYLE = {'dsfont24': dict(size=20, wght=500, base=19, overlap=1),
         'talkfont24': dict(size=20, wght=700, base=19, overlap=1)}
REUSE_BLOCKS = (0x03, 0x04, 0xFF, 0x30, 0x31, 0x32, 0x4E, 0x4F, 0x50, 0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57,
                0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x5F, 0x60, 0x61, 0x62, 0x63, 0x64, 0x65, 0x66, 0x67,
                0x68, 0x69, 0x6A, 0x6B, 0x6C, 0x6D, 0x6E, 0x6F, 0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x76, 0x77,
                0x78, 0x79, 0x7A, 0x7B, 0x7C, 0x7D, 0x7E, 0x7F, 0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87,
                0x88, 0x89, 0x8A, 0x8B, 0x8C, 0x8D, 0x8E, 0x8F, 0x90, 0x91, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97,
                0x98, 0x99, 0x9A, 0x9B, 0x9C, 0x9D, 0x9E, 0x9F)

JOINS_LEFT = set()    # forms whose stroke continues to the next (left) letter: initial and medial
JOINS_RIGHT = set()   # forms joined from the previous (right) letter: final and medial
for _cp in range(0xFE80, 0xFEFD):
    import unicodedata as _ud
    _n = _ud.name(chr(_cp), '')
    if 'INITIAL' in _n or 'MEDIAL' in _n:
        JOINS_LEFT.add(_cp)
    if 'FINAL' in _n or 'MEDIAL' in _n:
        JOINS_RIGHT.add(_cp)
JOINS_LEFT.add(0x0640)    # tatweel: a joining stroke on both sides
JOINS_RIGHT.add(0x0640)


def _components(m):
    """8-connected components of a boolean mask: list of (ys, xs) index arrays."""
    seen = np.zeros_like(m, bool)
    out = []
    H, W = m.shape
    for y0, x0 in zip(*np.nonzero(m)):
        if seen[y0, x0]:
            continue
        st, ys, xs = [(y0, x0)], [], []
        seen[y0, x0] = True
        while st:
            y, x = st.pop()
            ys.append(y); xs.append(x)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < H and 0 <= xx < W and m[yy, xx] and not seen[yy, xx]:
                        seen[yy, xx] = True
                        st.append((yy, xx))
        out.append((np.array(ys), np.array(xs)))
    return out


DOT_GAP = 2     # pixels between two dots side by side (Noto's 1 px gap closes when the game draws text smaller)


def spread_dots(a, cp=0):
    """Pull the dots of a two- or three-dot letter apart so there are DOT_GAP clear columns between dots side by
    side. The game draws menu text smaller than the cell (about 0.75x, filtered), which merged Noto's 1-px gaps
    into one blob (reviewer: the dots of ت ي ة ث looked wrong). Dots stay inside the glyph's own ink extent, so
    the cell does not widen and joining strokes stay flush with the cell edges."""
    strong = a > 110
    comps = _components(strong)
    if len(comps) < 2:
        return a
    lab = np.full(a.shape, -1, int)
    for k, (ys, xs) in enumerate(comps):
        lab[ys, xs] = k
    # every faint pixel belongs to the strong component next to it (its soft edge)
    weak = (a > 6) & (lab < 0)
    for y, x in zip(*np.nonzero(weak)):
        nb = lab[max(y - 1, 0):y + 2, max(x - 1, 0):x + 2]
        nb = nb[nb >= 0]
        if len(nb):
            lab[y, x] = np.bincount(nb).argmax()
    body = max(range(len(comps)), key=lambda k: len(comps[k][0]))
    cols = np.nonzero((a > 24).any(axis=0))[0]
    lo, hi = int(cols.min()), int(cols.max())
    # the dots may grow the glyph by a pixel on a side that has no joining stroke
    if cp not in JOINS_LEFT:
        lo -= 1
    if cp not in JOINS_RIGHT:
        hi += 1
    # dots that touch at this size come out as one small blob: split it at its faint middle column (two dots
    # side by side), or into a top dot and a pair under it (three dots, ث ش)
    nxt = len(comps)
    parts = []
    for k, (ys, xs) in enumerate(comps):
        h, w = ys.max() - ys.min() + 1, xs.max() - xs.min() + 1
        if k == body or len(ys) > 18 or h > 5 or w > 6 or w < 4:
            parts.append((k, ys, xs))
            continue
        top_rows = 2 if h >= 4 else 0
        by = ys.min() + top_rows
        sub = a[by:ys.max() + 1, xs.min():xs.max() + 1].astype(int)
        colsum = sub.sum(axis=0)
        inner = colsum[1:-1]
        cut = 1 + int(inner.argmin()) if len(inner) else -1
        if cut < 0 or colsum[cut] > 0.4 * colsum.max():
            parts.append((k, ys, xs))      # no faint middle: a hamza or one wide dot, leave it
            continue
        cx = xs.min() + cut
        lower = ys >= by
        groups = [(lower & (xs < cx)), (lower & (xs > cx))]
        if top_rows:
            groups.append(~lower)
        for gi, g in enumerate(groups):
            kk = k if gi == 0 else nxt
            if gi:
                nxt += 1
            lab[ys[g], xs[g]] = kk
            parts.append((kk, ys[g], xs[g]))
        mid = lower & (xs == cx)
        lab[ys[mid], xs[mid]] = -1
    dots = []
    for k, ys, xs in parts:
        if k == body or not len(ys):
            continue
        h, w = ys.max() - ys.min() + 1, xs.max() - xs.min() + 1
        if len(ys) <= 9 and h <= 3 and w <= 3:
            dots.append(dict(k=k, y0=int(ys.min()), y1=int(ys.max()), x0=int(xs.min()), x1=int(xs.max())))
    out = a.copy()
    used = set()
    pairs = []
    for i, d in enumerate(dots):
        for j, e in enumerate(dots):
            if j <= i or i in used or j in used:
                continue
            if min(d['y1'], e['y1']) - max(d['y0'], e['y0']) < 0:
                continue
            L, R = (d, e) if d['x0'] < e['x0'] else (e, d)
            gap = R['x0'] - L['x1'] - 1
            if 0 <= gap < DOT_GAP:
                used.update((i, j))
                pairs.append((L, R, gap))
    for L, R, gap in pairs:
        wl, wr = L['x1'] - L['x0'] + 1, R['x1'] - R['x0'] + 1
        room = hi - lo + 1
        g = DOT_GAP if wl + wr + DOT_GAP <= room else max(gap, room - wl - wr)
        if g <= gap:
            continue
        total = wl + g + wr
        start = int(round((L['x0'] + R['x1'] + 1) / 2 - total / 2))
        start = min(max(start, lo), hi - total + 1)
        moves = [(L['k'], start - L['x0']), (R['k'], start + wl + g - R['x0'])]
        lifted = []
        for k, sh in moves:
            ys, xs = np.nonzero(lab == k)
            lifted.append((ys, xs, a[ys, xs].copy(), sh))
            out[ys, xs] = 0
        for ys, xs, v, sh in lifted:
            nx = xs + sh
            ok = (nx >= 0) & (nx < a.shape[1])
            out[ys[ok], nx[ok]] = np.maximum(out[ys[ok], nx[ok]], v[ok])
    return out


def render(cp, st, H):
    """(alpha cell H x w, ink-free advance) for one presentation form, baseline at st['base']."""
    f = ImageFont.truetype(FONT, st['size'])
    try:
        f.set_variation_by_axes([st['wght']])
    except Exception:
        pass
    ch = chr(cp)
    adv = int(round(f.getlength(ch)))
    pad = 10
    W = adv + 2 * pad + 8
    big = H + 16                     # render with room above and below, then fit into the cell
    im = Image.new('L', (W, big), 0)
    B = st['base'] + 8
    ImageDraw.Draw(im).text((pad, B), ch, font=f, fill=255, anchor='ls')
    a = np.asarray(im).copy()
    if 0xFE80 <= cp <= 0xFEFC:
        a = spread_dots(a, cp)
    xs = np.nonzero(a.max(axis=0) > 8)[0]
    if not len(xs):
        return None
    # horizontally: the advance box [pad, pad + adv], widened to any ink outside it
    x0, x1 = min(int(xs.min()), pad), max(int(xs.max()) + 1, pad + adv)
    a = a[:, x0:x1]
    # vertically: rows above the baseline into 0..base, rows below into base..H-1; a glyph that sticks out
    # (hamza on top, deep descenders) is squashed on that side only, so every baseline stays at st['base']
    ys = np.nonzero(a.max(axis=1) > 8)[0]
    top, bot = int(ys.min()), int(ys.max()) + 1
    up = a[min(top, B - st['base']):B]
    dn = a[B:max(bot, B + 1)]
    if up.shape[0] > st['base']:
        up = mf.resize(up, up.shape[1], st['base'])
    if dn.shape[0] > H - st['base']:
        dn = mf.resize(dn, dn.shape[1], H - st['base'])
    cell = np.zeros((H, a.shape[1]), np.uint8)
    cell[st['base'] - up.shape[0]:st['base']] = up
    cell[st['base']:st['base'] + dn.shape[0]] = dn
    rows = slice(max(st['base'] - 5, 0), st['base'] + 2)     # the joining stroke only, not dots above / below it
    if cp in JOINS_LEFT:     # the stroke flush to the left edge, so it meets the next letter
        cell = thicken_edge(cell, left=True, rows=rows)
    if cp in JOINS_RIGHT:
        cell = thicken_edge(cell, left=False, rows=rows)
    return cell


def thicken_edge(cell, left, rows=slice(None)):
    """Copy the edge column's join stroke one column inward if the edge is fainter, so joins meet."""
    c = cell.copy()
    e, i = (0, 1) if left else (-1, -2)
    c[rows, e] = np.maximum(c[rows, e], c[rows, i])
    return c


def used_codepoints(usrdir):
    import souls_formats as sf
    import fmg
    used = set()
    for b in ('menu.msgbnd', 'item.msgbnd'):
        p = os.path.join(usrdir, 'MSG', 'NA_ENGLISH', b)
        d = open(p, 'rb').read() if os.path.exists(p) else sf.dcx_decompress(open(p + '.dcx', 'rb').read())[0]
        for e in sf.Bnd3(d).entries:
            try:
                m = fmg.read(e['data'])
            except Exception:
                continue
            for s in m.values():
                if s:
                    used.update(map(ord, s))
    return used


def digit_cell(cp, st, H, w):
    """An Arabic-Indic digit centred in a cell w pixels wide (the width of the ASCII digit it stands in for)."""
    a = render(cp, st, H)
    xs = np.nonzero(a.max(axis=0) > 8)[0]
    a = a[:, xs.min():xs.max() + 1]
    if a.shape[1] > w:
        a = mf.resize(a, w, H)
    cell = np.zeros((H, w), np.uint8)
    x = (w - a.shape[1]) // 2
    cell[:, x:x + a.shape[1]] = a
    return cell


def build(ccm_path, tpf_path, out_ccm, out_tpf, fontname, used, preview=None, cps=ARABIC, digits=True):
    st = STYLE[fontname]
    c = Ccm(open(ccm_path, 'rb').read())
    tpfb = bytearray(open(tpf_path, 'rb').read())
    tpf = mf.sf.Tpf(bytes(tpfb))
    H = c.h
    tex_i = c.glyphs[c.index(ord('e'))][7]
    tex = tpf.textures[tex_i]
    TW, TH = tex['w'], tex['h']
    levels = mf.bc3_levels(tex['data'], TW, TH)
    page = mf.alpha_of(tex['data'], TW, TH, levels[0][2], levels[0][3])
    newpage = page.copy()

    cells = {}
    for cp in cps:
        r = render(cp, st, H)
        if r is not None:
            cells[cp] = r
    digit_entries = {}      # U+0660-0669 -> a copy of the ASCII digit's record (same cell, redrawn below)
    if digits:
        for k, cp in enumerate(DIGITS):
            g = list(c.glyphs[c.index(0x30 + k)])
            assert g[7] == tex_i, 'digits are expected on the text page'
            x, y, x2, y2 = round(g[0] * TW), round(g[1] * TH), round(g[2] * TW), round(g[3] * TH)
            assert y2 - y == H
            newpage[y:y2, x:x2] = digit_cell(cp, st, H, x2 - x)
            digit_entries[cp] = g

    # free space 1: the bottom of the page
    bottom = mf.ink_bbox(page, 0)[3]
    rects = []      # (x, y, w) free row segments of height H
    y = (bottom + 4 + 3) // 4 * 4
    while y + H <= TH:
        rects.append([1, y, TW - 2])
        y += H + 2
    # free space 2: cells of characters the US text never uses, on this page
    reuse = []
    seen = set()
    for a, b, gi0 in c.groups:
        for cp in range(a, b + 1):
            g = c.glyphs[gi0 + cp - a]
            if g[7] != tex_i or cp in used or (cp >> 8) not in REUSE_BLOCKS:
                continue
            x, yy = round(g[0] * TW), round(g[1] * TH)
            x2, y2 = round(g[2] * TW), round(g[3] * TH)
            if (x, yy) in seen or y2 - yy != H:
                continue
            seen.add((x, yy))
            reuse.append([x, yy, x2 - x])
    # a reused cell may be shared with a used character (two code points, one cell): drop those
    used_rects = set()
    for a, b, gi0 in c.groups:
        for cp in range(a, b + 1):
            if cp in used:
                g = c.glyphs[gi0 + cp - a]
                used_rects.add((round(g[0] * TW), round(g[1] * TH)))
    reuse = [r for r in reuse if (r[0], r[1]) not in used_rects]
    reuse.sort(key=lambda r: r[2])

    entries = {}
    order = sorted(cells, key=lambda cp: -cells[cp].shape[1])
    for cp in order:
        cell = cells[cp]
        w = cell.shape[1]
        spot = None
        for r in rects:
            if r[2] >= w:
                spot = (r[0], r[1])
                r[0] += w + 2
                r[2] -= w + 2
                break
        if spot is None:
            for k, r in enumerate(reuse):
                if r[2] >= w:
                    spot = (r[0], r[1])
                    newpage[r[1]:r[1] + H, r[0]:r[0] + r[2]] = 0
                    del reuse[k]
                    break
        if spot is None:
            raise SystemExit(f'{fontname}: no room for U+{cp:04X} ({w} px)')
        x, y = spot
        newpage[y:y + H, x:x + w] = cell
        # text is drawn in visual order, so this glyph's right edge meets the letter drawn after it (to its
        # right); a final / medial form joins there: overlap that neighbour so the join has no gap
        adv = w - st['overlap'] if cp in JOINS_RIGHT else w
        entries[cp] = [x / TW, y / TH, (x + w) / TW, (y + H) / TH, 0, w, adv, tex_i]

    n_letters = len(entries)
    entries.update(digit_entries)
    cps_sorted = sorted(entries)
    assert all(c.index(cp) is None for cp in cps_sorted)
    first = len(c.glyphs)
    for cp in cps_sorted:
        c.glyphs.append(entries[cp])
    runs = []
    for k, cp in enumerate(cps_sorted):
        if runs and runs[-1][1] == cp - 1:
            runs[-1][1] = cp
        else:
            runs.append([cp, cp, first + k])
    c.groups = sorted(c.groups + runs, key=lambda g: g[0])

    # re-encode only the blocks whose pixels changed, at every mip level
    data = bytearray(tex['data'])
    dirty = (newpage != page)
    lvl = newpage
    for li, (w, h, o, n) in enumerate(levels):
        if li > 0:
            p = lvl.astype(np.uint16)
            ph, pw = p.shape
            if ph > 1 and pw > 1:
                p = p[:ph // 2 * 2, :pw // 2 * 2]
                lvl = ((p[0::2, 0::2] + p[1::2, 0::2] + p[0::2, 1::2] + p[1::2, 1::2] + 2) // 4).astype(np.uint8)
            d = dirty[:dirty.shape[0] // 2 * 2, :dirty.shape[1] // 2 * 2]
            dirty = d[0::2, 0::2] | d[1::2, 0::2] | d[0::2, 1::2] | d[1::2, 1::2] if d.size else d
        bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
        for by in range(bh):
            for bx in range(bw):
                if not dirty[by * 4:by * 4 + 4, bx * 4:bx * 4 + 4].any():
                    continue
                blk = np.zeros((4, 4), int)
                sub = lvl[by * 4:by * 4 + 4, bx * 4:bx * 4 + 4]
                blk[:sub.shape[0], :sub.shape[1]] = sub
                off = o + (by * bw + bx) * 16
                data[off:off + 16] = mf.enc_alpha_block(blk) + mf.WHITE
    o = 0x10
    flag2 = tpfb[0x0D]
    toff = None
    for i in range(len(tpf.textures)):
        off, size = struct.unpack_from('>Ii', tpfb, o)
        if i == tex_i:
            toff = off
            assert size == len(data)
        o += 20 + (4 if flag2 else 0) + 8
    tpfb[toff:toff + len(data)] = data
    open(out_ccm, 'wb').write(c.write())
    open(out_tpf, 'wb').write(bytes(tpfb))
    if preview:
        dec = mf.alpha_of(bytes(data), TW, TH, 0, levels[0][3])
        Image.fromarray(dec).save(preview)
    return n_letters, len(reuse)

"""Add French letters (accented Latin, oe/ae ligatures, guillemets) to Demon's Souls NA_ENGLISH fonts.
Glyphs are composed from the font's own letters and accent marks; new cells go into the empty bottom of the
glyph page; the CCM gets new code groups + glyph entries appended (existing indices unchanged)."""
import sys, struct
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))   # the repo's tools/souls_formats.py
import souls_formats as sf
import numpy as np
from PIL import Image
from ccm import Ccm

ACC = {'grave': '`', 'acute': '\u00b4', 'circ': '^', 'diaer': '\u00a8'}
LOWER = {'à': ('a', 'grave'), 'â': ('a', 'circ'), 'ä': ('a', 'diaer'), 'é': ('e', 'acute'), 'è': ('e', 'grave'),
         'ê': ('e', 'circ'), 'ë': ('e', 'diaer'), 'î': ('i', 'circ'), 'ï': ('i', 'diaer'), 'ô': ('o', 'circ'),
         'ö': ('o', 'diaer'), 'ù': ('u', 'grave'), 'û': ('u', 'circ'), 'ü': ('u', 'diaer'), 'ÿ': ('y', 'diaer')}
UPPER = {k.upper(): (v[0].upper(), v[1]) for k, v in LOWER.items() if k != 'ÿ'}
UPPER['Ÿ'] = ('Y', 'diaer')


def bc3_levels(data, w, h):
    levels, o = [], 0
    while True:
        bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
        n = bw * bh * 16
        levels.append((w, h, o, n)); o += n
        if w == 1 and h == 1:
            break
        w, h = max(1, w // 2), max(1, h // 2)
    assert o == len(data), (o, len(data))
    return levels


def alpha_of(data, w, h, o, n):
    rgba = sf.decode_bc(data[o:o + n], w, h, 3)
    return np.frombuffer(rgba, np.uint8).reshape(h, w, 4)[:, :, 3].copy()


def enc_alpha_block(a):
    a = a.flatten().astype(int)
    hi, lo = int(a.max()), int(a.min())
    if hi == lo:
        return bytes([hi, lo]) + b'\0' * 6
    pal = [hi, lo] + [((7 - k) * hi + k * lo + 3) // 7 for k in range(1, 7)]
    bits = 0
    for i, v in enumerate(a):
        j = min(range(8), key=lambda j: abs(pal[j] - v))
        bits |= j << (3 * i)
    return bytes([hi, lo]) + bits.to_bytes(6, 'little')


WHITE = bytes([0xFF, 0xFF, 0xFF, 0xFF, 0, 0, 0, 0])


def encode_rows(alpha, y0):
    """BC3 blocks for block rows from y0 (multiple of 4) to the end; white colour."""
    h, w = alpha.shape
    out = bytearray()
    for by in range(y0 // 4, max(1, (h + 3) // 4)):
        for bx in range(max(1, (w + 3) // 4)):
            blk = np.zeros((4, 4), int)
            sub = alpha[by * 4:by * 4 + 4, bx * 4:bx * 4 + 4]
            blk[:sub.shape[0], :sub.shape[1]] = sub
            out += enc_alpha_block(blk) + WHITE
    return out


def ink_bbox(cell, thr=24):
    ys, xs = np.nonzero(cell > thr)
    if not len(ys):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def resize(a, w, h):
    return np.asarray(Image.fromarray(a).resize((max(1, w), max(1, h)), Image.LANCZOS)).astype(np.uint8)


def put(dst, src, x, y):
    h, w = src.shape
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(dst.shape[1], x + w), min(dst.shape[0], y + h)
    if x1 > x0 and y1 > y0:
        dst[y0:y1, x0:x1] = np.maximum(dst[y0:y1, x0:x1], src[y0 - y:y1 - y, x0 - x:x1 - x])


def build(ccm_path, tpf_path, out_ccm, out_tpf, preview=None):
    c = Ccm(open(ccm_path, 'rb').read())
    tpfb = bytearray(open(tpf_path, 'rb').read())
    tpf = sf.Tpf(bytes(tpfb))
    H = c.h
    tex_i = c.glyphs[c.index(ord('e'))][7]
    tex = tpf.textures[tex_i]
    TW, TH = tex['w'], tex['h']
    levels = bc3_levels(tex['data'], TW, TH)
    page = alpha_of(tex['data'], TW, TH, levels[0][2], levels[0][3])
    used_bottom = ink_bbox(page, 0)[3]

    def cell_of(ch):
        g = c.glyphs[c.index(ord(ch))]
        x, y = round(g[0] * TW), round(g[1] * TH)
        x2, y2 = round(g[2] * TW), round(g[3] * TH)
        return page[y:y2, x:x2].astype(np.uint8).copy(), g

    xh_top = ink_bbox(cell_of('x')[0])[1]
    base = ink_bbox(cell_of('x')[0])[3]

    def accent(name, max_h=None):
        cell, _ = cell_of(ACC[name])
        x0, y0, x1, y1 = ink_bbox(cell)
        a = cell[y0:y1, x0:x1]
        if name == 'circ':   # ASCII ^ is big: scale to the acute's height
            ac = cell_of(ACC['acute'])[0]
            ah = ink_bbox(ac)
            th = ah[3] - ah[1]
            nw = min(round(a.shape[1] * th / a.shape[0] * 1.6), (ah[2] - ah[0]) * 2 + 1)
            a = resize(a, max(3, nw), th)
        if max_h and a.shape[0] > max_h:
            a = resize(a, max(2, a.shape[1] if name == 'diaer' else round(a.shape[1] * 0.9)), max_h)
        return a

    new = {}   # ch -> (cell array, pre, width, adv)
    for ch, (b, acc) in LOWER.items():
        cell, g = cell_of(b)
        if b == 'i':                     # dotless i: drop the dot above the x-height
            cell[:xh_top - 1, :] = 0
        bx0, _, bx1, _ = ink_bbox(cell)
        a = accent(acc)
        ay1 = xh_top - 1                 # one clear row above the x-height
        put(cell, a, round((bx0 + bx1) / 2 - a.shape[1] / 2), ay1 - a.shape[0])
        new[ch] = (cell, g[4], g[5], g[6])
    for ch, (b, acc) in UPPER.items():
        cell, g = cell_of(b)
        x0, y0, x1, y1 = ink_bbox(cell)
        room = 3 if acc != 'diaer' else 2                # accent rows above the squashed capital
        top = room + 1
        squ = resize(cell[y0:y1, :], cell.shape[1], y1 - top)
        cell2 = np.zeros_like(cell)
        put(cell2, squ, 0, top)
        a = accent(acc, max_h=room)
        put(cell2, a, round((x0 + x1) / 2 - a.shape[1] / 2), top - 1 - a.shape[0])
        new[ch] = (cell2, g[4], g[5], g[6])
    # cedilla: the comma's tail under c / C
    com, _ = cell_of(',')
    cx0, cy0, cx1, cy1 = ink_bbox(com)
    tail = resize(com[cy0:cy1, cx0:cx1], max(2, round((cx1 - cx0) * 0.8)), max(3, round((cy1 - cy0) * 0.7)))
    for ch, b in (('ç', 'c'), ('Ç', 'C')):
        cell, g = cell_of(b)
        x0, y0, x1, y1 = ink_bbox(cell)
        put(cell, tail, round((x0 + x1) / 2 - tail.shape[1] / 2), y1 - 1)
        new[ch] = (cell, g[4], g[5], g[6])
    # ligatures
    for ch, (l, r) in (('œ', 'oe'), ('æ', 'ae'), ('Œ', 'OE'), ('Æ', 'AE')):
        lc, lg = cell_of(l)
        rc, rg = cell_of(r)
        lx0, _, lx1, _ = ink_bbox(lc)
        rx0, _, rx1, _ = ink_bbox(rc)
        ov = 2 if l.islower() else 3
        w = (lx1 - lx0) + (rx1 - rx0) - ov + 2
        cell = np.zeros((H, w), np.uint8)
        put(cell, lc[:, lx0:lx1], 1, 0)
        put(cell, rc[:, rx0:rx1], 1 + (lx1 - lx0) - ov, 0)
        new[ch] = (cell, lg[4], w, w + max(0, lg[6] - lg[5]))
    # guillemets from < and >
    for ch, b in (('«', '<'), ('»', '>')):
        bc, g = cell_of(b)
        x0, y0, x1, y1 = ink_bbox(bc)
        s = resize(bc[y0:y1, x0:x1], max(3, round((x1 - x0) * 0.55)), max(3, round((y1 - y0) * 0.6)))
        w = s.shape[1] * 2 + 2
        cell = np.zeros((H, w), np.uint8)
        mid = (xh_top + base) // 2
        put(cell, s, 1, mid - s.shape[0] // 2)
        put(cell, s, s.shape[1], mid - s.shape[0] // 2)
        new[ch] = (cell, 1, w, w + 1)

    # place the new cells into the empty bottom of the page
    x, y = 1, (used_bottom + 4 + 3) // 4 * 4
    newpage = page.copy()
    entries = {}
    for ch in sorted(new, key=ord):
        cell, pre, wid, adv = new[ch]
        cw = cell.shape[1]
        if x + cw + 1 > TW:
            x, y = 1, y + H + 2
        assert y + H <= TH, 'page full'
        newpage[y:y + H, x:x + cw] = cell
        entries[ch] = [x / TW, y / TH, (x + cw) / TW, (y + H) / TH, pre, wid, adv, tex_i]
        x += cw + 2
    cps = sorted(ord(ch) for ch in entries)
    assert all(c.index(cp) is None for cp in cps), [hex(cp) for cp in cps if c.index(cp) is not None]
    first = len(c.glyphs)
    for cp in cps:
        c.glyphs.append(entries[chr(cp)])
    runs = []
    for k, cp in enumerate(cps):
        if runs and runs[-1][1] == cp - 1:
            runs[-1][1] = cp
        else:
            runs.append([cp, cp, first + k])
    c.groups = sorted(c.groups + runs, key=lambda g: g[0])
    # texture: re-encode only the changed block rows of each level
    y0 = (used_bottom // 4) * 4
    data = bytearray(tex['data'])
    lvl = newpage
    for li, (w, h, o, n) in enumerate(levels):
        if li > 0:
            p = lvl.astype(np.uint16)
            ph, pw = p.shape
            if ph > 1 and pw > 1:
                p = p[:ph // 2 * 2, :pw // 2 * 2]
                lvl = ((p[0::2, 0::2] + p[1::2, 0::2] + p[0::2, 1::2] + p[1::2, 1::2] + 2) // 4).astype(np.uint8)
        ly0 = ((y0 >> li) // 4) * 4
        bw = max(1, (w + 3) // 4)
        blob = encode_rows(lvl, ly0)
        start = o + (ly0 // 4) * bw * 16
        assert start + len(blob) == o + n, (li, start, len(blob), o, n)
        data[start:start + len(blob)] = blob
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
        dec = alpha_of(bytes(data), TW, TH, 0, levels[0][3])
        crop = dec[max(0, y0 - 56):min(TH, y + H + 4), :]
        Image.fromarray(crop).resize((crop.shape[1] * 3, crop.shape[0] * 3), Image.NEAREST).save(preview)
    return len(entries), runs


if __name__ == '__main__':
    src, dst = sys.argv[1], sys.argv[2]
    for f in ('dsfont24', 'talkfont24'):
        n, runs = build(f'{src}/{f}.ccm', f'{src}/{f}.tpf', f'{dst}/{f}.ccm', f'{dst}/{f}.tpf',
                        preview=f'{dst}/{f}_preview.png')
        print(f, n, 'glyphs added,', len(runs), 'code groups')

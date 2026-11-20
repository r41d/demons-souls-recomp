"""The HUD's hand labels in Arabic: "L" -> يس (يسار, left) and "R" -> يم (يمين, right), as the reviewers asked.

They are not text: menu.drb draws them as 19x19 sprites cut from the menu texture "Menu08" (BC3), a cream letter
on a dark box, at (79,404)-(98,423) for L and (102,404)-(121,423) for R. Here the letter is wiped to the box colour
and the Arabic is drawn in the letter's own cream, from Noto Sans Arabic; only the BC3 blocks that change are
re-encoded (the texture has one mip level).

  python make_ar_hud.py <menu.tpf> <out.tpf> [preview.png]
"""
import os
import struct
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'shaping'))
sys.path.insert(0, os.path.join(HERE, '..', 'demons-souls-recomp', 'tools'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools'))
import souls_formats as sf  # noqa: E402
import arshape              # noqa: E402

FONT = next(p for p in (os.path.join(HERE, 'fonts', 'NotoSansArabic.ttf'),
                        os.path.join(HERE, 'source', 'NotoSansArabic[wght].ttf')) if os.path.exists(p))
LABELS = [((79, 404, 98, 423), 'يس'), ((102, 404, 121, 423), 'يم')]
SS = 6      # drawn at 6x, then scaled down


def draw_label(cell, text):
    """cell: HxWx4 uint8 of the original sprite. Returns the cell with the letter replaced by `text`."""
    rgb = cell[..., :3].astype(np.float32)
    lum = rgb.mean(axis=2)
    inside = cell[..., 3] > 180
    dark = inside & (lum < 40)
    box = np.median(rgb[dark], axis=0)                            # the dark box (translucent)
    box_a = float(np.median(cell[..., 3][dark]))
    ink = np.median(rgb[inside & (lum > 150)], axis=0)           # the cream letter (opaque)
    out = cell.copy().astype(np.float32)
    out[..., :3][inside] = box                                    # wipe the letter: colour and opacity
    out[..., 3][inside] = box_a
    h, w = cell.shape[:2]
    ys, xs = np.nonzero(inside)
    ix0, ix1, iy0, iy1 = xs.min() + 1, xs.max(), ys.min() + 2, ys.max() - 1
    bw_, bh_ = (ix1 - ix0) * SS, (iy1 - iy0) * SS
    # shaped and already in left-to-right drawing order: draw it as is (BASIC layout; Raqm would reverse it again)
    vis = arshape.visual(arshape.shape(text))
    f = ImageFont.truetype(FONT, 16 * SS, layout_engine=ImageFont.Layout.BASIC)
    try:
        f.set_variation_by_axes([700])
    except Exception:
        pass
    tmp = Image.new('L', (40 * SS, 30 * SS), 0)
    td = ImageDraw.Draw(tmp)
    bb = td.textbbox((4 * SS, 4 * SS), vis, font=f)
    td.text((4 * SS, 4 * SS), vis, font=f, fill=255)
    glyph = tmp.crop(bb)
    # fill the box's height; narrow the word (at most to 70 %) when it is wider than the box, so the dots stay big
    gh = bh_
    gw = round(glyph.width * gh / glyph.height)
    if gw > bw_:
        gw2 = max(bw_, round(gw * 0.7))
        if gw2 > bw_:
            gh = round(gh * bw_ / gw2)
            gw2 = bw_
        gw = gw2
    glyph = glyph.resize((gw, gh), Image.LANCZOS)
    big = Image.new('L', (w * SS, h * SS), 0)
    big.paste(glyph, (round((ix0 + ix1) / 2 * SS - gw / 2), round((iy0 + iy1) / 2 * SS - gh / 2)))
    m = np.asarray(big.resize((w, h), Image.LANCZOS)).astype(np.float32)[..., None] / 255
    m = m * inside[..., None]
    out[..., :3] = out[..., :3] * (1 - m) + ink * m
    out[..., 3] = out[..., 3] * (1 - m[..., 0]) + 255 * m[..., 0]
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def _565(c):
    r, g, b = (int(round(v)) for v in c)
    return ((r * 31 + 127) // 255) << 11 | ((g * 63 + 127) // 255) << 5 | ((b * 31 + 127) // 255)


def _un565(v):
    return np.array([((v >> 11) & 31) * 255 / 31, ((v >> 5) & 63) * 255 / 63, (v & 31) * 255 / 31])


def enc_color(px):
    """16 RGB pixels -> 8-byte BC1 colour block, 4-colour mode (endpoints along the brightness range)."""
    px = px.astype(np.float32)
    lum = px @ np.array([0.299, 0.587, 0.114])
    hi, lo = px[lum.argmax()], px[lum.argmin()]
    c0, c1 = _565(hi), _565(lo)
    if c0 == c1:
        return struct.pack('<HHI', c0, c1, 0)
    if c0 < c1:
        c0, c1 = c1, c0
    p0, p1 = _un565(c0), _un565(c1)
    pal = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3])
    idx = ((px[:, None, :] - pal[None]) ** 2).sum(axis=2).argmin(axis=1)
    bits = 0
    for i, j in enumerate(idx):
        bits |= int(j) << (2 * i)
    return struct.pack('<HHI', c0, c1, bits)


def enc_alpha(a):
    a = a.astype(int)
    hi, lo = int(a.max()), int(a.min())
    if hi == lo:
        return bytes([hi, lo]) + b'\0' * 6
    pal = [hi, lo] + [((7 - k) * hi + k * lo + 3) // 7 for k in range(1, 7)]
    bits = 0
    for i, v in enumerate(a):
        j = min(range(8), key=lambda j: abs(pal[j] - v))
        bits |= j << (3 * i)
    return bytes([hi, lo]) + bits.to_bytes(6, 'little')


def build(src_tpf, out_tpf, preview=None):
    tpfb = bytearray(open(src_tpf, 'rb').read())
    tpf = sf.Tpf(bytes(tpfb))
    ti = next(i for i, t in enumerate(tpf.textures) if t['name'] == 'Menu08')
    t = tpf.textures[ti]
    assert t['format'] == 5 and t['mips'] == 1, (t['format'], t['mips'])
    w, h, rgba = sf.decode_texture(t)
    img = np.frombuffer(bytes(rgba), np.uint8).reshape(h, w, 4).copy()
    new = img.copy()
    for (x0, y0, x1, y1), text in LABELS:
        new[y0:y1, x0:x1] = draw_label(img[y0:y1, x0:x1], text)
    data = bytearray(t['data'])
    bw = (w + 3) // 4
    dirty = (new != img).any(axis=2)
    for by in range(h // 4):
        for bx in range(bw):
            if not dirty[by * 4:by * 4 + 4, bx * 4:bx * 4 + 4].any():
                continue
            blk = new[by * 4:by * 4 + 4, bx * 4:bx * 4 + 4].reshape(16, 4)
            off = (by * bw + bx) * 16
            data[off:off + 16] = enc_alpha(blk[:, 3]) + enc_color(blk[:, :3])
    o = 0x10
    flag2 = tpfb[0x0D]
    for i in range(len(tpf.textures)):
        off, size = struct.unpack_from('>Ii', tpfb, o)
        if i == ti:
            assert size == len(data)
            tpfb[off:off + size] = data
        o += 20 + (4 if flag2 else 0) + 8
    open(out_tpf, 'wb').write(bytes(tpfb))
    if preview:
        w2, h2, rgba2 = sf.decode_texture(sf.Tpf(bytes(tpfb)).textures[ti])
        a = np.frombuffer(bytes(rgba2), np.uint8).reshape(h2, w2, 4)
        Image.fromarray(np.concatenate([img[396:432, 70:130], a[396:432, 70:130]], axis=0)).resize(
            (480, 576), Image.NEAREST).save(preview)


if __name__ == '__main__':
    build(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)

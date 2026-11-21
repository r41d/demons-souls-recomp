"""The big centre-screen banners in Arabic: YOU DIED, YOU REVIVED, THE DEMON WAS DESTROYED, YOU REGAINED LOST SOUL,
TARGET WAS DESTROYED, BLACK PHANTOM WAS DESTROYED.

They are pictures, not text: 1024x128 BC1 textures Menu14 - Menu18 in MENU/menu.tpf, one flat colour on black (the
game blends them so black does not show). menu.drb shows Menu15 as two sprites, (0,0)-(440,128) for YOU DIED and
(440,0)-(1024,128) for YOU REVIVED; the others are shown whole. Each phrase is drawn in its own area in Noto Naskh
Arabic, in the English lettering's colour, centred where the English was and on its baseline, one size for all (the
alef as tall as the English capitals), narrowed when a phrase is wider than its area. Wording by the reviewers (Zack).

  python make_ar_banners.py <menu.tpf> <out.tpf> [preview.png]
"""
import os
import struct
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'demons-souls-recomp', 'tools'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools'))
import souls_formats as sf  # noqa: E402

NASKH = next(p for p in (os.path.join(HERE, 'fonts', 'NotoNaskhArabic.ttf'),
                         os.path.join(HERE, 'source', 'NotoNaskhArabic[wght].ttf')) if os.path.exists(p))
# texture -> [(x0, x1) area of the texture, Arabic]
BANNERS = {
    'Menu15': [((0, 440), 'لقد مت'),                       # YOU DIED
               ((440, 1024), 'ثم بعثت')],                     # YOU REVIVED
    'Menu14': [((0, 1024), 'تدمر الشيطان')],               # THE DEMON WAS DESTROYED
    'Menu16': [((0, 1024), 'إسترجعت الأرواح المفقودة')],   # YOU REGAINED LOST SOUL
    'Menu17': [((0, 1024), 'تدمر الهدف')],                 # TARGET WAS DESTROYED
    'Menu18': [((0, 1024), 'تدمر الطيف الأسود')],          # BLACK PHANTOM WAS DESTROYED
}
SS = 4          # drawn at 4x, then scaled down
ALEF = 1.0      # the alef's height / the English capitals' height
WGHT = 500      # Naskh weight (400-700)
MARGIN = 12     # px kept free at the ends of an area


def font(size):
    f = ImageFont.truetype(NASKH, size, layout_engine=ImageFont.Layout.RAQM)
    try:
        f.set_variation_by_axes([WGHT])
    except Exception:
        pass
    return f


def alef_height(size):
    """Ink height of an alef at this size: the Arabic is sized so its alef is as tall as the English capitals."""
    f = font(size)
    im = Image.new('L', (size * 2, size * 3), 0)
    ImageDraw.Draw(im).text((size // 2, size * 2), 'ا', font=f, fill=255, anchor='ls')
    b = im.getbbox()
    return b[3] - b[1]


def draw_area(rgb, x0, x1, text):
    """rgb: (128, 1024, 3) float. Replaces the English in columns x0-x1 with text: same colour, centred where the
    English was, on its baseline, the alef as tall as the capitals; narrowed (whole) when wider than the area."""
    a = rgb[:, x0:x1]
    lum = a.max(axis=2)
    on = lum > 0.5 * lum.max()
    ys, xs = np.nonzero(on)
    col = np.median(a[on], axis=0) / max(1e-6, float(np.median(lum[on])))   # colour at full brightness
    col = col * float(np.percentile(lum[on], 90))
    base = ys.max() + 1                              # the English baseline (capitals: no descenders)
    eh = base - ys.min()                             # the capitals' height
    cx = (xs.min() + xs.max() + 1) / 2
    size = round(1000 * eh * SS * ALEF / alef_height(1000))
    f = font(size)
    W, H = (x1 - x0) * SS * 3, 128 * SS * 2
    big = Image.new('L', (W, H), 0)
    by = 128 * SS                                    # the baseline in big
    ImageDraw.Draw(big).text((W // 2, by), text, font=f, fill=255, anchor='ms', direction='rtl', language='ar')
    l, t, r, b = big.getbbox()
    room = ((x1 - x0) - 2 * MARGIN) * SS
    k = min(1.0, room / (r - l))
    g = big.crop((l, 0, r, H))
    if k < 1:
        g = g.resize((round(g.width * k), round(H * k)), Image.LANCZOS)
    out = Image.new('L', ((x1 - x0) * SS, 128 * SS), 0)
    # the ink centred on the English centre, the baseline on the English baseline
    out.paste(g, (round(cx * SS - g.width / 2), round(base * SS - by * k)))
    m = np.asarray(out.resize((x1 - x0, 128), Image.LANCZOS)).astype(np.float32)[..., None] / 255
    rgb[:, x0:x1] = m * col


def bc1_encode(rgb):
    """(h, w, 3) uint8 -> BC1, 4-colour blocks, endpoints the block's brightest and darkest pixel (text on black: the
    colour line runs from black to the lettering's colour)."""
    h, w, _ = rgb.shape
    b = rgb.reshape(h // 4, 4, w // 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 3).astype(np.int32)
    lum = b @ np.array([299, 587, 114])
    n = np.arange(len(b))
    hi, lo = b[n, lum.argmax(axis=1)], b[n, lum.argmin(axis=1)]

    def p565(c):
        r, g, bb = ((c[:, 0] * 31 + 127) // 255, (c[:, 1] * 63 + 127) // 255, (c[:, 2] * 31 + 127) // 255)
        return (r << 11) | (g << 5) | bb, np.stack([r * 255 / 31, g * 255 / 63, bb * 255 / 31], axis=1)

    c0, e0 = p565(hi)
    c1, e1 = p565(lo)
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    e0, e1 = np.where(swap[:, None], e1, e0), np.where(swap[:, None], e0, e1)
    pal = np.stack([e0, e1, (2 * e0 + e1) / 3, (e0 + 2 * e1) / 3], axis=1)          # (n, 4, 3)
    idx = ((b[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(axis=3).argmin(axis=2)
    same = c0 == c1                                     # flat block: 4-colour mode needs c0 > c1
    c1 = np.where(same & (c0 > 0), c0 - 1, c1)
    c0 = np.where(same & (c0 == 0), 1, c0)
    idx = np.where(same[:, None], np.where(c0[:, None] == 1, 1, 0), idx)
    bits = (idx.astype(np.uint64) << (2 * np.arange(16, dtype=np.uint64))).sum(axis=1)
    out = np.zeros((len(b), 8), np.uint8)
    out[:, 0], out[:, 1] = c0 & 255, c0 >> 8
    out[:, 2], out[:, 3] = c1 & 255, c1 >> 8
    for k in range(4):
        out[:, 4 + k] = (bits >> np.uint64(8 * k)) & np.uint64(255)
    return out.tobytes()


def build(src_tpf, out_tpf, preview=None):
    tpfb = bytearray(open(src_tpf, 'rb').read())
    tpf = sf.Tpf(bytes(tpfb))
    rows = []
    for name, areas in BANNERS.items():
        ti = next(i for i, t in enumerate(tpf.textures) if t['name'] == name)
        t = tpf.textures[ti]
        assert t['format'] == 0 and (t['w'], t['h'], t['mips']) == (1024, 128, 1), (name, t['format'], t['w'], t['h'])
        w, h, rgba = sf.decode_texture(t)
        orig = np.frombuffer(bytes(rgba), np.uint8).reshape(h, w, 4)[..., :3].copy()
        rgb = orig.astype(np.float32)
        for (x0, x1), text in areas:
            draw_area(rgb, x0, x1, text)
        new = np.clip(rgb + 0.5, 0, 255).astype(np.uint8)
        data = bc1_encode(new)
        assert len(data) == len(t['data'])
        o = 0x10
        flag2 = tpfb[0x0D]
        for i in range(len(tpf.textures)):
            off, size = struct.unpack_from('>Ii', tpfb, o)
            if i == ti:
                tpfb[off:off + size] = data
            o += 20 + (4 if flag2 else 0) + 8
        rows.append((orig, name, ti))
    open(out_tpf, 'wb').write(bytes(tpfb))
    if preview:
        done = sf.Tpf(bytes(tpfb))
        sheet = Image.new('RGB', (2048 + 8, 128 * len(rows)))
        for k, (orig, name, ti) in enumerate(rows):
            w, h, rgba = sf.decode_texture(done.textures[ti])
            sheet.paste(Image.fromarray(orig), (0, 128 * k))
            sheet.paste(Image.frombytes('RGBA', (w, h), bytes(rgba)).convert('RGB'), (1032, 128 * k))
        sheet.save(preview)


if __name__ == '__main__':
    build(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)

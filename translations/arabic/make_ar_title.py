"""The title-screen logo in Arabic (ديمونز سولز), drawn into the game's own MENU/menu.tpf texture "Title".

  python make_ar_title.py <menu.tpf> <out.tpf> [preview.png]

The logo is one full-screen layer in menu.drb: Sprite (0,0)-(1024,720) shows texture (0,0)-(1024,720), and Sprite
(1024,0)-(1280,720) shows texture (0,729)-(720,985) turned a quarter turn (the right 256 columns stored on their
side). The title screen's own animation (fade, glow) works on those two sprites, so it is kept as it is.
Black background, opaque, BC1 (DXT1) like the original. Lettering: Noto Naskh Arabic (SIL OFL 1.1, on this PC),
worn down with procedural cracks and specks in the spirit of the original's distressed lettering.
"""
import os
import struct
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools'))
import souls_formats as sf  # noqa: E402

NASKH = [os.path.join(HERE, 'fonts', 'NotoNaskhArabic.ttf')]
W, H = 1280, 720


def font(size, wght=700):
    path = next(p for p in NASKH if os.path.exists(p))
    f = ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)
    try:
        f.set_variation_by_axes([wght])
    except Exception:
        pass
    return f


def noise(shape, scale, rng):
    """Smooth value noise in 0..1 at about `scale` pixels per feature."""
    h, w = shape
    sh, sw = max(2, h // scale + 2), max(2, w // scale + 2)
    g = Image.fromarray((rng.random((sh, sw)) * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
    return np.asarray(g).astype(np.float32) / 255


def text_mask(words):
    """(alpha 0..255 float array, (x0, y0, x1, y1)) of the logo lettering on the 1280x720 screen."""
    im = Image.new('L', (W, H), 0)
    dr = ImageDraw.Draw(im)
    for text, size, (x, y) in words:
        dr.text((x, y), text, font=font(size), fill=255, anchor='rs', direction='rtl', language='ar')
    a = np.asarray(im).astype(np.float32)
    ys, xs = np.nonzero(a > 0)
    return a, (xs.min(), ys.min(), xs.max(), ys.max())


def distress(a, rng):
    """Worn, cracked lettering: rough edges, specks and pits inside, thin dark cracks, a little bloom."""
    m = a / 255
    # rough edges: erode where coarse noise is low
    edge = Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2.2))
    e = np.asarray(edge).astype(np.float32) / 255
    rough = noise(m.shape, 9, rng) * 0.55 + noise(m.shape, 3, rng) * 0.45
    m = np.clip((e - 0.32 - (rough - 0.5) * 0.55) * 5.5, 0, 1) * (m > 0.02)
    # pits and specks inside the strokes
    pits = noise(m.shape, 2, rng)
    m = m * np.where(pits > 0.83, 0.15, 1.0)
    blot = noise(m.shape, 14, rng)
    m = m * np.clip(0.62 + blot * 0.6, 0, 1)
    # cracks: random walks of dark thin lines
    cr = Image.new('L', (W, H), 0)
    d = ImageDraw.Draw(cr)
    ys, xs = np.nonzero(m > 0.5)
    for _ in range(26):
        k = rng.integers(len(ys))
        x, y = float(xs[k]), float(ys[k])
        ang = rng.uniform(0, np.pi)
        for _ in range(int(rng.integers(6, 22))):
            ang += rng.normal(0, 0.45)
            step = rng.uniform(3, 7)
            nx, ny = x + np.cos(ang) * step, y + np.sin(ang) * step
            d.line((x, y, nx, ny), fill=255, width=1)
            x, y = nx, ny
    c = np.asarray(cr).astype(np.float32) / 255
    m = m * (1 - c * 0.75)
    # fine dark specks, the original's main texture
    fine = noise(m.shape, 1, rng)
    m = m * np.where(fine > 0.9, 0.35, 1.0) * np.where(fine < 0.04, 0.6, 1.0)
    # faint grain around the letters, as on the original's scratched texture
    grain = (noise(m.shape, 1, rng) > 0.985) * (np.asarray(edge).astype(np.float32) / 255 > 0.05) * 0.55
    m = np.maximum(m, grain)
    tone = 0.86 + noise(m.shape, 30, rng) * 0.14
    return np.clip(m * tone, 0, 1)


def logo(seed=7):
    rng = np.random.default_rng(seed)
    # like the original: the first word high and wide, the second lower and offset, read right to left
    words = [('ديمونز', 215, (1120, 250)), ('سولز', 215, (830, 440))]
    a, box = text_mask(words)
    m = distress(a, rng)
    img = Image.fromarray((m * 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    tm = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 18) \
        if os.path.exists('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf') else ImageFont.load_default()
    d.text((box[0] - 8, box[3] - 20), 'TM', font=tm, fill=210, anchor='rs')
    return img


def bc1_encode(rgb):
    """Grey RGB (h, w) uint8 -> BC1 blocks as stored on PC / PS3 (little-endian 565 endpoints)."""
    h, w = rgb.shape
    b = rgb.reshape(h // 4, 4, w // 4, 4).transpose(0, 2, 1, 3).reshape(-1, 16).astype(np.int32)
    lo, hi = b.min(axis=1), b.max(axis=1)

    def g565(v):
        r5, g6 = (v * 31 + 127) // 255, (v * 63 + 127) // 255
        return (r5 << 11) | (g6 << 5) | r5, r5 * 255 // 31, g6 * 255 // 63

    c0, r0, gg0 = g565(hi)
    c1, r1, gg1 = g565(lo)
    v0, v1 = (r0 + gg0 + r0) / 3, (r1 + gg1 + r1) / 3
    pal = np.stack([v0, v1, (2 * v0 + v1) / 3, (v0 + 2 * v1) / 3], axis=1)
    idx = np.abs(b[:, :, None] - pal[:, None, :]).argmin(axis=2)
    same = c0 <= c1               # flat block: 4-colour mode needs c0 > c1
    c0 = np.where(same, np.maximum(c0, c1), c0)
    c1 = np.where(same, np.where(c0 > 0, c0 - 1, 0), c1)
    idx = np.where(same[:, None], 0, idx)
    bits = (idx << (2 * np.arange(16))).sum(axis=1).astype(np.uint32)
    out = np.zeros((len(b), 8), np.uint8)
    out[:, 0], out[:, 1] = c0 & 255, c0 >> 8
    out[:, 2], out[:, 3] = c1 & 255, c1 >> 8
    for k in range(4):
        out[:, 4 + k] = (bits >> (8 * k)) & 255
    return out.tobytes()


def build(src_tpf, out_tpf, preview=None):
    tpfb = bytearray(open(src_tpf, 'rb').read())
    tpf = sf.Tpf(bytes(tpfb))
    ti = next(i for i, t in enumerate(tpf.textures) if t['name'] == 'Title')
    t = tpf.textures[ti]
    assert t['format'] == 0 and t['w'] == 1024 and t['h'] == 1024, (t['format'], t['w'], t['h'])
    art = os.path.join(HERE, 'title_logo_ar.png')   # the logo picture (logo_art.py), or hand-made art
    if os.path.exists(art):
        im = Image.open(art)
        if im.mode in ('RGBA', 'LA'):
            bg = Image.new('RGBA', im.size, (0, 0, 0, 255))
            bg.alpha_composite(im.convert('RGBA'))
            im = bg
        scr = np.asarray(im.convert('L').resize((W, H), Image.LANCZOS))
    else:
        import logo_art
        scr = np.asarray(logo_art.logo())
    page = np.zeros((1024, 1024), np.uint8)
    page[0:720, 0:1024] = scr[:, :1024]
    page[729:985, 0:720] = np.asarray(Image.fromarray(scr[:, 1024:1280]).transpose(Image.ROTATE_270))
    data = bc1_encode(page)
    assert len(data) <= len(t['data'])
    o = 0x10
    flag2 = tpfb[0x0D]
    for i in range(len(tpf.textures)):
        off, size = struct.unpack_from('>Ii', tpfb, o)
        if i == ti:
            tpfb[off:off + len(data)] = data
        o += 20 + (4 if flag2 else 0) + 8
    open(out_tpf, 'wb').write(bytes(tpfb))
    if preview:
        w, h, rgba = sf.decode_texture(sf.Tpf(bytes(tpfb)).textures[ti])
        im = Image.frombytes('RGBA', (w, h), bytes(rgba)).convert('L')
        s = Image.new('L', (W, H))
        s.paste(im.crop((0, 0, 1024, 720)), (0, 0))
        s.paste(im.crop((0, 729, 720, 985)).transpose(Image.ROTATE_90), (1024, 0))
        s.save(preview)


if __name__ == '__main__':
    build(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)

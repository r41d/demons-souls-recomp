"""The Arabic title logo, drawn to follow the original's look (used by make_ar_title.py when there is no hand-made
title_logo_ar.png). All procedural, from an OFL font (Noto Naskh Arabic); nothing from the game is used.

How it imitates the original logo:
- chiselled letters: each outline is traced and straightened into flat facets (Douglas-Peucker), like cut stone;
- blade strokes: a sweeping tapered swash from the first letter over the word (the original's "D"), the ل of سولز
  drawn up into a tall blade point (its sword-like "l"; no cross-guard, which on a letter would read as a cross), and the final ز's tail drawn out into a long spike (its "S");
- the two words on a diagonal, the second tucked under the end of the first, inside the original's box
  (x 128-1153, y 72-497 of 1280x720), small TM at the end;
- surface: bright white with clusters of dark specks near the edges, and chips out of the edges;
- breaks: short cuts across single strokes with that piece nudged, from logo_cuts.json (made with the cut editor
  page; apply_cuts is the same rule the page uses).
"""
import json
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT = os.path.join(HERE, 'fonts', 'NotoNaskhArabic.ttf')
W, H = 1280, 720
S = 2                      # work at 2x, then scale down
BOX = (128, 72, 1153, 497)  # the original logo's box on the 1280x720 screen


def _font(size):
    f = ImageFont.truetype(FONT, size * S, layout_engine=ImageFont.Layout.RAQM)
    try:
        f.set_variation_by_axes([700])
    except Exception:
        pass
    return f


def _word(text, size):
    """Mask (2x) of one word, cropped to its ink."""
    f = _font(size)
    im = Image.new('L', (W * S, H * S), 0)
    ImageDraw.Draw(im).text((W * S - 40, H * S // 2), text, font=f, fill=255, anchor='rs', direction='rtl',
                            language='ar')
    a = np.asarray(im) > 127
    ys, xs = np.nonzero(a)
    return a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


# --- outline tracing and faceting ----------------------------------------------------------------------------

_NB = [(0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1)]   # (dy, dx) clockwise from east


def _loops(m):
    """Boundary loops of a boolean mask (outer edges and hole edges), as lists of (x, y)."""
    p = np.pad(m, 1)
    inner = p[1:-1, 1:-1] & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    edge = m & ~inner
    seen = np.zeros_like(m)
    Hh, Ww = m.shape
    loops = []
    for y0, x0 in zip(*np.nonzero(edge)):
        if seen[y0, x0]:
            continue
        loop, y, x, d = [], y0, x0, 6
        for _ in range(200000):
            loop.append((x, y))
            seen[y, x] = True
            for k in range(8):
                nd = (d + 6 + k) % 8          # start looking left of where we came from (Moore tracing)
                yy, xx = y + _NB[nd][0], x + _NB[nd][1]
                if 0 <= yy < Hh and 0 <= xx < Ww and m[yy, xx]:
                    y, x, d = yy, xx, nd
                    break
            else:
                break
            if (y, x) == (y0, x0) and len(loop) > 2:
                break
        if len(loop) > 12:
            loops.append(loop)
    return loops


def _rdp(pts, eps):
    pts = np.asarray(pts, float)
    if len(pts) < 3:
        return pts
    keep = np.zeros(len(pts), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = pts[i], pts[j]
        ab = b - a
        n = np.hypot(*ab) or 1.0
        seg = pts[i + 1:j]
        d = np.abs(ab[0] * (seg[:, 1] - a[1]) - ab[1] * (seg[:, 0] - a[0])) / n
        k = int(d.argmax())
        if d[k] > eps:
            keep[i + 1 + k] = True
            stack += [(i, i + 1 + k), (i + 1 + k, j)]
    return pts[keep]


def facet(m, eps):
    """The mask redrawn from straightened outlines: flat cut facets instead of smooth curves."""
    out = np.zeros(m.shape, bool)
    for loop in _loops(m):
        # split the closed loop at its farthest point so both halves simplify cleanly
        arr = np.asarray(loop, float)
        far = int(np.hypot(*(arr - arr[0]).T).argmax())
        poly = np.vstack([_rdp(arr[:far + 1], eps)[:-1], _rdp(np.vstack([arr[far:], arr[:1]]), eps)[:-1]])
        if len(poly) < 3:
            continue
        lay = Image.new('1', (m.shape[1], m.shape[0]), 0)
        ImageDraw.Draw(lay).polygon([tuple(p) for p in poly], fill=1)
        out ^= np.asarray(lay, bool)
    return out | (m & _erode(m, 3))   # keep thin joins the polygon pass might lose


def _erode(m, r):
    im = Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(2 * r + 1))
    return np.asarray(im) > 127


# --- blade strokes ---------------------------------------------------------------------------------------------

def _crescent(d, cx, cy, rx, ry, a0, a1, w, n=60):
    """A tapered blade along an elliptic arc: zero width at both ends, w at the middle."""
    outer, inner = [], []
    for k in range(n + 1):
        t = k / n
        a = math.radians(a0 + (a1 - a0) * t)
        th = w * math.sin(math.pi * t) ** 0.8
        outer.append((cx + (rx + th / 2) * math.cos(a), cy - (ry + th / 2) * math.sin(a)))
        inner.append((cx + (rx - th / 2) * math.cos(a), cy - (ry - th / 2) * math.sin(a)))
    d.polygon(outer + inner[::-1], fill=255)


def _spike(d, base_l, base_r, tip):
    d.polygon([base_l, tip, base_r], fill=255)


def _tallest(wm):
    """(x0, x1, top) of the tallest vertical stroke of a word mask (the ل of سولز)."""
    tops = np.array([np.nonzero(c)[0].min() if c.any() else 10 ** 6 for c in wm.T])
    k = int(tops.argmin())
    xs = np.nonzero(tops <= tops[k] + 5 * S)[0]
    xs = xs[np.abs(xs - k) < 40 * S]
    return int(xs.min()), int(xs.max()), int(tops[k])


def letters():
    """2x mask of the whole logo fitted into BOX, before texture; and the TM position (1x)."""
    w1, w2 = _word('ديمونز', 300), _word('سولز', 300)
    CW, CH = 3 * W * S, 3 * H * S
    canvas = Image.new('L', (CW, CH), 0)
    # first word high on the right, second lower on the left, its first letter tucked under the first word's end
    x1r, y1t = CW - 200 * S, 300 * S
    x1l = x1r - w1.shape[1]
    y2t = y1t + int(w1.shape[0] * 0.62)
    x2r = x1l + int(w1.shape[1] * 0.28)
    x2l = x2r - w2.shape[1]
    for wm, x, y in ((w1, x1l, y1t), (w2, x2l, y2t)):
        canvas.paste(255, (x, y), Image.fromarray((wm * 255).astype(np.uint8)))
    m = np.asarray(canvas) > 127
    d = ImageDraw.Draw(canvas)

    # swash: a tapered blade from just right of the first letter (د) curling up and over its first letters
    cols = np.nonzero(m[:, x1r - 14 * S:x1r].any(axis=1))[0]
    top_r = int(cols.min())
    rx = int(w1.shape[1] * 0.16)
    _crescent(d, x1r - rx + 10 * S, top_r - 8 * S, rx, 70 * S, -40, 128, 20 * S)

    # ل of سولز drawn up into a long blade point: the original's sword-like "l"
    lx0, lx1, lt = _tallest(w2)
    lx0, lx1, ly = x2l + lx0, x2l + lx1, y2t + lt
    row = np.nonzero(w2[lt + 40 * S])[0]
    row = row[np.abs(row - (lx0 - x2l + lx1 - x2l) // 2) < 30 * S]
    sx0, sx1 = x2l + int(row.min()), x2l + int(row.max())        # the stem's full width a little below its top
    _spike(d, (sx0, ly + 40 * S), (sx1, ly + 40 * S), ((sx0 + sx1) // 2 + 5 * S, ly - 100 * S))
    # (no cross-guard: on a letter it would read as a cross)

    # final ز of سولز (left end): its tail drawn out into a long spike down and to the left (the original's "S")
    m = np.asarray(canvas) > 127
    left = m[:, :x2l + 60 * S]
    rows = np.nonzero(left.any(axis=1))[0]
    by = int(rows.max())
    bx = int(np.nonzero(left[by - 8 * S:by + 1].any(axis=0))[0].min())
    _spike(d, (bx + 6 * S, by - 16 * S), (bx + 26 * S, by - 3 * S), (bx - 85 * S, by + 40 * S))

    m = np.asarray(canvas) > 127
    ys, xs = np.nonzero(m)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    crop = Image.fromarray((m[y0:y1, x0:x1] * 255).astype(np.uint8))
    bw, bh = (BOX[2] - BOX[0]) * S, (BOX[3] - BOX[1]) * S
    f = min(bw / crop.width, bh / crop.height)
    crop = crop.resize((int(crop.width * f), int(crop.height * f)), Image.LANCZOS)
    out = Image.new('L', (W * S, H * S), 0)
    ox = BOX[0] * S + (bw - crop.width) // 2
    oy = BOX[1] * S + (bh - crop.height) // 2
    out.paste(crop, (ox, oy))
    m = np.asarray(out) > 127
    # TM at the end of the second word (its left end), at its baseline
    tmx = (ox + int((x2l - x0) * f)) // S
    tmy = (oy + int((y2t + w2.shape[0] - y0) * f)) // S
    return facet(m, 3.0 * S), (tmx, tmy)


def _slice(d, x, y, rng, length=None, width=None):
    """A thin dark slice through (x, y): near horizontal or gently diagonal, slightly bent, pointed at both ends."""
    ang = math.radians(rng.choice([1, -1]) * rng.uniform(4, 38) + (180 if rng.random() < 0.5 else 0))
    L = (length or rng.uniform(55, 200)) * S
    w = (width or rng.uniform(2.4, 4.2)) * S
    bend = rng.uniform(-8, 8) * S
    ux, uy = math.cos(ang), math.sin(ang)
    px, py = -uy, ux
    top, bot = [], []
    n = 24
    for i in range(n + 1):
        t = i / n
        c = (t - 0.5) * L
        off = bend * (1 - (2 * t - 1) ** 2)
        hw = w / 2 * math.sin(math.pi * t) ** 0.6
        cx, cy = x + ux * c + px * off, y + uy * c + py * off
        top.append((cx + px * hw, cy + py * hw))
        bot.append((cx - px * hw, cy - py * hw))
    d.polygon(top + bot[::-1], fill=0)


def _bilinear(a, x, y):
    h, w = a.shape
    x = np.clip(x, 0, w - 1.001)
    y = np.clip(y, 0, h - 1.001)
    x0, y0 = x.astype(int), y.astype(int)
    fx, fy = x - x0, y - y0
    return ((a[y0, x0] * (1 - fx) + a[y0, x0 + 1] * fx) * (1 - fy) +
            (a[y0 + 1, x0] * (1 - fx) + a[y0 + 1, x0 + 1] * fx) * fy)


CUTS_FILE = os.path.join(HERE, 'logo_cuts.json')


def default_cuts(m, rng, n):
    """The logo's breaks when there is no logo_cuts.json: n short cuts, each straight across one stroke (edge to
    edge), mostly vertical, spread over the letters. Returned in the cut format below (1x screen pixels)."""
    h, w = m.shape
    thick = _erode(m, 7 * S)                 # inside a stroke at least ~14 px thick
    ys, xs = np.nonzero(thick)
    used, cuts, tries = [], [], 0
    while len(cuts) < n and tries < 400:
        tries += 1
        k = rng.integers(len(ys))
        x0, y0 = int(xs[k]), int(ys[k])
        if any(math.hypot(x0 - ux, y0 - uy) < 105 * S for ux, uy in used):
            continue                         # spread out: not two breaks side by side
        ang = math.radians(90 + rng.uniform(-30, 30) + rng.choice([0, 180]))   # mostly vertical cuts
        ux, uy = math.cos(ang), math.sin(ang)
        ends = []                            # walk both ways along the cut until out of the stroke
        for sgn in (1, -1):
            t = 0
            while t < 90 * S:
                xx, yy = int(x0 + sgn * ux * t), int(y0 + sgn * uy * t)
                if not (0 <= xx < w and 0 <= yy < h) or not m[yy, xx]:
                    break
                t += 1
            ends.append(t)
        if max(ends) >= 90 * S or sum(ends) < 16 * S:
            continue                         # along a stroke rather than across it, or too thin
        used.append((x0, y0))
        e1, e2 = ends[0] + 3 * S, ends[1] + 3 * S
        cuts.append({'x1': round((x0 - ux * e2) / S, 1), 'y1': round((y0 - uy * e2) / S, 1),
                     'x2': round((x0 + ux * e1) / S, 1), 'y2': round((y0 + uy * e1) / S, 1),
                     'gap': round(float(rng.uniform(2.2, 3.0)), 2),
                     'slide': round(float(rng.choice([-1, 1]) * rng.uniform(1.6, 3.0)), 2),
                     'fade': 20.0, 'flip': False})
    return cuts


def apply_cuts(a, cuts):
    """Apply breaks to a greyscale picture (float, 0-255), one after the other. A cut is a segment (x1,y1)-(x2,y2) in
    1280x720 pixels. The side to the left of the direction 1->2 (the right side with flip) moves: it opens by `gap`
    and slides `slide` px along the cut, the shift fading out over `fade` px away from the cut and over 7 px past
    its ends; then the opened gap along the segment is drawn black. The cut editor page does exactly the same."""
    h, w = a.shape
    a = a.astype(np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    for c in cuts:
        x1, y1, x2, y2 = (float(c[k]) for k in ('x1', 'y1', 'x2', 'y2'))
        L = math.hypot(x2 - x1, y2 - y1)
        if L < 1:
            continue
        ux, uy = (x2 - x1) / L, (y2 - y1) / L
        nx, ny = -uy, ux
        if c.get('flip'):
            nx, ny = -nx, -ny
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        gap, sl, fade = float(c.get('gap', 2.5)), float(c.get('slide', 0)), max(float(c.get('fade', 20)), 1.0)
        t = (xx - cx) * ux + (yy - cy) * uy
        nn = (xx - cx) * nx + (yy - cy) * ny
        along = np.clip(1 - np.maximum(-L / 2 - t, t - L / 2) / 7.0, 0, 1)
        fall = np.where(nn > 0, np.exp(-np.maximum(nn, 0) / fade) * along, 0)
        sx, sy = (ux * sl + nx * gap) * fall, (uy * sl + ny * gap) * fall
        a = np.where(nn > 0, _bilinear(a, xx - sx, yy - sy), a)
        line = (np.abs(nn - gap * 0.5) < gap * 0.5) & (np.abs(t) < L / 2)
        a = np.where(line, 0, a)
    return a


def _noise(shape, scale, rng):
    h, w = shape
    g = Image.fromarray((rng.random((h // scale + 2, w // scale + 2)) * 255).astype(np.uint8))
    return np.asarray(g.resize((w, h), Image.BICUBIC)).astype(np.float32) / 255


def base_logo(seed=11):
    """The textured logo before the breaks (also the cut editor's base picture), and its 2x letter mask."""
    rng = np.random.default_rng(seed)
    m2, (tmx, tmy) = letters()
    big = Image.fromarray((m2 * 255).astype(np.uint8))
    d = ImageDraw.Draw(big)
    # chips out of the edges: small dark wedges cut in at edge points
    edge = m2 & ~_erode(m2, 2)
    ey, ex = np.nonzero(edge)
    for _ in range(110):
        k = rng.integers(len(ey))
        x, y = ex[k], ey[k]
        r = rng.uniform(3, 9) * S
        a = rng.uniform(0, 2 * math.pi)
        d.polygon([(x, y), (x + r * math.cos(a), y + r * math.sin(a)),
                   (x + r * math.cos(a + 0.8), y + r * math.sin(a + 0.8))], fill=0)
    a = np.asarray(big.resize((W, H), Image.LANCZOS)).astype(np.float32) / 255
    # dark speck clusters, mostly near the edges, on otherwise clean bright letters
    near = 1 - np.asarray(Image.fromarray((a * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(6))) / 255
    zone = (_noise(a.shape, 30, rng) > 0.66) | ((near > 0.22) & (_noise(a.shape, 12, rng) > 0.55))
    fine = _noise(a.shape, 1, rng)
    speck = zone & (fine > 0.70)
    a = a * np.where(speck, 0.08, 1.0)
    a = a * np.where(fine > 0.985, 0.15, 1.0)
    img = Image.fromarray((np.clip(a, 0, 1) * 248).astype(np.uint8))
    tm = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    ImageDraw.Draw(img).text((tmx - 4, tmy), 'TM', fill=225, anchor='rb',
                             font=ImageFont.truetype(tm, 14) if os.path.exists(tm) else None)
    return img, m2


def logo(seed=11, cuts=None):
    """The finished logo: the base picture with its breaks (logo_cuts.json, made with the cut editor; or the
    default ones)."""
    img, m2 = base_logo(seed)
    if cuts is None:
        if os.path.exists(CUTS_FILE):
            cuts = json.load(open(CUTS_FILE, encoding='utf-8'))['cuts']
        else:
            cuts = default_cuts(m2, np.random.default_rng(seed + 1), 9)
    a = apply_cuts(np.asarray(img).astype(np.float32), cuts)
    return Image.fromarray(np.clip(a + 0.5, 0, 255).astype(np.uint8))


if __name__ == '__main__':
    import sys
    logo().save(sys.argv[1] if len(sys.argv) > 1 else 'logo.png')

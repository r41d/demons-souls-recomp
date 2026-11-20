"""The title movie (MOVIE/ds_title.pam: the logo forming out of smoke) remade for the Arabic logo.

  python make_ar_title_movie.py <ds_title.pam> <out.pam> [--frames DIR] [--keep]

The original movie's smoke is the English logo's own shape, so the logo cannot simply be swapped in its frames.
Instead the same five phases are drawn from the Arabic logo (title_logo_ar.png, made by logo_art.py), on the
original's timeline (300 frames, 29.97 fps, measured from the movie):
  0-6 black; 6-75 blue smoke in the logo's shape, turning, with light rays behind it; 70-115 the smoke breaks into
  blue dust; 95-160 the logo forms out of the dust, dark grey and mottled; 150-300 it brightens to the static
  title logo (menu.tpf "Title"), which the game shows when the movie ends -- so there is no jump at the hand-over.
The frames are encoded to MPEG-2 (ffmpeg; same size, rate and GOP shape as the original) and poured into the
original file's own video packets, in place: the PAMF header, every pack boundary and the LPCM audio stay byte for
byte; the video packets left over become padding packets (0xBE). The game's movie player reads the packs in order and does not use
the PAMF entry-point map, so only the video bytes change.
"""
import argparse
import math
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
W, H = 1280, 720
N = 300
SW, SH = 640, 360          # the smoke is drawn at half size, then scaled up (it is soft anyway)


# --- small image tools ---------------------------------------------------------------------------------------------

def _box(a, r, axis):
    if r < 1:
        return a
    p = np.pad(a, [(r + 1, r) if k == axis else (0, 0) for k in range(2)], mode='edge')
    c = np.cumsum(p, axis=axis, dtype=np.float64)
    n = a.shape[axis]
    hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = np.take(c, np.arange(0, n), axis=axis)
    return ((hi - lo) / (2 * r + 1)).astype(np.float32)


def blur(a, r):
    """Gaussian-like blur (three box passes each way)."""
    b = max(1, int(round(r * 0.8)))
    a = a.astype(np.float32)
    for _ in range(3):
        a = _box(_box(a, b, 0), b, 1)
    return a


def resize(a, w, h):
    return np.asarray(Image.fromarray(a.astype(np.float32), 'F').resize((w, h), Image.BILINEAR))


def sample(a, x, y):
    """Bilinear sample of a at float coordinates x, y (clamped)."""
    h, w = a.shape
    x = np.clip(x, 0, w - 1.001)
    y = np.clip(y, 0, h - 1.001)
    x0, y0 = x.astype(int), y.astype(int)
    fx, fy = x - x0, y - y0
    return ((a[y0, x0] * (1 - fx) + a[y0, x0 + 1] * fx) * (1 - fy) +
            (a[y0 + 1, x0] * (1 - fx) + a[y0 + 1, x0 + 1] * fx) * fy)


class Noise:
    """Smooth fractal noise fields that drift over time (crops of big fixed fields, so frames are coherent)."""

    def __init__(self, rng, w, h, scales=(64, 32, 16, 8), pad=200):
        self.w, self.h, self.pad = w, h, pad
        self.layers = []
        for s in scales:
            big = rng.random(((h + 2 * pad) // s + 3, (w + 2 * pad) // s + 3)).astype(np.float32)
            self.layers.append((resize(big, w + 2 * pad, h + 2 * pad), s))

    def at(self, t, speed=0.6):
        out = np.zeros((self.h, self.w), np.float32)
        tot = 0
        for k, (f, s) in enumerate(self.layers):
            amp = 0.5 ** k
            ang = 0.7 + k * 1.9
            dx = self.pad + math.cos(ang) * t * speed * (k + 1)
            dy = self.pad + math.sin(ang) * t * speed * (k + 1)
            yy, xx = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
            out += amp * sample(f, xx + dx, yy + dy)
            tot += amp
        return out / tot


def smooth(t, a, b):
    """0 before a, 1 after b, smooth in between."""
    x = min(max((t - a) / max(b - a, 1e-6), 0.0), 1.0)
    return x * x * (3 - 2 * x)


# --- the frames ------------------------------------------------------------------------------------------------------

def logo_image():
    art = os.path.join(HERE, 'title_logo_ar.png')
    if os.path.exists(art):
        im = Image.open(art).convert('L').resize((W, H), Image.LANCZOS)
    else:
        import logo_art
        im = logo_art.logo()
    return np.asarray(im).astype(np.float32) / 255


def frames(out_dir, seed=3):
    rng = np.random.default_rng(seed)
    L = logo_image()                                   # 1280x720, the static logo (end frame)
    Ls = resize(L, SW, SH)
    cloud_base = blur(Ls, 11) + 0.45 * blur(Ls, 28)
    cloud_base /= cloud_base.max()
    dust_base = blur(Ls, 4) + 0.5 * blur(Ls, 14)
    dust_base /= dust_base.max()
    n1 = Noise(rng, SW, SH, (48, 24, 12, 6))
    n2 = Noise(rng, SW, SH, (96, 48, 24))
    grain = Noise(rng, W, H, (6, 3, 2), pad=40)
    mott = Noise(rng, W, H, (40, 20, 10, 5), pad=40)
    ys, xs = np.nonzero(Ls > 0.3)
    cx, cy = xs.mean(), ys.mean()
    yy, xx = np.mgrid[0:SH, 0:SW].astype(np.float32)
    ang = np.arctan2(yy - cy, xx - cx)
    ray_pat = np.zeros((SH, SW), np.float32)
    for k in range(5):                                 # angular streak pattern for the light rays
        ray_pat += (0.5 ** k) * np.cos(ang * (7 + 6 * k) + rng.uniform(0, 6.3))
    ray_pat = np.clip(ray_pat * 0.7 + 0.25, 0, 1) ** 2
    dist = np.hypot(xx - cx, yy - cy)
    blue = np.array([0.55, 0.80, 1.00], np.float32)
    os.makedirs(out_dir, exist_ok=True)
    for t in range(N):
        # 1) smoke: the logo's blurred shape pushed around by drifting noise, bright where the noise peaks
        f1 = n1.at(t, 0.9)
        f2 = n2.at(t, 0.5)
        warp = 48 * (1 - smooth(t, 30, 110)) + 6
        cl = sample(cloud_base, xx + (f2 - 0.5) * 2 * warp, yy + (n2.at(t + 500, 0.5) - 0.5) * 2 * warp)
        wisp = np.clip((f1 - 0.40) * 3.2, 0, 1.0) * (0.55 + 0.9 * np.clip(n1.at(t * 1.7 + 300, 1.2) - 0.35, 0, 0.5))
        smoke = cl * wisp
        smoke = smoke + 0.5 * blur(smoke, 8)
        s_amt = smooth(t, 4, 20) * (1 - smooth(t, 60, 105)) * 1.5
        # 2) light rays from behind the logo: the smoke smeared outwards from its centre, in streaks
        acc = np.zeros_like(smoke)
        src = blur(smoke, 2)
        for k in range(1, 16):
            sc = 1 + 0.11 * k
            acc += sample(src, cx + (xx - cx) / sc, cy + (yy - cy) / sc) * (0.9 ** k)
        rays = acc / 5 * ray_pat * (0.7 + 0.3 * math.sin(t * 0.09)) * np.clip(dist / 140, 0, 1)
        r_amt = smooth(t, 6, 24) * (1 - smooth(t, 48, 92)) * 0.9
        # 3) dust: fine speckles in the logo's shape, drifting, bluish
        d_amt = smooth(t, 50, 78) * (1 - smooth(t, 115, 165))
        glow = s_amt * smoke + r_amt * rays + 0.35 * d_amt * resize(dust_base, SW, SH) * f1
        col = glow[..., None] * blue
        big = np.stack([resize(col[..., c], W, H) for c in range(3)], axis=-1)
        if d_amt > 0:      # dust at full size: fine specks in the logo's area, drifting
            speck = np.clip((rng.random((H, W)).astype(np.float32) - 0.55) * 3.5, 0, 1)
            field = resize(np.clip((n1.at(t * 2 + 900, 1.5) - 0.28) * 2.5, 0, 1) * dust_base, W, H)
            big += (d_amt * 3.6 * speck * np.clip(field * 1.8, 0, 1))[..., None] * blue
        # 4) the logo forming: revealed through a noise threshold, dark grey and mottled, then brightening
        reveal = smooth(t, 86, 145)
        g = grain.at(t, 0.3)
        mask = np.clip((reveal * 1.35 - g) * 6, 0, 1) if reveal < 1 else 1.0
        bright = 0.18 + 0.30 * smooth(t, 110, 160) + 0.52 * smooth(t, 150, N - 1) ** 1.15
        mot = mott.at(t, 0.15)
        mottle = 1 - (1 - smooth(t, 150, 230)) * np.clip((0.62 - mot) * 2.2, 0, 0.75)
        tint = np.array([1.0, 1.0, 1.0], np.float32) * (1 - 0.18 * (1 - smooth(t, 120, 170))) + \
            np.array([-0.05, 0.0, 0.10], np.float32) * (1 - smooth(t, 120, 170))
        logo = (L * mask * bright * mottle)[..., None] * tint
        img = np.clip(big + logo, 0, 1)
        if t == N - 1:
            img = np.repeat(L[..., None], 3, axis=-1)      # the last frame is exactly the static logo
        Image.fromarray((img * 255 + 0.5).astype(np.uint8)).save(os.path.join(out_dir, 'f%04d.png' % (t + 1)))


# --- encode and repack -----------------------------------------------------------------------------------------------

def encode(frame_dir, es_path, kbps):
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-framerate', '30000/1001',
                    '-i', os.path.join(frame_dir, 'f%04d.png'), '-c:v', 'mpeg2video', '-pix_fmt', 'yuv420p',
                    '-profile:v', '4', '-level:v', '4', '-g', '18', '-bf', '2', '-sc_threshold', '1000000000',
                    '-b:v', f'{kbps}k', '-qmin', '1', '-qmax', '4', '-maxrate', '30000k', '-bufsize', '9781k', '-dc', '10',
                    '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709',
                    '-vf', 'scale=out_color_matrix=bt709:out_range=tv', '-f', 'mpeg2video', es_path], check=True)


def video_payloads(data, data_off, data_size):
    """(PES start, payload offset, payload length) of every video PES packet (stream 0xE0), in stream order."""
    out = []
    end = data_off + data_size
    p = data_off
    while p + 14 <= end:
        assert data[p:p + 4] == b'\x00\x00\x01\xba', hex(p)
        q = p + 14 + (data[p + 13] & 7)
        pe = p + 2048
        while q + 6 <= pe and data[q:q + 3] == b'\x00\x00\x01':
            sid = data[q + 3]
            if sid == 0xB9:
                break
            ln = int.from_bytes(data[q + 4:q + 6], 'big')
            if sid == 0xE0:
                hl = data[q + 8]
                out.append((q, q + 9 + hl, ln - 3 - hl))
            q += 6 + ln
        p = pe
    return out


def repack(src_pam, es, dst_pam):
    """Pour the new video stream into the original's video packets in order. The packet where it ends is
    filled up with zero stuffing placed before the sequence end code (legal MPEG-2 stuffing); every video packet
    after it becomes a padding packet (stream 0xBE), which demuxers skip."""
    data = bytearray(open(src_pam, 'rb').read())
    data_off = int.from_bytes(data[8:12], 'big') * 2048
    data_size = int.from_bytes(data[12:16], 'big') * 2048
    slots = video_payloads(data, data_off, data_size)
    end_code = b'\x00\x00\x01\xb7'
    body = es[:-4] if es.endswith(end_code) else es
    need = len(body) + 4
    k = 0
    used = 0
    for i, (pes, off, n) in enumerate(slots):
        if k < len(body) + 4 and used == i:
            room = n
            if len(body) - k >= room:
                data[off:off + n] = body[k:k + n]
                k += n
            else:
                rest = body[k:]
                pad = room - len(rest) - 4
                if pad < 0:     # the end code does not fit: stuffing here, the end code in the next packet
                    data[off:off + n] = rest[:n] + b'\x00' * max(0, n - len(rest))
                    k += n
                else:
                    data[off:off + n] = rest + b'\x00' * pad + end_code
                    k = len(body) + 4
            used += 1
        else:
            ln = int.from_bytes(data[pes + 4:pes + 6], 'big')
            data[pes + 3] = 0xBE
            data[pes + 6:pes + 6 + ln] = b'\xff' * ln
    cap = sum(n for _, _, n in slots)
    if k < len(body) + 4:
        return False, cap
    open(dst_pam, 'wb').write(bytes(data))
    return True, cap


def build(src_pam, dst_pam, frame_dir=None, keep=False):
    tmp = tempfile.mkdtemp()
    fdir = frame_dir or os.path.join(tmp, 'frames')
    if not os.path.exists(os.path.join(fdir, 'f%04d.png' % N)):
        frames(fdir)
    for kbps in (24000, 20000, 16000, 12000):
        es_path = os.path.join(tmp, 'title.m2v')
        encode(fdir, es_path, kbps)
        ok, cap = repack(src_pam, open(es_path, 'rb').read(), dst_pam)
        if ok:
            print(f'ds_title.pam: Arabic title movie, {os.path.getsize(es_path)} of {cap} video bytes ({kbps} kbit/s)')
            break
    else:
        raise SystemExit('the movie does not fit in the original video packets')
    if not keep:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('--frames')
    ap.add_argument('--keep', action='store_true')
    a = ap.parse_args()
    build(a.src, a.dst, a.frames, a.keep)

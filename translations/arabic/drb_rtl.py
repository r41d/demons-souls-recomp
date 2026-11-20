"""Right-to-left layout for the game's menu layout file (MENU/menu.drb), for the Arabic text.

  python drb_rtl.py IN.drb OUT.drb

The text engine aligns each line inside its Text shape's box by the shape's alignment byte (SHPR+0x16:
bit 0 = right, bit 1 = centre-x; found by static study of the EBOOT). Every left-aligned Text shape is aligned right in its
own box, unless it is a stat label (FMG 76) or a value box overlaps it on the same row (weights...). Those stay
left-aligned, as the value is drawn inside the label's box and right-aligning the label would draw it over the value.
(Mirroring such rows was tried: the containers' coordinates do not give a safe mirror axis.)
A shape's box is s16 left, top, right, bottom at SHPR+0. Never write OUT into the game folder.
"""
import struct
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drbparse  # noqa: E402

WINDOW = 12     # Text shapes this far apart in the file count as neighbours
# FMG 76 (menu common text) holds the stat / defence / resistance labels; their values are placed by other parts of
# the layout that this check cannot pair them with, so those labels stay left-aligned
KEEP_FMG = 76
# HUD names next to their item / magic slot (the box starts at the slot and runs far to the right): left-aligned,
# the text stays beside its slot (right-aligned it ended 200 px away from it)
KEEP_SHAPES = {1983, 1984}
# the value-overlap check (off: in game it also kept titles and help lines left that were fine right-aligned)
CHECK_VALUES = False


def rect(d, o):
    return list(struct.unpack_from('>hhhh', d, o))


def vover(a, b):
    return min(a[3], b[3]) - max(a[1], b[1]) > 0.5 * min(a[3] - a[1], b[3] - b[1])


# literal (non-FMG) Text shapes the reviewers asked to translate: shape index -> (FMG id in menu.msgbnd, string id).
# The shape is switched to read that FMG string (apply_ar.py EXTRA adds it). Growing the STR section instead to hold
# the Arabic made the game stall at boot, so the file size stays the same.
RETARGET = {2200: (70, 6104)}    # options menu "HUD" (the literal is "HUD表示"; the font has no 表示)


def retarget(d, table):
    _, shapes, _ = drbparse.parse(bytes(d))
    for idx, (cat, sid) in table.items():
        sh = next(x for x in shapes if x['i'] == idx)
        assert sh['type'] == 'Text' and d[sh['off'] + 0x17] == 0, idx
        d[sh['off'] + 0x17] = 1
        struct.pack_into('>II', d, sh['off'] + 0x1C, cat, sid)


def main(src, dst):
    d = bytearray(open(src, 'rb').read())
    _, shapes, _ = drbparse.parse(bytes(d))
    texts = [s for s in shapes if s['type'] == 'Text']
    pos = {s['i']: k for k, s in enumerate(texts)}
    flipped = kept = 0
    for t in texts:
        if d[t['off'] + 0x16] & 3:
            continue
        cat = struct.unpack_from('>I', d, t['off'] + 0x1C)[0] if d[t['off'] + 0x17] == 1 else None
        if cat == KEEP_FMG or t['i'] in KEEP_SHAPES:
            kept += 1
            continue
        r = rect(d, t['off'])
        k = pos[t['i']]
        near = texts[max(0, k - WINDOW):k] + texts[k + 1:k + 1 + WINDOW]
        # a value next to the label: text set by code (type 2) or right-aligned, overlapping on the same row
        vals = [s for s in near if d[s['off'] + 0x17] == 2 or d[s['off'] + 0x16] & 1]
        clash = CHECK_VALUES and any(vover(q, r) and q[0] < r[2] and q[2] > r[0] for q in (rect(d, s['off']) for s in vals))
        if clash:
            kept += 1
        else:
            d[t['off'] + 0x16] |= 1
            flipped += 1
    retarget(d, RETARGET)
    open(dst, 'wb').write(bytes(d))
    print(f'menu.drb: {flipped} text boxes right-aligned, {kept} labels with a value on their row kept left')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])

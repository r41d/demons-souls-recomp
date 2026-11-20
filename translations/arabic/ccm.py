import struct
class Ccm:
    def __init__(s, d):
        s.head = bytearray(d[:0x20])
        (s.ver, s.size, s.h, s.tw, s.th, s.u0e, ng, nglyph, goff, gloff) = struct.unpack_from('>IIHHHHHHII', d, 0)
        s.tail = d[0x1C:0x20]
        s.groups = [list(struct.unpack_from('>III', d, goff + i * 12)) for i in range(ng)]
        s.glyphs = [list(struct.unpack_from('>ffffhhhh', d, gloff + i * 24)) for i in range(nglyph)]
        assert gloff + nglyph * 24 == len(d) == s.size, (gloff, nglyph, len(d))
    def index(s, cp):
        for a, b, i in s.groups:
            if a <= cp <= b: return i + cp - a
        return None
    def write(s):
        ng, nglyph = len(s.groups), len(s.glyphs)
        goff = 0x20; gloff = goff + ng * 12
        out = bytearray(gloff + nglyph * 24)
        struct.pack_into('>IIHHHHHHII', out, 0, s.ver, len(out), s.h, s.tw, s.th, s.u0e, ng, nglyph, goff, gloff)
        out[0x1C:0x20] = s.tail
        for k, g in enumerate(s.groups): struct.pack_into('>III', out, goff + k * 12, *g)
        for k, g in enumerate(s.glyphs): struct.pack_into('>ffffhhhh', out, gloff + k * 24, *g)
        return bytes(out)

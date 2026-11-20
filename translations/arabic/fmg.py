"""Demon's Souls FMG (v1, big-endian, UTF-16BE) read/write."""
import struct

def read(d):
    assert d[1] == 1 and d[8] == 1, d[:12].hex()
    ng, ns, soff = struct.unpack_from('>III', d, 0x0C)
    out = {}
    for g in range(ng):
        first, a, b = struct.unpack_from('>III', d, 0x1C + g * 12)
        for k, i in enumerate(range(a, b + 1)):
            o = struct.unpack_from('>I', d, soff + (first + k) * 4)[0]
            if o == 0:
                out[i] = None
                continue
            e = o
            while d[e:e + 2] != b'\0\0':
                e += 2
            out[i] = d[o:e].decode('utf-16-be')
    return out

def write(entries, head=b'\x00\x01\x00\x00'):
    ids = sorted(entries)
    groups = []
    for i in ids:
        if groups and groups[-1][2] == i - 1:
            groups[-1][2] = i
        else:
            groups.append([None, i, i])
    n = 0
    for g in groups:
        g[0] = n; n += g[2] - g[1] + 1
    hdr = 0x1C
    soff = hdr + len(groups) * 12
    data = bytearray(soff + n * 4)
    offs = []
    for i in ids:
        s = entries[i]
        if s is None:
            offs.append(0); continue
        offs.append(len(data))
        data += s.encode('utf-16-be') + b'\0\0'
    data[0:4] = head
    struct.pack_into('>I', data, 4, len(data))
    data[8:12] = b'\x01\xff\x00\x00'
    struct.pack_into('>III', data, 0x0C, len(groups), n, soff)
    for k, g in enumerate(groups):
        struct.pack_into('>III', data, hdr + k * 12, *g)
    for k, o in enumerate(offs):
        struct.pack_into('>I', data, soff + k * 4, o)
    return bytes(data)

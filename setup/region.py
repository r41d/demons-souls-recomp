"""Which Demon's Souls release this setup builds: the USA disc (BLUS-30443, what the port was made for) or the
European one (BLES-00932, the same build with regional edits, its addresses shifted).

Everything the setup needs that differs between them is here. The release is read from the game's own
PARAM.SFO (TITLE_ID) -- from the extracted game folder, or from the disc image before it is extracted -- so
nobody has to choose it. The EU edits to the recompiled code, patches/lifted-code-eu.patch, are
patches/lifted-code.patch carried over to the EU EBOOT's addresses by tools/port_patch_eu.py (a US->EU address
map built from both EBOOTs by tools/us2eu_map.py). It is derived, not edited by hand: after any change to
lifted-code.patch it must be regenerated (tools/port_patch_eu.py's docs), and
`port_patch_eu.py --check patches/lifted-code.patch patches/lifted-code-eu.patch` says whether it is current.
The US values below are exactly the ones the setup always used, so a US setup sees no change.
"""
import os
import struct

REGIONS = {
    'US': {
        'title_id': 'BLUS30443', 'serial': 'BLUS-30443', 'name': 'USA',
        'eboot_bin_sha256': '77375fbf5c654b5a92b870831862a9a09bdc9e6052956d5b84503bda5704f3ac',
        'eboot_elf_sha256': '90eaa5e9e48b4874292badc311ccea253e6a622ff188bfbdbe76e6f6821269d8',
        # SPURS job binaries the game hands to SPU at run time; they sit inside the EBOOT (file offsets)
        'spurs_jobs': [(0x176B480, 384, 'B1463A6F2DF960AC'), (0x176B600, 2064, 'BD46853E5ADE5098')],
        'lift_args': ['--code-end', '0x16bb5e4', '--chunks', '128'],
        'redump_page': 'http://redump.org/disc/16199/',
        'lifted_patch': 'lifted-code.patch',
    },
    'EU': {
        'title_id': 'BLES00932', 'serial': 'BLES-00932', 'name': 'Europe',
        'eboot_bin_sha256': '0245165fd097c0135f8da1007ec0d2fcc0b1de1305946b619457d9dc06a80a2c',
        'eboot_elf_sha256': 'ad67e0b852faa2b459a2344f80dbae21ce3c92263ebea67c643231c653e30ed3',
        # the same job binaries, byte for byte, 0x2100 further into the EBOOT
        'spurs_jobs': [(0x176D580, 384, 'B1463A6F2DF960AC'), (0x176D700, 2064, 'BD46853E5ADE5098')],
        'lift_args': ['--code-end', '0x16bd05c', '--chunks', '128'],
        'redump_page': 'http://redump.org/disc/28776/',
        'lifted_patch': 'lifted-code-eu.patch',
    },
}
BY_TITLE = {r['title_id']: k for k, r in REGIONS.items()}
DEFAULT = 'US'


def sfo_title_id(data):
    """TITLE_ID from the bytes of a PARAM.SFO, or None."""
    if len(data) < 20 or data[:4] != b'\0PSF':
        return None
    kt, dt, n = struct.unpack_from('<III', data, 8)
    for i in range(n):
        ko, fmt, ln, _, do = struct.unpack_from('<HHIII', data, 20 + i * 16)
        end = data.find(b'\0', kt + ko)
        if data[kt + ko:end] == b'TITLE_ID':
            return data[dt + do:dt + do + ln].rstrip(b'\0').decode('ascii', 'replace')
    return None


def from_title_id(title_id):
    """'US' / 'EU' for a known TITLE_ID, else None."""
    return BY_TITLE.get(title_id)


def detect(game_dir):
    """The release of an extracted game folder (its PARAM.SFO; failing that the EBOOT's hash), or None."""
    sfo = os.path.join(game_dir, 'PS3_GAME', 'PARAM.SFO')
    if os.path.isfile(sfo):
        with open(sfo, 'rb') as f:
            r = from_title_id(sfo_title_id(f.read()))
        if r:
            return r
    import hashlib
    for name, key in (('EBOOT.elf', 'eboot_elf_sha256'), ('EBOOT.BIN', 'eboot_bin_sha256')):
        p = os.path.join(game_dir, 'PS3_GAME', 'USRDIR', name)
        if os.path.isfile(p):
            h = hashlib.sha256()
            with open(p, 'rb') as f:
                for b in iter(lambda: f.read(1 << 20), b''):
                    h.update(b)
            for k, r in REGIONS.items():
                if r[key] == h.hexdigest():
                    return k
    return None


def current(root):
    """(key, settings) of the game folder in this checkout; the US release when there is no game yet."""
    k = detect(os.path.join(root, 'game')) or DEFAULT
    return k, REGIONS[k]

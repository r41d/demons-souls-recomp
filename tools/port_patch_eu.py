#!/usr/bin/env python3
"""Port patches/lifted-code.patch (made against the US lift, BLUS30443) to the EU lift (BLES00932).

Dev tool. The lifted code names everything by guest address (func_XXXXXXXX, loc_XXXXXXXX, ctx->lr =
0xXXXXXXXX, TOC values), and the EU EBOOT is the same build with nearly every address shifted, so a
US hunk neither finds its lines nor its file in the EU lift. For every hunk this:
  1. finds the hunk's old lines (context + '-') in the EU lift by comparing with every address token
     blanked out, searching all chunk files (EU groups functions into chunks a little differently);
  2. takes the EU lines exactly as they are there, and learns the hunk's US->EU token pairs by zipping
     the aligned US and EU lines (ground truth from the EU lift); each pair is cross-checked against the
     address map (tools/us2eu_map.py) and a disagreement is reported;
  3. rewrites the '+' lines: tokens learned in the hunk first, else the address map (a func_/loc_ name
     must then exist in the EU target file); 8-digit literals outside the EBOOT stay, and the literals in
     KEEP are numbers, not addresses (re-audit KEEP when lifted-code.patch gains 8-digit number literals:
     anything else in the EBOOT's range is taken for an address and listed in <out>.log);
  4. regroups the hunks by EU file and position and writes a unified diff with EU line numbers.
SPU files only change folder (their images are byte-identical, at +0x2100 in the file) and keep their
SPU local-store names.

One edit cannot be carried over: DES_FRAME_SKIP (NOTES fix 72). EU rewrote the frame-skip function (US
func_000CBB70, EU func_000CC920: four limits instead of two), so the US hunks that mention des_skip_limit
are left out and frame_skip() below makes the EU edit itself; if fix 72 changes in the US patch, change
FRAME_SKIP_HELPER / frame_skip() to match. Any other hunk that cannot be found fails the run and is written
to <out>.rejects. The EU patch is then made in memory (ported hunks + the frame-skip edit applied to the EU
lift, diffed per file) and starts with a line naming the sha256 of the lifted-code.patch it was made from.

Regenerating patches/lifted-code-eu.patch after a change to patches/lifted-code.patch (Linux; both games):
  1. a US setup whose port/recompiled has the current lifted-code.patch applied (the US checkout);
  2. a fresh, UNPATCHED EU lift: setup steps 1-4 on the EU disc, or tools' lift by hand (setup.py step_lift
     before step 5); its port/ is the <EU port dir> below and is only read;
  3. the address map, once: us2eu_map.py build <US EBOOT.elf> <EU EBOOT.elf> <US port>/out/EBOOT.functions.json
     -o us2eu_map.json --eu-functions <EU port>/out/EBOOT.functions.json
  4. port_patch_eu.py patches/lifted-code.patch <EU port dir> us2eu_map.json --us-port <US port dir>
                      -o patches/lifted-code-eu.patch
     (the output is written only when every hunk ported with no warning; read <out>.log: the '+' line tokens
     taken from the address map rather than from the hunk's own context);
  5. check: git apply --check -p1 in a copy of the unpatched EU lift, then build and play the EU game.
  port_patch_eu.py --check patches/lifted-code.patch patches/lifted-code-eu.patch tells whether the EU patch
  was made from the current US patch (exit 1 if not).
"""
import argparse
import difflib
import hashlib
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import us2eu_map  # noqa: E402

TOK = re.compile(r'\b(func_|loc_)([0-9A-F]{8})\b|\b0x([0-9A-Fa-f]{8})(?![0-9A-Fa-f])')
KEEP = {0x01000000, 0x00100000}   # range bounds in added code, not addresses (audit of lifted-code.patch)
SPU_DIRS = {}                     # US spu folder -> EU spu folder, filled from the EU port's spu/
HEADER = '# lifted-code-eu.patch: made by tools/port_patch_eu.py from lifted-code.patch sha256 '
US_FRAME_SKIP = 0x000CBB70        # the US frame-skip function (fix 72); its EU twin comes from the map

FRAME_SKIP_HELPER = r'''/* DES_FRAME_SKIP (PORT PATCH, not lifted code): {eu} is the game's frame skip (EU EBOOT; the
 * US one is {us}). Each frame it takes the time since the previous flip in guest timebase
 * ticks as a percentage of the frame period and flags the next frame to be drawn without its 3D scene
 * when that runs late. The EU build rewrote it: four limits instead of two -- 109% twice, and 105% of
 * half the percentage (the US 210%) twice, the second pair against a period it derives from the
 * display rate. With PS3_CLOCK_SCALE=200 (60 fps modes) guest time runs 2x, so every limit is scaled
 * by the clock scale here, as in the US build: the skip fires at the original REAL time.
 * DES_FRAME_SKIP=0 never skips, =1 is the lifted behaviour. See docs/NOTES.md fix 72. */
#include <stdlib.h>
extern "C" uint32_t ps3_clock_scale(void);   /* PS3_CLOCK_SCALE, sys_timer.c */
static uint64_t des_skip_limit(uint64_t pct)
{{
    static int mode = -2;
    if (mode == -2) {{ const char* e = getenv("DES_FRAME_SKIP"); mode = e ? atoi(e) : -1;
                      fprintf(stderr, "[des] frame skip: %s\n", mode == 0 ? "off (DES_FRAME_SKIP=0)" :
                              mode == 1 ? "lifted (DES_FRAME_SKIP=1)" : "real-time limits"); }}
    if (mode == 0) return ~0ull;
    if (mode == 1) return pct;
    return pct * ps3_clock_scale() / 100;
}}
'''


def frame_skip(lines, def_idx, end_idx, eu_name, us_name):
    """The EU edit for fix 72: the helper before the function, and its four limit compares (109% = 0x6D,
    105% of half = 0x69) wrapped in des_skip_limit(). Returns the new lines; fails if the shape differs."""
    out = list(lines)
    n = 0
    for i in range(def_idx, end_idx):
        new = re.sub(r'uint64_t b = \(uint64_t\)(0x6D|0x69);', r'uint64_t b = des_skip_limit(\1);', out[i])
        n += new != out[i]
        out[i] = new
    if n != 4:
        sys.exit(f'frame skip: {eu_name} has {n} limit compares, expected 4 -- port fix 72 by hand')
    out[def_idx:def_idx] = FRAME_SKIP_HELPER.format(eu=eu_name, us=us_name).split('\n')
    return out


def sha256_text(path):
    return hashlib.sha256(open(path, 'rb').read().replace(b'\r\n', b'\n')).hexdigest()


def toks(line):
    out = []
    for m in TOK.finditer(line):
        if m.group(1):
            out.append((m.start(), m.end(), m.group(1), int(m.group(2), 16), m.group(0)))
        else:
            out.append((m.start(), m.end(), '0x', int(m.group(3), 16), m.group(0)))
    return out


def norm(line):
    return TOK.sub(lambda m: (m.group(1) + 'X') if m.group(1) else '0xADDR', line.rstrip('\r\n'))


def fmt(kind, val, like):
    if kind in ('func_', 'loc_'):
        return f'{kind}{val:08X}'
    digits = like[2:]
    return '0x' + (f'{val:08X}' if digits == digits.upper() else f'{val:08x}')


def parse_patch(text):
    files, cur, hunk = [], None, None
    for raw in text.split('\n'):
        if raw.startswith('diff ') or raw.startswith('--- '):
            continue                     # some files in the patch have no 'diff' line: the '+++' one names them
        elif raw.startswith('+++ '):
            cur = {'path': raw[4:].split('\t')[0].strip()[2:], 'hunks': []}
            files.append(cur); hunk = None
        elif raw.startswith('@@'):
            m = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', raw)
            hunk = {'old': int(m.group(1)), 'lines': []}
            cur['hunks'].append(hunk)
        elif hunk is not None and raw[:1] in (' ', '-', '+'):
            hunk['lines'].append((raw[0], raw[1:]))
        elif hunk is not None and raw.startswith('\\'):
            continue
    return files


FDEF = re.compile(r'^void (func_[0-9A-F]{8})\(ppu_context\* ctx\) \{')


def read_lines(path):
    return open(path, encoding='utf-8', errors='surrogateescape').read().replace('\r\n', '\n').split('\n')


class Tree:
    """A lift's recompiled/ and spu/ sources, with a normalized-line index and function spans."""
    def __init__(self, port, index=True):
        self.port = port
        self.files = {}
        self.index = defaultdict(list)          # normalized line -> [(rel path, line idx)]
        self.fdef = {}                          # func name -> (rel path, def line idx, end idx)
        for sub in ('recompiled', 'spu'):
            for d, _, fs in os.walk(os.path.join(port, sub)):
                for f in fs:
                    if not f.endswith(('.c', '.cpp', '.h')):
                        continue
                    rel = os.path.relpath(os.path.join(d, f), port).replace(os.sep, '/')
                    lines = read_lines(os.path.join(d, f))
                    self.files[rel] = lines
                    if sub != 'recompiled':
                        continue
                    defs = [(i, m.group(1)) for i, l in enumerate(lines) for m in [FDEF.match(l)] if m]
                    for k, (i, name) in enumerate(defs):
                        end = defs[k + 1][0] if k + 1 < len(defs) else len(lines)
                        self.fdef[name] = (rel, i, end)
                    if index:
                        for i, l in enumerate(lines):
                            self.index[norm(l)].append((rel, i))
        self.normed = {rel: [norm(l) for l in ls] for rel, ls in self.files.items()} if index else {}

    def enclosing(self, rel, start):
        """The function whose body holds line `start` of `rel` (None in a file's preamble)."""
        best = None
        for name, (r, i, end) in self.fdef.items():
            if r == rel and i <= start < end:
                best = name
        return best

    def match(self, rel, nold, lo=0, hi=None):
        ln = self.normed[rel]
        hi = len(ln) if hi is None else hi
        return [(rel, s) for s in range(lo, max(lo, hi - len(nold) + 1)) if ln[s:s + len(nold)] == nold]

    def find_global(self, nold):
        best = None
        for k, nl in enumerate(nold):
            if not nl.strip():
                continue
            n = len(self.index.get(nl, ()))
            if n and (best is None or n < best[0]):
                best = (n, k)
        if best is None:
            return []
        hits = []
        for rel, i in self.index[nold[best[1]]]:
            s = i - best[1]
            if s >= 0 and self.normed[rel][s:s + len(nold)] == nold:
                hits.append((rel, s))
        return hits


def main():
    if len(sys.argv) == 4 and sys.argv[1] == '--check':
        us_sha = sha256_text(sys.argv[2])
        first = open(sys.argv[3], encoding='utf-8', errors='replace').readline().strip()
        ok = first == (HEADER + us_sha).strip()
        print(f'{sys.argv[3]}: ' + ('made from the current lifted-code.patch' if ok else
              'OUT OF DATE: lifted-code.patch changed since it was made -- regenerate it (see this tool\'s docs)'))
        sys.exit(0 if ok else 1)
    ap = argparse.ArgumentParser(description='Port lifted-code.patch to the EU lift (see the module docs).')
    ap.add_argument('patch', help='patches/lifted-code.patch')
    ap.add_argument('port', help='the EU port dir with a fresh, UNPATCHED lift (read only)')
    ap.add_argument('map', help='us2eu_map.json (tools/us2eu_map.py build)')
    ap.add_argument('--us-port', required=True, help='the US port dir (its recompiled/ has the patch applied)')
    ap.add_argument('-o', '--out', required=True, help='the EU patch to write (only when the run is clean)')
    ap.add_argument('--manual', nargs='*', default=[], help='US file:old-start of other hunks left out on purpose')
    a = ap.parse_args()
    amap = us2eu_map.load(a.map)
    eu = Tree(a.port)
    us = Tree(a.us_port, index=False)
    for d in os.listdir(os.path.join(a.port, 'spu')):
        m = re.match(r'(spu_\d{4})_at_([0-9A-F]{8})$', d)
        if m:
            SPU_DIRS[m.group(1)] = d
    files = parse_patch(open(a.patch, encoding='utf-8', errors='surrogateescape').read())
    manual = set(a.manual)

    out_hunks = defaultdict(list)        # EU rel path -> [(start, old lines, new lines, origin)]
    rejects, warns, unchecked, how_found = [], [], [], defaultdict(int)
    frame_skip_hunks = []
    n_hunks = 0
    for f in files:
        us_rel = f['path']
        is_spu = us_rel.startswith('spu/')
        hint = us_rel
        if is_spu:
            m = re.match(r'spu/((spu_\d{4})_at_[0-9A-F]{8})/(.*)', us_rel)
            us_pre, eu_pre = m.group(1), SPU_DIRS[m.group(2)]
            hint = f'spu/{eu_pre}/{m.group(3)}'
        for h in f['hunks']:
            n_hunks += 1
            origin = f'{us_rel}:{h["old"]}'
            if any('des_skip_limit' in t for _, t in h['lines']):
                frame_skip_hunks.append(origin)    # fix 72: made for EU by frame_skip() below
                continue
            if is_spu:   # SPU function names carry the image's folder (file offset): rename, else unchanged
                h = dict(h, lines=[(p, t.replace(us_pre, eu_pre)) for p, t in h['lines']])
            old = [t for p, t in h['lines'] if p in ' -']
            if origin in manual:
                rejects.append((origin, h, 'listed with --manual')); continue
            if is_spu:
                ls = eu.files.get(hint, [])
                hits = [(hint, s) for s in range(len(ls) - len(old) + 1) if ls[s:s + len(old)] == old]
                how = 'spu'
            else:
                nold = [norm(l) for l in old]
                # 1. the function the hunk edits: found in the US lift (patched), mapped, searched in its EU body
                newb = [t for p, t in h['lines'] if p in ' +']
                ul = us.files.get(us_rel, [])
                at = [s for s in range(len(ul) - len(newb) + 1) if ul[s:s + len(newb)] == newb]
                fus = us.enclosing(us_rel, at[0]) if len(at) == 1 else None
                hits, how = [], None
                if fus:
                    try:
                        feu = 'func_%08X' % amap.tr(int(fus[5:], 16))
                    except (KeyError, ValueError) as e:
                        feu = None
                        warns.append(f'{origin}: enclosing {fus} not mappable ({e})')
                    if feu in eu.fdef:
                        rel, i, end = eu.fdef[feu]
                        hits, how = eu.match(rel, nold, i, end + len(nold)), f'function {fus}->{feu}'
                        if len(hits) > 1:
                            # the block repeats in the function: take the EU copy with the same rank as the US
                            # one (the other copies are still unpatched in the US lift, so they count before it)
                            _, ui, uend = us.fdef[fus]
                            unold = [norm(l) for l in ul[ui:uend + len(nold)]]
                            before = sum(1 for s2 in range(0, at[0] - ui) if unold[s2:s2 + len(nold)] == nold)
                            total = before + 1 + sum(1 for s2 in range(at[0] - ui + 1, len(unold) - len(nold) + 1)
                                                     if unold[s2:s2 + len(nold)] == nold)
                            if total == len(hits):
                                hits, how = [hits[before]], f'function-rank {fus}->{feu} ({before + 1}/{total})'
                    elif feu:
                        warns.append(f'{origin}: {feu} (from {fus}) is not defined in the EU lift')
                # 2. file preamble (includes, macros): the EU file of the same name
                if not hits and not fus and hint in eu.files:
                    hits, how = eu.match(hint, nold), 'same file'
                # 3. anywhere, if unique
                if not hits and not fus:
                    hits, how = eu.find_global(nold), 'global'
            if len(hits) != 1:
                rejects.append((origin, h, f'{len(hits)} places in the EU lift ({how})')); continue
            how_found[how.split()[0]] += 1
            rel, s = hits[0]
            eu_old = eu.files[rel][s:s + len(old)]
            pairs = {}
            for ul, el in zip(old, eu_old):
                for (_, _, k, uv, _), (_, _, k2, ev, _) in zip(toks(ul), toks(el)):
                    if is_spu:
                        ev = uv
                    if pairs.get((k, uv), ev) != ev:
                        warns.append(f'{origin}: {k}{uv:08X} pairs with both {pairs[(k, uv)]:08X} and {ev:08X}')
                    pairs[(k, uv)] = ev
                    if not is_spu:
                        try:
                            mv = amap.tr(uv)
                            if mv != ev:
                                warns.append(f'{origin}: context pairs {k}{uv:08X} -> {ev:08X}, map says {mv:08X}')
                        except (KeyError, ValueError):
                            pass
            new = []
            oi = 0
            for p, t in h['lines']:
                if p == ' ':
                    new.append((' ', eu_old[oi])); oi += 1
                elif p == '-':
                    new.append(('-', eu_old[oi])); oi += 1
                else:
                    line = t
                    if not is_spu:
                        parts, last = [], 0
                        for st, en, k, uv, raw in toks(t):
                            ev = pairs.get((k, uv))
                            how = 'hunk'
                            if ev is None:
                                if k == '0x' and uv in KEEP:
                                    ev, how = uv, 'keep'
                                else:
                                    try:
                                        ev, how = amap.tr(uv), 'map'
                                    except KeyError:
                                        ev, how = uv, 'outside'
                                    except ValueError as e:
                                        rejects.append((origin, h, f'+ line token {raw} not mappable: {e}'))
                                        ev, how = None, 'fail'
                            if ev is None:
                                break
                            if how == 'map':
                                unchecked.append(f'{origin}: {raw} -> {fmt(k, ev, raw)} (map, {amap.info(uv)[1]})')
                                name = fmt(k, ev, raw)
                                if k == 'func_' and name not in eu.fdef:      # declared for all in ppu_recomp.h
                                    warns.append(f'{origin}: {name} is not a function of the EU lift')
                                if k == 'loc_' and not any(name + ':' in l for l in eu.files[rel]):
                                    warns.append(f'{origin}: label {name} is not in {rel}')
                            parts.append(t[last:st]); parts.append(fmt(k, ev, raw)); last = en
                        else:
                            line = ''.join(parts) + t[last:]
                            new.append(('+', line)); continue
                        break
                    new.append(('+', line))
            else:
                out_hunks[rel].append((s, len(old), new, origin))
                continue
            # a failed '+' token: already in rejects

    # apply the ported hunks to the EU lift in memory (by position, last first), then the frame-skip edit
    patched = {}
    for rel in sorted(out_hunks):
        hs = sorted(out_hunks[rel], key=lambda x: x[0])
        for (s1, n1, _, o1), (s2, _, _, o2) in zip(hs, hs[1:]):
            if s1 + n1 > s2:
                sys.exit(f'overlapping hunks in {rel}: {o1} and {o2}')
        lines = list(eu.files[rel])
        for s, n_old, new, _ in reversed(hs):
            lines[s:s + n_old] = [t for p, t in new if p != '-']
        patched[rel] = lines
    if frame_skip_hunks:
        us_name = 'func_%08X' % US_FRAME_SKIP
        eu_name = 'func_%08X' % amap.tr(US_FRAME_SKIP)
        rel, i, end = eu.fdef[eu_name]
        lines = patched.get(rel, eu.files[rel])
        di = lines.index(eu.files[rel][i])          # the def line, after earlier hunks moved it
        patched[rel] = frame_skip(lines, di, di + (end - i), eu_name, us_name)
        print(f'frame skip (fix 72): {len(frame_skip_hunks)} US hunks -> EU edit of {eu_name} in {rel}')
    if [x for x in rejects if x[0] not in manual] or warns:
        print('NOT written (rejected hunks or warnings, see below and the .log/.rejects files)')
    else:
        with open(a.out, 'w', encoding='utf-8', errors='surrogateescape', newline='\n') as o:
            o.write(HEADER + sha256_text(a.patch) + '\n')
            for rel in sorted(patched):
                # the files' real text: split('\n') left an empty last element for a final newline
                old_l = '\n'.join(eu.files[rel]).splitlines(True)
                new_l = '\n'.join(patched[rel]).splitlines(True)
                o.write(f'diff -ruN a/{rel} b/{rel}\n')
                for d in difflib.unified_diff(old_l, new_l, f'a/{rel}', f'b/{rel}', n=3):
                    o.write(d if d.endswith('\n') else d + '\n\\ No newline at end of file\n')
    with open(a.out + '.rejects', 'w', encoding='utf-8', errors='surrogateescape') as r:
        for origin, h, why in rejects:
            r.write(f'### {origin}: {why}\n')
            for p, t in h['lines']:
                r.write(p + t + '\n')
    with open(a.out + '.log', 'w', encoding='utf-8') as lg:
        lg.write('\n'.join(['# warnings'] + warns + ['', '# + line tokens taken from the address map'] + unchecked) + '\n')
    ported = sum(len(v) for v in out_hunks.values())
    print('found by:', dict(how_found))
    print(f'hunks: {n_hunks}, ported {ported}, rejected {len(rejects)} -> {a.out}.rejects; '
          f'{len(warns)} warnings, {len(unchecked)} map-only + tokens -> {a.out}.log')
    for origin, _, why in rejects:
        print(f'  REJECT {origin}: {why}')
    bad = [x for x in rejects if x[0] not in manual]
    sys.exit(1 if bad or warns else 0)


if __name__ == '__main__':
    main()

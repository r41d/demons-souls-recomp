#!/usr/bin/env python3
"""US -> EU guest-address map for the Demon's Souls EBOOT pair (BLUS30443 -> BLES00932).

Dev tool, stdlib only. Builds a map from the two decrypted EBOOT.elf files plus the US
ppu_loader function list, checks it against itself, and translates addresses.

  us2eu_map.py build <US EBOOT.elf> <EU EBOOT.elf> <US EBOOT.functions.json> -o map.json
                     [--eu-functions <EU EBOOT.functions.json>]
  us2eu_map.py tr <map.json> 0xADDR ...
  us2eu_map.py patch <map.json> <lifted-code.patch> [--stubs stubs.cpp] -o patch_addrs.json

Python API:
  m = load('map.json'); m.tr(0x19B5040) -> 0x19B5270
  m.tr raises KeyError for addresses outside the EBOOT's sections and ValueError for
  addresses inside them that cannot be mapped safely (changed function interior, data
  inserted/changed in EU, ambiguous alignment). m.info(addr) -> (eu, kind).

How the map is built (everything below is printed with counts by `build`)
  Code (sections from the first function to the end of .text): every US function from
  functions.json is compared with EU code, LI field of b/bl masked, all other bits exact.
  All matches in a window are candidates; a Viterbi pass in address order picks one
  shift per function (reward per match, penalty per shift change, no EU overlap); gaps
  are then filled positionally (still identical at the gap edge, or a unique candidate
  inside the gap). Loader 'functions' are split at interior entry points other code
  branches to (the loader merges code without descriptors). Masked compare cannot tell
  look-alikes apart that differ only in their call targets, so pieces are then moved to
  where >= 2 identical call sites (80%) say they are (call-graph votes), and function
  pointers in aligned data/.opd place the entries of changed functions.
  Identical pieces map every interior address by offset. Changed ones ('changed') map
  only their entry, when it is pinned (first function after a matched one with the same
  EU function count in the gap), positional with a matching prologue, taken from its
  .opd descriptor's position, or voted by call sites / function pointers; an entry that
  votes contradict is dropped. Interiors of changed code are refused.
  Data (every other allocated section):
   - .opd: descriptor for descriptor via {tr(entry), tr(toc)}; duplicates (per-module
     copies) by the nearest unique neighbour's shift.
   - import stubs / stub slots / FNID table: matched by NID (EU has them permuted).
   - other sections: seed-and-extend word matching that allows moved objects. Seeds:
     addresses that identical code reaches through TOC slots/section anchors (lwz rX,
     d(r2) then d'(rX) or addi), pointer words, unique 4-word runs, section ends. A
     word pair is equal if equal, or a pointer whose target maps consistently (pass 1:
     same target section; passes 2-3: tr(target)). Blocks contradicted by a code
     reference are dropped. A US word claimed by blocks with different shifts, or two
     US words landing on one EU word, is ambiguous: it is mapped only if code/pointer
     evidence names exactly that address, or 'data-context' if both clean neighbours
     have the same shift and it is a candidate. Words in no block are refused.
     A section matched as one full-length block (with the lax pointer test) is a
     constant shift ('data'); the TOC is one of those even though some of its values
     differ (section anchors).
   - NOBITS (.bss, .tbss): constant section shift.
  Kinds returned by info(): code, code-entry(changed func), data, data-pw,
  data-context (heuristic), data-evidence, bss, section-end.
"""
import argparse
import array
import bisect
import hashlib
import json
import re
import struct
import sys

SHF_WRITE, SHF_ALLOC, SHF_EXEC = 1, 2, 4
SHT_NOBITS = 8
K = 8               # resync run length (words)
PEN = 2.0           # Viterbi penalty for a shift change
LITTLE = sys.byteorder == 'little'


# ----------------------------------------------------------------------------- ELF
class Elf:
    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            self.data = f.read()
        d = self.data
        if d[:4] != b'\x7fELF' or d[4] != 2 or d[5] != 2:
            raise SystemExit(f'{path}: not a big-endian ELF64')
        shoff = struct.unpack_from('>Q', d, 0x28)[0]
        shentsize, shnum, _ = struct.unpack_from('>HHH', d, 0x3A)
        self.sections = []
        for k in range(shnum):
            (_n, typ, flags, addr, off, size, _l, _i, align,
             _e) = struct.unpack_from('>IIQQQQIIQQ', d, shoff + k * shentsize)
            self.sections.append(dict(idx=k, type=typ, flags=flags, addr=addr, off=off,
                                      size=size, align=align))
        self.sha256 = hashlib.sha256(d).hexdigest()

    def alloc(self):
        return [s for s in self.sections if s['flags'] & SHF_ALLOC and s['size']]

    def words(self, s, start=None, end=None):
        """big-endian u32 words of [start,end) (vaddrs) of section s"""
        a = s['addr'] if start is None else start
        e = s['addr'] + s['size'] if end is None else end
        off = s['off'] + (a - s['addr'])
        n = (e - a) // 4
        w = array.array('I', self.data[off:off + 4 * n])
        if LITTLE:
            w.byteswap()
        return w

    def u32(self, addr):
        for s in self.alloc():
            if s['type'] != SHT_NOBITS and s['addr'] <= addr < s['addr'] + s['size'] - 3:
                return struct.unpack_from('>I', self.data, s['off'] + addr - s['addr'])[0]
        return None


def mask_branches(w):
    """b/bl/ba/bla: keep opcode + AA/LK, drop LI"""
    m = array.array('I', w)
    for k, x in enumerate(w):
        if x >> 26 == 18:
            m[k] = x & 0xFC000003
    return m


def mask_imm(w):
    """for small exec sections (import stubs): also drop the 16-bit immediates of
    addi/addis/ori/oris and D-form loads/stores, which hold halves of moved addresses"""
    m = mask_branches(w)
    for k, x in enumerate(m):
        if x >> 26 in (14, 15, 24, 25) or 32 <= x >> 26 <= 55 or x >> 26 in (58, 62):
            m[k] = x & 0xFFFF0000
    return m


class SecIndex:
    """address -> section index for one side (alloc sections)"""
    def __init__(self, secs):
        self.secs = sorted(secs, key=lambda s: s['addr'])
        self.starts = [s['addr'] for s in self.secs]

    def find(self, a, allow_end=False):
        k = bisect.bisect_right(self.starts, a) - 1
        if k >= 0:
            s = self.secs[k]
            if a < s['addr'] + s['size'] or (allow_end and a == s['addr'] + s['size']):
                return s['idx']
        if allow_end and k >= 0:
            pass
        return None


# ----------------------------------------------------------------------------- map (load side)
class Map:
    def __init__(self, j):
        self.j = j
        self.dom = (j['code']['us_start'], j['code']['us_end'])
        f = j['code']['functions']
        self.f_us, self.f_end, self.f_eu, self.f_st = f['us'], f['us_end'], f['eu'], f['status']
        self.secs = sorted(j['sections'], key=lambda s: s['us_start'])
        self.sec_starts = [s['us_start'] for s in self.secs]
        for s in self.secs:
            s['_ro'] = [r[0] for r in s.get('runs', [])]
            s['_co'] = [r[0] for r in s.get('context', [])]
            s['_amb'] = sorted(s.get('ambiguous', []))
            s['_ambo'] = [a[0] for a in s['_amb']]
            s['_res'] = {int(k, 16): v for k, v in s.get('resolved', {}).items()}

    def info(self, addr):
        """-> (eu_addr, kind); KeyError if outside the EBOOT, ValueError if refused"""
        a = int(addr)
        if self.dom[0] <= a < self.dom[1]:
            k = bisect.bisect_right(self.f_us, a) - 1
            if k < 0 or a >= self.f_end[k]:
                raise ValueError(f'0x{a:08X}: code address outside any function')
            st = self.f_st[k]
            if st == 0:
                return self.f_eu[k] + (a - self.f_us[k]), 'code'
            if a == self.f_us[k] and self.f_eu[k] >= 0:
                return self.f_eu[k], 'code-entry(changed func)'
            why = 'entry not mappable' if a == self.f_us[k] else 'interior of a changed function'
            raise ValueError(f'0x{a:08X}: {why} (func_{self.f_us[k]:08X})')
        k = bisect.bisect_right(self.sec_starts, a) - 1
        s = self.secs[k] if k >= 0 else None
        if s is None or a >= s['us_start'] + s['us_size']:
            # one past the end of a section
            for t in self.secs:
                if a == t['us_start'] + t['us_size']:
                    r = t.get('runs')
                    if t['kind'] != 'piecewise' or (r and r[-1][0] + r[-1][2] == t['us_size'] and
                                                    r[-1][1] + r[-1][2] == t['eu_size']):
                        return t['eu_start'] + t['eu_size'], 'section-end'
                    raise ValueError(f'0x{a:08X}: end of section {t["idx"]}, whose tail differs')
            raise KeyError(f'0x{a:08X}: not inside any EBOOT section')
        off = a - s['us_start']
        if s['kind'] in ('const', 'nobits'):
            return s['eu_start'] + off, 'bss' if s['kind'] == 'nobits' else 'data'
        rk = bisect.bisect_right(s['_ro'], off) - 1
        run = s['runs'][rk] if rk >= 0 else None
        if run is not None and off < run[0] + run[2]:
            return s['eu_start'] + run[1] + (off - run[0]), 'data-pw'
        ck = bisect.bisect_right(s['_co'], off) - 1
        if ck >= 0 and off < s['context'][ck][0] + s['context'][ck][2]:
            c = s['context'][ck]
            return s['eu_start'] + c[1] + (off - c[0]), 'data-context'
        ak = bisect.bisect_right(s['_ambo'], off) - 1
        if ak >= 0 and off < s['_amb'][ak][0] + s['_amb'][ak][1]:
            w = a & ~3
            if w in s['_res']:
                return s['_res'][w] + (a - w), 'data-evidence'
            raise ValueError(f'0x{a:08X}: section {s["idx"]} ambiguous (several EU candidates, '
                             f'no pointer evidence)')
        raise ValueError(f'0x{a:08X}: section {s["idx"]} data changed/removed in EU (no match)')

    def tr(self, addr):
        return self.info(addr)[0]


def load(path):
    with open(path) as f:
        return Map(json.load(f))


# ----------------------------------------------------------------------------- build
class Builder:
    def __init__(self, us_elf, eu_elf, us_funcs, eu_funcs=None, window=0x2000, log=print):
        self.log = log
        self.U, self.E = Elf(us_elf), Elf(eu_elf)
        self.window = window
        us_al, eu_al = self.U.alloc(), self.E.alloc()
        if [(s['idx'], s['flags'], s['type']) for s in us_al] != \
           [(s['idx'], s['flags'], s['type']) for s in eu_al]:
            raise SystemExit('section tables differ in shape; this tool assumes same sections')
        self.pairs = list(zip(us_al, eu_al))
        self.usi, self.eui = SecIndex(us_al), SecIndex(eu_al)
        self.us_sec = {s['idx']: s for s in us_al}
        self.eu_sec = {s['idx']: s for s in eu_al}
        execs = [p for p in self.pairs if p[0]['flags'] & SHF_EXEC]
        text = max(execs, key=lambda p: p[0]['size'])
        self.text_idx = text[0]['idx']
        self.us_funcs_raw = self._read_funcs(us_funcs)
        self.eu_funcs_raw = self._read_funcs(eu_funcs) if eu_funcs else None
        # function domain: from the section holding the first function to the end of .text
        first = self.us_funcs_raw[0][0]
        s0 = self.us_sec[self.usi.find(first)]
        self.dom_us = (s0['addr'], text[0]['addr'] + text[0]['size'])
        self.dom_eu = (self.eu_sec[s0['idx']]['addr'], text[1]['addr'] + text[1]['size'])
        self.dom_secs = {p[0]['idx'] for p in self.pairs
                         if self.dom_us[0] <= p[0]['addr'] < self.dom_us[1]}
        self.code = None

    @staticmethod
    def _read_funcs(path):
        with open(path) as f:
            d = json.load(f)
        return sorted((int(x['start'], 16), int(x['end'], 16), int(x['toc'], 16),
                       int(x.get('opd', '0'), 16)) for x in d)

    # ---------------------------------------------------------------- code
    def build_code(self):
        U, E = self.U, self.E
        ua, ub = self.dom_us
        ea, eb = self.dom_eu
        us_w = self._dom_words(U, ua, ub)
        eu_w = self._dom_words(E, ea, eb)
        self.us_w, self.eu_w = us_w, eu_w
        um, em = mask_branches(us_w).tobytes(), mask_branches(eu_w).tobytes()
        self.um, self.em = um, em
        funcs = [(a, min(b, ub)) for a, b, _t, _o in self.us_funcs_raw if ua <= a < ub]
        self.funcs = funcs
        delta = (eb - ea) - (ub - ua)
        lo, hi = min(0, delta) - self.window, max(0, delta) + self.window
        cands = []
        for a, b in funcs:
            ndl = um[a - ua:b - ua]
            p0 = max(0, a - ua + lo)
            p1 = min(len(em), b - ua + hi)
            res, p = [], em.find(ndl, p0, p1)
            while p != -1 and len(res) < 24:
                if p % 4 == 0:
                    res.append(ea + p - a)
                p = em.find(ndl, p + 1, p1)
            cands.append(res)
        nuniq = sum(1 for c in cands if len(c) == 1)
        nmulti = sum(1 for c in cands if len(c) > 1)
        nnone = sum(1 for c in cands if not c)
        self.log(f'code: {len(funcs)} US functions in 0x{ua:X}..0x{ub:X}; candidates: '
                 f'{nuniq} unique, {nmulti} multiple, {nnone} none (window {lo:+#x}..{hi:+#x})')
        shifts = self._viterbi(funcs, cands, ea)
        self._fill_gaps(funcs, cands, shifts, ea, eb)
        self.shifts = shifts
        self._changed(funcs, shifts, ea, eb)
        self.f_starts = [f[0] for f in funcs]
        self.split_pieces()
        self.refine_calls()

    @staticmethod
    def _dom_words(elf, a, b):
        out = array.array('I')
        for s in sorted(elf.alloc(), key=lambda s: s['addr']):
            s0, s1 = s['addr'], s['addr'] + s['size']
            if s1 <= a or s0 >= b:
                continue
            cur = a + 4 * len(out)
            if s0 > cur:
                out.extend([0] * ((s0 - cur) // 4))
            out.extend(elf.words(s, max(s0, a), min(s1, b)))
        return out

    def _viterbi(self, funcs, cands, ea):
        states = {0: (0.0, ea)}
        back = []
        for (a, b), C in zip(funcs, cands):
            new, bk = {}, {}
            for s, (sc, lend) in states.items():
                if s not in new or sc > new[s][0]:
                    new[s] = (sc, lend)
                    bk[s] = (s, False)
                for c in C:
                    if a + c < lend:
                        continue
                    v = sc + 1.0 - (0.0 if c == s else PEN)
                    if c not in new or v > new[c][0]:
                        new[c] = (v, b + c)
                        bk[c] = (s, True)
            best = max(v[0] for v in new.values())
            keep = sorted(((v[0], s) for s, v in new.items() if v[0] >= best - 3 * PEN),
                          reverse=True)[:32]
            states = {s: new[s] for _v, s in keep}
            back.append(bk)
        s = max(states, key=lambda x: states[x][0])
        shifts = [None] * len(funcs)
        for k in range(len(funcs) - 1, -1, -1):
            prev, matched = back[k][s]
            if matched:
                shifts[k] = s
            s = prev
        return shifts

    def _eq_at(self, a, b, eu_addr):
        ua, ea = self.dom_us[0], self.dom_eu[0]
        n = b - a
        p = eu_addr - ea
        if p < 0 or p + n > len(self.em):
            return False
        return self.um[a - ua:b - ua] == self.em[p:p + n]

    def _fill_gaps(self, funcs, cands, shifts, ea, eb):
        n, filled = len(funcs), 0
        k = 0
        while k < n:
            if shifts[k] is not None:
                k += 1
                continue
            g0 = k
            while k < n and shifts[k] is None:
                k += 1
            g1 = k  # unmatched [g0, g1)
            lo_eu = (funcs[g0 - 1][1] + shifts[g0 - 1]) if g0 > 0 else ea
            hi_eu = (funcs[g1][0] + shifts[g1]) if g1 < n else eb
            # forward from the left edge
            cur, i = lo_eu, g0
            while i < g1:
                a, b = funcs[i]
                if cur + (b - a) <= hi_eu and self._eq_at(a, b, cur):
                    shifts[i] = cur - a
                    cur += b - a
                    filled += 1
                    i += 1
                else:
                    break
            # backward from the right edge
            cur, j = hi_eu, g1 - 1
            while j >= i:
                a, b = funcs[j]
                if cur - (b - a) >= lo_eu and self._eq_at(a, b, cur - (b - a)):
                    shifts[j] = cur - b
                    cur -= b - a
                    filled += 1
                    j -= 1
                else:
                    break
            # unique candidate inside what is left of the gap
            lo2 = (funcs[i - 1][1] + shifts[i - 1]) if i > 0 and shifts[i - 1] is not None else lo_eu
            for t in range(i, j + 1):
                a, b = funcs[t]
                ok = [c for c in cands[t] if a + c >= lo2 and b + c <= cur]
                if len(ok) == 1:
                    shifts[t] = ok[0]
                    lo2 = b + ok[0]
                    filled += 1
        self.log(f'code: gap fill matched {filled} more functions')

    def _changed(self, funcs, shifts, ea, eb):
        n = len(funcs)
        eu_starts = None
        if self.eu_funcs_raw:
            eu_starts = sorted(x[0] for x in self.eu_funcs_raw)
            self.eu_start_set = set(eu_starts)
            self.eu_end_of = {x[0]: x[1] for x in self.eu_funcs_raw}
        status = [0 if s is not None else 2 for s in shifts]
        eu = [funcs[k][0] + shifts[k] if shifts[k] is not None else -1 for k in range(n)]
        changed = []
        k = 0
        while k < n:
            if shifts[k] is not None:
                k += 1
                continue
            g0 = k
            while k < n and shifts[k] is None:
                k += 1
            g1 = k
            lo_eu = (funcs[g0 - 1][1] + shifts[g0 - 1]) if g0 > 0 else ea
            hi_eu = (funcs[g1][0] + shifts[g1]) if g1 < n else eb
            ks = g1 - g0
            eu_in = None
            if eu_starts is not None:
                eu_in = eu_starts[bisect.bisect_left(eu_starts, lo_eu):
                                  bisect.bisect_left(eu_starts, hi_eu)]
            for t in range(g0, g1):
                a, b = funcs[t]
                i = t - g0
                cand, why = None, ''
                if eu_in is not None and len(eu_in) == ks:
                    cand = eu_in[i]
                    if i == 0 and cand == lo_eu:
                        why = 'pinned: first function after a matched one; EU gap has the same function count'
                    elif ks == 1:
                        why = 'pinned: only function in the gap on both sides'
                    else:
                        why = 'same position in gap (same function count)'
                elif i == 0:
                    cand = lo_eu
                    why = 'pinned: first function after a matched one'
                pw = 0
                if cand is not None:
                    pw = self._prologue_eq(a, b, cand)
                ok = False
                if cand is not None:
                    counts_ok = eu_in is not None and len(eu_in) == ks
                    if counts_ok and (why.startswith('pinned') or pw >= 2):
                        ok = True
                    elif pw >= (2 if eu_in is None else 4):
                        ok = True
                    if not ok:
                        why += (f'; refused: prologue differs ({pw} words equal)'
                                + ('' if eu_in is None or counts_ok else
                                   f' and the EU gap has {len(eu_in)} functions for {ks}'))
                else:
                    why = (f'gap has {ks} US vs {len(eu_in) if eu_in is not None else "?"} EU '
                           f'functions')
                eu_end = None
                if ok:
                    status[t], eu[t] = 1, cand
                    if eu_in is not None:
                        eu_end = self.eu_end_of.get(cand)
                changed.append(dict(us=a, us_size=b - a, eu=cand if ok else None,
                                    eu_size=(eu_end - cand) if eu_end else None,
                                    prologue_words_equal=pw, note=why,
                                    gap_us_funcs=ks,
                                    gap_eu_funcs=len(eu_in) if eu_in is not None else None,
                                    _lo=lo_eu, _hi=hi_eu))
        self.code = dict(status=status, eu=eu)
        self.changed = changed
        nm = sum(1 for s in status if s == 0)
        self.log(f'code: {nm}/{n} functions identical modulo b/bl ({100.0 * nm / n:.2f}%), '
                 f'{len(changed)} changed ({sum(1 for c in changed if c["eu"] is not None)} '
                 f'with a mapped entry)')

    def call_votes(self):
        """for every US call/tail-call target: Counter of EU targets seen at the same
        instruction of identical (status 0) callers"""
        ua, ea = self.dom_us[0], self.dom_eu[0]
        votes = {}
        for k, (a, b) in enumerate(self.funcs):
            if self.code['status'][k] != 0:
                continue
            e = self.code['eu'][k]
            for q in range(a, b, 4):
                w = self.us_w[(q - ua) // 4]
                if w >> 26 != 18 or w & 2:
                    continue
                li = w & 0x03FFFFFC
                t = q + (li - 0x04000000 if li & 0x02000000 else li)
                if a <= t < b:
                    continue
                w2 = self.eu_w[(e + q - a - ea) // 4]
                li2 = w2 & 0x03FFFFFC
                t2 = e + q - a + (li2 - 0x04000000 if li2 & 0x02000000 else li2)
                d = votes.setdefault(t, {})
                d[t2] = d.get(t2, 0) + 1
        return votes

    def split_pieces(self):
        """split loader functions at entry points other code calls/jumps into (the loader
        merges code without descriptors into one 'function'); each piece is mapped on its
        own from then on"""
        ua = self.dom_us[0]
        starts = [a for a, _b in self.funcs]
        ext = set()
        for k, (a, b) in enumerate(self.funcs):
            for q in range(a, b, 4):
                w = self.us_w[(q - ua) // 4]
                if w >> 26 != 18 or w & 2:
                    continue
                li = w & 0x03FFFFFC
                t = q + (li - 0x04000000 if li & 0x02000000 else li)
                if not a <= t < b:
                    ext.add(t)
        funcs, st, eu, parent = [], [], [], []
        for k, (a, b) in enumerate(self.funcs):
            lo = bisect.bisect_right(sorted_ext := self._sorted_ext(ext), a)
            hi = bisect.bisect_left(sorted_ext, b)
            cuts = [a] + [t for t in sorted_ext[lo:hi] if t % 4 == 0] + [b]
            for x, y in zip(cuts, cuts[1:]):
                funcs.append((x, y))
                parent.append(a)
                s0, e0 = self.code['status'][k], self.code['eu'][k]
                if x == a:
                    st.append(s0)
                    eu.append(e0)
                elif s0 == 0:
                    st.append(0)
                    eu.append(e0 + x - a)
                else:
                    st.append(2)
                    eu.append(-1)
        nsplit = sum(1 for p, (x, _y) in zip(parent, funcs) if p != x)
        self.log(f'code: split {nsplit} called interior entry points off '
                 f'{len({p for p, (x, _y) in zip(parent, funcs) if p != x})} loader functions '
                 f'-> {len(funcs)} pieces')
        self.orig_starts = set(starts)
        self.funcs = funcs
        self.f_starts = [a for a, _b in funcs]
        self.code = dict(status=st, eu=eu)

    def _sorted_ext(self, ext):
        if getattr(self, '_ext_cache', None) is None or self._ext_cache[0] is not ext:
            self._ext_cache = (ext, sorted(ext))
        return self._ext_cache[1]

    def fptr_votes(self):
        """function-pointer votes: words of strictly aligned data runs (TOC left out) that
        point at .opd descriptors give US entry -> EU entry pairs"""
        X = self.opd_idx
        if X is None:
            return {}
        uo, eo = self.us_sec[X], self.eu_sec[X]
        votes = {}

        def add(w, w2):
            if (self.usi.find(w) == X and self.eui.find(w2) == X and
                    (w - uo['addr']) % 8 == 0 and (w2 - eo['addr']) % 8 == 0):
                e, e2 = self.U.u32(w), self.E.u32(w2)
                d = votes.setdefault(e, {})
                d[e2] = d.get(e2, 0) + 1
        for idx, r in self.data_res.items():
            if idx == X or self.us_sec[idx]['flags'] & SHF_EXEC or idx == self.toc_idx:
                continue
            for i0, j0, ln in r['runs']:
                for t in range(ln):
                    add(r['A'][i0 + t], r['B'][j0 + t])
        return votes

    def refine_calls(self, rounds=6, extra=None, extra_changed_only=False):
        """re-place code by what identical callers call. Masked compare cannot tell apart
        look-alikes that differ only in their own call targets; where their order differs,
        the positional pick is wrong and the callers show it. A target is moved when at
        least 2 identical call sites, 80% of them, agree and the piece is identical
        (modulo b/bl) there; otherwise (changed code) only its entry is mapped, and only
        if it is an EU function start (EU list) or the prologue agrees."""
        st, eu = self.code['status'], self.code['eu']
        chg = {c['us']: c for c in self.changed}
        moved = entries = lost = 0
        changes = 0
        for _r in range(rounds):
            cv = self.call_votes()
            votes = cv if extra_changed_only else self.merge_ev(cv, extra or {})
            vk = {}
            for k, (a, b) in enumerate(self.funcs):
                d = votes.get(a)
                need = 2
                if extra_changed_only and st[k] != 0 and extra and a in extra:
                    d = self.merge_ev({a: d or {}}, {a: extra[a]})[a]
                    need = 1
                if not d:
                    continue
                tot = sum(d.values())
                t2, n = max(d.items(), key=lambda x: x[1])
                if tot >= need and n >= 0.8 * tot and not (st[k] != 2 and eu[k] == t2):
                    same = self._eq_at(a, b, t2)
                    if not same:
                        if self.eu_funcs_raw is not None:
                            if t2 not in self.eu_start_set:
                                continue
                        elif self._prologue_eq(a, b, t2) < 2:
                            continue
                    vk[k] = (t2, n, same)
            if not vk:
                break
            old = {k: (st[k], eu[k]) for k in vk}
            for k, (t2, n, same) in vk.items():
                st[k], eu[k] = (0 if same else 1), t2
            # conflicts: two pieces on one EU entry, or overlapping identical pieces
            spans = sorted((eu[k], eu[k] + (self.funcs[k][1] - self.funcs[k][0] if st[k] == 0 else 4), k)
                           for k in range(len(st)) if st[k] != 2)

            def score(k):
                return votes.get(self.funcs[k][0], {}).get(eu[k], 0) + (0.5 if k not in vk else 0)
            losers = set()
            j = 0
            active = []
            for s0, e0, k in spans:
                active = [x for x in active if x[1] > s0 and x[2] not in losers]
                for x in active:
                    o = x[2]
                    if o in losers or k in losers:
                        continue
                    if score(k) > score(o):
                        losers.add(o)
                    else:
                        losers.add(k)
                if k not in losers:
                    active.append((s0, e0, k))
            for k in losers:
                if k in vk and old[k][0] != 2 and score(k) == 0:
                    st[k], eu[k] = old[k]
                else:
                    st[k], eu[k] = 2, -1
                    lost += 1
            ch = 0
            for k, (t2, n, same) in vk.items():
                if k in losers:
                    continue
                ch += 1
                a, b = self.funcs[k]
                if same:
                    moved += 1
                    if a in chg:
                        self.changed.remove(chg.pop(a))
                else:
                    entries += 1
                    c = chg.get(a)
                    if c is None:
                        c = dict(us=a, us_size=b - a, eu=None, eu_size=None,
                                 prologue_words_equal=self._prologue_eq(a, b, t2), note='',
                                 gap_us_funcs=None, gap_eu_funcs=None, _lo=0, _hi=0)
                        chg[a] = c
                        self.changed.append(c)
                    c['eu'] = t2
                    c['note'] = (c['note'] + '; ' if c['note'] else '') + \
                        f'entry from call graph / function pointers ({n} agreeing votes)'
            for k in losers:
                a, b = self.funcs[k]
                if st[k] == 2 and a not in chg:
                    c = dict(us=a, us_size=b - a, eu=None, eu_size=None, prologue_words_equal=0,
                             note='lost its EU place to code the callers point at',
                             gap_us_funcs=None, gap_eu_funcs=None, _lo=0, _hi=0)
                    chg[a] = c
                    self.changed.append(c)
            changes += ch
            if not ch:
                break
        # a changed entry that votes contradict (and that could not be moved) is dropped
        votes = self.call_votes()
        dropped = 0
        for k in range(len(st)):
            if st[k] != 1:
                continue
            a = self.funcs[k][0]
            d = dict(votes.get(a, {}))
            if extra and a in extra:
                d = self.merge_ev({a: d}, {a: extra[a]})[a]
            if d and max(d.items(), key=lambda x: x[1])[0] != eu[k]:
                st[k], eu[k] = 2, -1
                dropped += 1
                c = chg.get(a)
                if c is not None:
                    c['note'] += '; entry refused: call sites / function pointers point elsewhere'
        # pieces left without a place: take a free EU spot where they are identical, if
        # there is exactly one in the window
        ua, ea = self.dom_us[0], self.dom_eu[0]
        delta = (self.dom_eu[1] - ea) - (self.dom_us[1] - ua)
        lo, hi = min(0, delta) - self.window, max(0, delta) + self.window
        spans = sorted((eu[k], eu[k] + (self.funcs[k][1] - self.funcs[k][0] if st[k] == 0 else 4))
                       for k in range(len(st)) if st[k] != 2)
        starts = [x[0] for x in spans]

        def free(x, y):
            i = bisect.bisect_right(starts, x) - 1
            if i >= 0 and spans[i][1] > x:
                return False
            return i + 1 >= len(spans) or spans[i + 1][0] >= y
        replaced = 0
        for k in range(len(st)):
            if st[k] != 2:
                continue
            a, b = self.funcs[k]
            ndl = self.um[a - ua:b - ua]
            p0, p1 = max(0, a - ua + lo), min(len(self.em), b - ua + hi)
            res, p = [], self.em.find(ndl, p0, p1)
            while p != -1 and len(res) < 8:
                if p % 4 == 0 and free(ea + p, ea + p + b - a):
                    res.append(ea + p)
                p = self.em.find(ndl, p + 1, p1)
            if len(res) == 1:
                st[k], eu[k] = 0, res[0]
                i = bisect.bisect_left(starts, res[0])
                starts.insert(i, res[0])
                spans.insert(i, (res[0], res[0] + b - a))
                replaced += 1
                if a in chg:
                    self.changed.remove(chg.pop(a))
        for c in self.changed:
            k = bisect.bisect_left(self.f_starts, c['us'])
            if st[k] == 2:
                c['eu'] = None
        self.changed.sort(key=lambda c: c['us'])
        nm = sum(1 for s in st if s == 0)
        self.log(f'code: call-graph refinement moved {moved} pieces, mapped {entries} changed '
                 f'entries, {lost} lost their place, {replaced} re-placed in a free identical spot, '
                 f'{dropped} changed entries dropped as contradicted; now {nm}/{len(st)} pieces identical, '
                 f'{len(self.changed)} changed ({sum(1 for c in self.changed if c["eu"] is not None)} '
                 f'with a mapped entry)')
        return changes

    def _prologue_eq(self, a, b, eu_addr):
        ua, ea = self.dom_us[0], self.dom_eu[0]
        n = 0
        while a + 4 * n < b and n < 16:
            p, q = a - ua + 4 * n, eu_addr - ea + 4 * n
            if q + 4 > len(self.em) or self.um[p:p + 4] != self.em[q:q + 4]:
                break
            n += 1
        return n

    def code_tr(self, a, identical_only=False):
        """None if not mappable (identical_only: entries of changed code count as unknown)"""
        ua, ub = self.dom_us
        if not ua <= a < ub:
            return None
        k = bisect.bisect_right(self.f_starts, a) - 1
        if k < 0 or a >= self.funcs[k][1]:
            return None
        st = self.code['status'][k]
        if st == 0:
            return self.code['eu'][k] + a - self.funcs[k][0]
        if st == 1 and a == self.funcs[k][0] and not identical_only:
            return self.code['eu'][k]
        return None

    # ---------------------------------------------------------------- data
    def ptr_eqv(self, w, w2, strict):
        """w (US word) and w2 (EU word) are the same pointer: same target section and, when
        the target is mappable, w2 == tr(w)"""
        T = self.usi.find(w, True)
        if T is None or self.eui.find(w2, True) != T:
            return False
        if T in self.dom_secs:
            t = self.code_tr(w)
            return t == w2 if t is not None else True
        if self.us_sec[T]['type'] == SHT_NOBITS:
            return w2 - w == self.eu_sec[T]['addr'] - self.us_sec[T]['addr']
        if T == self.opd_idx and strict is not None:
            return self.opd_equiv(w, w2)
        if strict is not None:
            try:
                return strict.tr(w) == w2
            except (KeyError, ValueError):
                pass
        return True

    def blocks(self, idx, strict, evidence):
        """seed-and-extend matching of one section (objects may move): returns runs,
        ambiguous words with their candidate EU offsets"""
        us, eu = self.us_sec[idx], self.eu_sec[idx]
        A, B = self.U.words(us), self.E.words(eu)
        if us['flags'] & SHF_EXEC:
            A, B = mask_imm(A), mask_imm(B)
        n, m = len(A), len(B)
        cache = {}

        def eqv(i, j):
            x, y = A[i], B[j]
            if x == y:
                return True
            r = cache.get((x, y))
            if r is None:
                r = cache[(x, y)] = self.ptr_eqv(x, y, strict)
            return r

        # seeds: start, end, pointer evidence, unique exact 4-grams
        seeds = [(0, 0), (n - 1, m - 1)]
        us0, eu0 = us['addr'], eu['addr']
        for w, vals in evidence.items():
            if us0 <= w < us0 + 4 * n and len(vals) == 1:
                w2 = next(iter(vals))
                if eu0 <= w2 < eu0 + 4 * m and (w - us0) % 4 == (w2 - eu0) % 4:
                    seeds.append(((w - us0) // 4, (w2 - eu0) // 4))
        if n == m:
            seeds.append((0, 0))
        ug, eg = {}, {}
        for q in range(0, m - 3):
            k = (B[q], B[q + 1], B[q + 2], B[q + 3])
            eg[k] = -1 if k in eg else q
        for p in range(0, n - 3):
            k = (A[p], A[p + 1], A[p + 2], A[p + 3])
            ug[k] = -1 if k in ug else p
        for k, p in ug.items():
            if p >= 0 and len(set(k)) > 1:
                q = eg.get(k, -1)
                if q >= 0:
                    seeds.append((p, q))
        cov = {}            # diag -> sorted list of [start, end)
        blocks = []
        for i, j in seeds:
            if not (0 <= i < n and 0 <= j < m):
                continue
            d = j - i
            iv = cov.setdefault(d, [])
            k = bisect.bisect_right(iv, [i, 1 << 60]) - 1
            if k >= 0 and iv[k][0] <= i < iv[k][1]:
                continue
            if not eqv(i, j):
                continue
            a = i
            while a > 0 and a + d > 0 and eqv(a - 1, a - 1 + d):
                a -= 1
            b = i + 1
            while b < n and b + d < m and eqv(b, b + d):
                b += 1
            bisect.insort(iv, [a, b])
            blocks.append((a, b, d))
        # drop blocks contradicted by evidence (an address inside them that code or a
        # pointer shows going somewhere else)
        evk = sorted(a for a in self.ev_code if us0 <= a < us0 + 4 * n)
        evidence_c = self.ev_code
        kept, dropped = [], 0
        for a, b, d in blocks:
            lo = bisect.bisect_left(evk, us0 + 4 * a)
            hi = bisect.bisect_left(evk, us0 + 4 * b)
            bad = False
            for x in evk[lo:hi]:
                if next(iter(evidence_c[x])) != x - us0 + eu0 + 4 * d:
                    bad = True
                    break
            if bad:
                dropped += 1
            else:
                kept.append((a, b, d))
        blocks = kept
        # resolve: a US word covered by blocks of different diagonals, or two US words
        # landing on one EU word, is ambiguous
        diag = [None] * n
        multi = {}
        for a, b, d in blocks:
            for i in range(a, b):
                c = diag[i]
                if c is None:
                    diag[i] = d
                elif c != d:
                    multi.setdefault(i, {c}).add(d)
        claims = [0] * m
        for i in range(n):
            if diag[i] is not None and i not in multi:
                claims[i + diag[i]] += 1
        amb = {}
        for i in range(n):
            if diag[i] is None:
                continue
            if i in multi:
                amb[i] = sorted(multi[i])
            elif claims[i + diag[i]] > 1:
                amb[i] = [diag[i]]
        # context: an ambiguous stretch whose clean neighbours on both sides have the same
        # shift, which is one of its candidates and lands on unclaimed EU words, takes it
        claimed = {i + diag[i] for i in range(n) if diag[i] is not None and i not in amb}
        ctx = {}
        keys = sorted(amb)
        k = 0
        while k < len(keys):
            s0 = keys[k]
            e0 = s0 + 1
            while k + 1 < len(keys) and keys[k + 1] == e0:
                k += 1
                e0 += 1
            k += 1
            lft = diag[s0 - 1] if s0 > 0 and (s0 - 1) not in amb else None
            rgt = diag[e0] if e0 < n and e0 not in amb else None
            if lft is None or lft != rgt:
                continue
            d = lft
            if all(d in amb[i] and 0 <= i + d < m and (i + d) not in claimed
                   for i in range(s0, e0)):
                for i in range(s0, e0):
                    ctx[i] = d
                    claimed.add(i + d)
                    del amb[i]

        def mkruns(pred, dg):
            out = []
            for i in range(n):
                if not pred(i):
                    continue
                if out and out[-1][0] + out[-1][2] == i and out[-1][1] - out[-1][0] == dg(i):
                    out[-1][2] += 1
                else:
                    out.append([i, i + dg(i), 1])
            return out
        runs = mkruns(lambda i: diag[i] is not None and i not in amb and i not in ctx,
                      lambda i: diag[i])
        cruns = mkruns(lambda i: i in ctx, lambda i: ctx[i])
        return dict(A=A, B=B, runs=runs, ctx=cruns, amb=amb, n=n, m=m, nblocks=len(blocks),
                    nseeds=len(seeds), ndropped=dropped)

    def code_evidence(self, parity=None):
        """(US address -> EU address) pairs from TOC-based accesses in identical functions:
        a register loaded from TOC slot s holds US word s / EU word s (same slot offset);
        a D-form access or addi off that register gives a US/EU target pair. Straight-line
        tracking only (registers dropped at branch targets, calls, and any other write)."""
        U = self.U
        Tu = self.usi.find(self.us_funcs_raw[0][2])
        tus, tes = self.us_sec[Tu], self.eu_sec[Tu]
        TW_u, TW_e = U.words(tus), self.E.words(tes)
        toc_shift = tes['addr'] - tus['addr']
        toc_of = {x[0]: x[2] for x in self.us_funcs_raw}
        ua = self.dom_us[0]
        W = self.us_w
        ev = {}
        LOADS = {32, 33, 34, 35, 40, 41, 42, 43, 46, 58}
        MEM = set(range(32, 56)) | {58, 62}
        UPD = {33, 35, 37, 39, 41, 43, 45, 47, 49, 51, 53, 55}
        WRITE_RA = {20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30}
        WRITE_RT = {7, 8, 12, 13}

        def s16(x):
            return x - 0x10000 if x & 0x8000 else x

        def add(a, b):
            d = ev.get(a)
            if d is None:
                ev[a] = {b: 1}
            else:
                d[b] = d.get(b, 0) + 1

        for k, (a, b) in enumerate(self.funcs):
            if self.code['status'][k] != 0 or a not in toc_of:
                continue
            if parity is not None and k % 2 != parity:
                continue
            tu = toc_of[a]
            te = tu + toc_shift
            labels = set()
            for q in range(a, b, 4):
                w = W[(q - ua) // 4]
                op = w >> 26
                if op == 16 and not w & 2:
                    labels.add(q + s16(w & 0xFFFC))
                elif op == 18 and not w & 2:
                    li = w & 0x03FFFFFC
                    labels.add(q + (li - 0x04000000 if li & 0x02000000 else li))
            regs = {}
            for q in range(a, b, 4):
                if q in labels:
                    regs = {}
                w = W[(q - ua) // 4]
                op = w >> 26
                rt, ra = (w >> 21) & 31, (w >> 16) & 31
                d = s16(w & 0xFFFF)
                if op in MEM:
                    if op in (58, 62):
                        d = s16(w & 0xFFFC)
                    if ra == 2:
                        o = (tu + d - tus['addr']) // 4
                        add(tu + d, te + d)
                        if op == 32 and 0 <= o < len(TW_u) and (tu + d) % 4 == 0:
                            regs[rt] = (TW_u[o], TW_e[o])
                            continue
                    elif ra and ra in regs:
                        vu, ve = regs[ra]
                        add(vu + d, ve + d)
                    if op in LOADS:
                        regs.pop(rt, None)
                    if op in UPD:
                        regs.pop(ra, None)
                    continue
                if op == 14 or op == 15:
                    if ra and ra in regs:
                        vu, ve = regs[ra]
                        if op == 14:
                            regs[rt] = (vu + d, ve + d)
                            add(vu + d, ve + d)
                        else:
                            regs[rt] = (vu + (d << 16), ve + (d << 16))
                    else:
                        regs.pop(rt, None)
                    continue
                if op == 18 or op == 17:
                    if w & 1 or op == 17:
                        for r in (0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12):
                            regs.pop(r, None)
                    else:
                        regs = {}
                    continue
                if op == 19:
                    if w & 1:
                        for r in (0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12):
                            regs.pop(r, None)
                    elif ((w >> 1) & 0x3FF) in (16, 528):
                        regs = {}
                    continue
                if op in (10, 11, 16, 59, 63, 4, 2, 3):
                    continue
                if op in WRITE_RA:
                    regs.pop(ra, None)
                elif op in WRITE_RT:
                    regs.pop(rt, None)
                else:
                    regs.pop(rt, None)
                    regs.pop(ra, None)
        return ev

    def opd_equiv(self, w, w2):
        """pointers into .opd are the same function pointer when the descriptors they point
        at hold {entry, toc} that map onto each other (duplicate descriptors are common)"""
        us, eu = self.us_sec[self.opd_idx], self.eu_sec[self.opd_idx]
        if (w - us['addr']) % 8 or (w2 - eu['addr']) % 8:
            return (w - us['addr']) % 8 == (w2 - eu['addr']) % 8
        e, t = self.U.u32(w), self.U.u32(w + 4)
        e2, t2 = self.E.u32(w2), self.E.u32(w2 + 4)
        if None in (e, t, e2, t2):
            return False
        te = self.code_tr(e, identical_only=True)
        toc_d = self.eu_sec[self.toc_idx]['addr'] - self.us_sec[self.toc_idx]['addr']
        if t2 != t + toc_d:
            return False
        return te == e2 if te is not None else True

    def opd_table(self):
        """.opd: descriptor-for-descriptor. A US descriptor {entry, toc} maps to the EU
        descriptor holding {tr(entry), tr(toc)}; duplicates (one copy per module is common)
        are settled by the shift of the nearest uniquely placed neighbour. A descriptor
        whose entry is a changed function is placed by context only when its neighbours on
        both sides share one shift and that EU slot is free; its EU entry then also gives
        the changed function's entry (if it lies in the right EU gap)."""
        opd = self.us_funcs_raw[0][3]
        X = self.usi.find(opd) if opd else None
        if X is None:
            return {}
        us, eu = self.us_sec[X], self.eu_sec[X]
        A, B = self.U.words(us), self.E.words(eu)
        n, m = len(A) // 2, len(B) // 2
        toc_d = self.eu_sec[self.toc_idx]['addr'] - self.us_sec[self.toc_idx]['addr']

        def toc_tr(t):
            return t + toc_d if self.usi.find(t) == self.toc_idx else None
        where = {}
        for q in range(m):
            where.setdefault((B[2 * q], B[2 * q + 1]), []).append(q)
        cands = []
        for p in range(n):
            e, t = self.code_tr(A[2 * p]), toc_tr(A[2 * p + 1])
            cands.append(where.get((e, t), []) if e is not None and t is not None else None)
        pos = [None] * n
        taken = set()
        for p in range(n):
            if cands[p] is not None and len(cands[p]) == 1:
                pos[p] = cands[p][0]
                taken.add(pos[p])
        uniq = [p for p in range(n) if pos[p] is not None]
        for p in range(n):
            c = cands[p]
            if not c or len(c) < 2:
                continue
            k = bisect.bisect_left(uniq, p)
            shifts = []
            for kk in (k - 1, k):
                if 0 <= kk < len(uniq):
                    shifts.append((abs(uniq[kk] - p), pos[uniq[kk]] - uniq[kk]))
            shifts.sort()
            for _dist, s in shifts:
                if p + s in c and p + s not in taken:
                    pos[p] = p + s
                    taken.add(p + s)
                    break
        ctx = {}
        for p in range(n):
            if pos[p] is not None or cands[p] is not None:
                continue
            lp = next((q for q in range(p - 1, -1, -1) if pos[q] is not None), None)
            rp = next((q for q in range(p + 1, n) if pos[q] is not None), None)
            if lp is None or rp is None:
                continue
            s = pos[lp] - lp
            if pos[rp] - rp != s or not 0 <= p + s < m or p + s in taken:
                continue
            ctx[p] = p + s
            taken.add(p + s)
        # changed-function entries from their descriptor position
        fidx = {a: k for k, (a, _b) in enumerate(self.funcs)}
        chg = {c['us']: c for c in self.changed}
        got = 0
        for p, q in ctx.items():
            a = A[2 * p]
            k = fidx.get(a)
            c = chg.get(a)
            if k is None or c is None:
                continue
            e2 = B[2 * q]
            ok = c['_lo'] <= e2 < c['_hi'] and (self.eu_funcs_raw is None or e2 in self.eu_start_set)
            if self.code['status'][k] == 1:
                if self.code['eu'][k] != e2:
                    c['note'] += f'; OPD position says 0x{e2:X} -> entry refused (conflict)'
                    self.code['status'][k], self.code['eu'][k], c['eu'] = 2, -1, None
                else:
                    c['note'] += '; OPD position agrees'
            elif ok:
                self.code['status'][k], self.code['eu'][k], c['eu'] = 1, e2, e2
                c['note'] += f'; entry from its OPD descriptor position (neighbours share one shift)'
                got += 1
        def runs_of(mp):
            out = []
            for p in sorted(mp):
                q = mp[p]
                if out and out[-1][0] + out[-1][2] == 8 * p and out[-1][1] - out[-1][0] == 8 * (q - p):
                    out[-1][2] += 8
                else:
                    out.append([8 * p, 8 * q, 8])
            return out
        placed = {p: pos[p] for p in range(n) if pos[p] is not None}
        nun = sum(1 for p in range(n) if pos[p] is None and p not in ctx)
        self.log(f'opd: sec {X}: {n} US / {m} EU descriptors; {len(placed)} placed by '
                 f'{{entry,toc}} ({sum(1 for p in placed if len(cands[p]) > 1)} of them duplicates '
                 f'settled by neighbour shift), {len(ctx)} by context, {nun} not placed; '
                 f'{got} changed-function entries taken from the OPD')
        return {X: dict(runs=runs_of(placed), context=runs_of(ctx))}

    def import_tables(self):
        """PRX import stubs: stub section X (32-byte stubs, stub k loads slot k), slot
        section Y (slot k -> stub k), FNID section Z (NID k), parallel tables. EU has the
        same NIDs in another order, so map entry k -> the EU entry with the same NID.
        Returns {section idx: runs} or {} when the structure is not found."""
        out = {}
        for us_x, eu_x in self.pairs:
            if not us_x['flags'] & SHF_EXEC or us_x['idx'] in self.dom_secs or us_x['size'] % 32:
                continue
            X = us_x['idx']
            ys = [p for p in self.pairs if p[0]['type'] != SHT_NOBITS and p[0]['size'] and
                  not p[0]['flags'] & SHF_EXEC and
                  all(self.usi.find(w) == X for w in self.U.words(p[0])) and
                  all(self.eui.find(w) == X for w in self.E.words(p[1]))]
            if len(ys) != 1:
                continue
            us_y, eu_y = ys[0]
            n = us_y['size'] // 4
            if eu_y['size'] != us_y['size'] or n * 32 != us_x['size']:
                continue
            Y = us_y['idx']
            zs = [p for p in self.pairs if p[0]['size'] == us_y['size'] and p[0]['idx'] not in (X, Y)
                  and not p[0]['flags'] & (SHF_EXEC | SHF_WRITE) and p[1]['size'] == us_y['size']
                  and len(set(self.U.words(p[0]))) == n
                  and sorted(self.U.words(p[0])) == sorted(self.E.words(p[1]))]
            if len(zs) != 1:
                continue
            us_z, eu_z = zs[0]
            Z = us_z['idx']
            nid_u, nid_e = self.U.words(us_z), self.E.words(eu_z)
            eidx = {v: k for k, v in enumerate(nid_e)}
            perm = [eidx[v] for v in nid_u]
            yu, ye = self.U.words(us_y), self.E.words(eu_y)
            out[Z] = [[4 * k, 4 * perm[k], 4] for k in range(n)]
            out[Y] = [[4 * k, 4 * perm[k], 4] for k in range(n)]
            out[X] = sorted([yu[k] - us_x['addr'], ye[perm[k]] - eu_x['addr'], 32] for k in range(n))
            self.log(f'imports: stubs sec {X}, slots sec {Y}, FNIDs sec {Z}: {n} imports, '
                     f'{sum(1 for k in range(n) if perm[k] == k)} at the same index, rest matched by NID')
        return out

    def collect_evidence(self, res):
        """pointer pairs (US target -> EU target) from aligned words of data sections, TOC
        excluded (TOC words are often section anchors, not object addresses)"""
        ev = {}
        for idx, r in res.items():
            if self.us_sec[idx]['flags'] & SHF_EXEC or idx == self.toc_idx:
                continue
            A, B = r['A'], r['B']
            for i0, j0, ln in r['runs']:
                for t in range(ln):
                    w, w2 = A[i0 + t], B[j0 + t]
                    if w == w2:
                        continue        # equal values: often integers that look like addresses
                    T = self.usi.find(w, True)
                    if T is not None and self.eui.find(w2, True) == T:
                        d = ev.setdefault(w, {})
                        d[w2] = d.get(w2, 0) + 1
        return ev

    @staticmethod
    def merge_ev(*evs):
        out = {}
        for ev in evs:
            for a, d in ev.items():
                o = out.setdefault(a, {})
                for b, c in d.items():
                    o[b] = o.get(b, 0) + c
        return out

    def build_data(self):
        self.f_starts = [f[0] for f in self.funcs]
        self.toc_idx = self.usi.find(self.us_funcs_raw[0][2])
        opd = self.us_funcs_raw[0][3]
        self.opd_idx = self.usi.find(opd) if opd else None
        evc = self.code_evidence()
        nconf = sum(1 for d in evc.values() if len(d) > 1)
        self.log(f'data: code-reference evidence (TOC-based accesses in identical functions): '
                 f'{len(evc)} US targets, {nconf} with conflicting EU targets (dropped)')
        full = {a: d for a, d in evc.items() if len(d) == 1}
        self.ev_code = full
        for rnd in range(4):
            self._build_data_once(evc, full)
            if rnd == 3:
                break
            # function pointers in strictly aligned data (vtables etc.) that point at the
            # descriptor of a changed function place that function's entry
            fv = self.fptr_votes()
            got = self.refine_calls(extra=fv, extra_changed_only=True)
            self.log(f'code: function-pointer votes from aligned data: '
                     f'{sum(sum(d.values()) for d in fv.values())} for {len(fv)} US entries; '
                     f'{got} placements changed' + ('; rebuilding the data map' if got else ''))
            if not got:
                break

    def _build_data_once(self, evc, full):
        self.overrides = {}
        for idx, runs in self.import_tables().items():
            self.overrides[idx] = dict(runs=runs, context=[], note='import table (by NID)')
        for idx, o in self.opd_table().items():
            self.overrides[idx] = dict(o, note='OPD (by descriptor)')
        # hold-out run: build with the evidence of even-numbered functions only and test the
        # data map on targets that only odd-numbered functions reach
        train = {a: d for a, d in self.code_evidence(0).items() if len(d) == 1}
        test = {a: d for a, d in self.code_evidence(1).items() if len(d) == 1 and a not in train}
        log = self.log
        self.log = lambda *x: None
        self.ev_code = train
        self._data_pipeline()
        self.log = log
        self.holdout = self._ref_check(test)
        h = self.holdout
        self.log(f'data: hold-out (map built from even functions, tested on {h["total"]} targets '
                 f'reached only by odd functions): {h["agree"]} agree, {h["refused"]} refused, '
                 f'{h["disagree"]} disagree ({h["pct_of_mappable"]:.3f}% of mappable)')
        self.ev_code = full
        self._data_pipeline()

    def _ref_check(self, evd):
        M = self.map
        tot = agree = refused = 0
        bad = []
        per = {}
        for a, d in evd.items():
            T = self.usi.find(a)
            if T is None or T in self.dom_secs or T == self.toc_idx:
                continue
            b2 = next(iter(d))
            tot += 1
            p = per.setdefault(T, [0, 0, 0])
            p[0] += 1
            try:
                t = M.tr(a)
            except (KeyError, ValueError):
                refused += 1
                p[2] += 1
                continue
            if t == b2:
                agree += 1
                p[1] += 1
            elif len(bad) < 40:
                bad.append(f'0x{a:X}: map 0x{t:X}, code says 0x{b2:X}')
        mapped = tot - refused
        return dict(total=tot, agree=agree, refused=refused, disagree=mapped - agree,
                    pct_of_mappable=round(100.0 * agree / mapped if mapped else 100.0, 3),
                    per_section={k: dict(refs=v[0], agree=v[1], refused=v[2])
                                 for k, v in sorted(per.items())},
                    sample_disagreements=bad)

    def _data_pipeline(self):
        ev = self.ev_code
        tmp = None
        const = set()
        for pas in (1, 2, 3):
            res, secs_json = {}, []
            for us, eu in self.pairs:
                idx = us['idx']
                if idx in self.dom_secs:
                    continue
                if us['type'] == SHT_NOBITS:
                    secs_json.append(dict(idx=idx, kind='nobits', us_start=us['addr'],
                                          us_size=us['size'], eu_start=eu['addr'],
                                          eu_size=eu['size']))
                    continue
                if idx in self.overrides:
                    o = self.overrides[idx]
                    r = dict(A=self.U.words(us), B=self.E.words(eu), n=us['size'] // 4,
                             m=eu['size'] // 4, amb={}, nseeds=0, nblocks=0, ndropped=0,
                             runs=[[a // 4, b // 4, c // 4] for a, b, c in o['runs']],
                             ctx=[[a // 4, b // 4, c // 4] for a, b, c in o['context']])
                    res[idx] = r
                    r['const'] = False
                    secs_json.append(dict(idx=idx, kind='piecewise', us_start=us['addr'],
                                          us_size=us['size'], eu_start=eu['addr'],
                                          eu_size=eu['size'], runs=o['runs'], context=o['context'],
                                          ambiguous=[], table=o['note']))
                    continue
                if pas == 1 or idx not in const:
                    r = self.blocks(idx, tmp, ev)
                else:
                    r = self.data_res_prev[idx]
                res[idx] = r
                full = (len(r['runs']) == 1 and r['runs'][0][:2] == [0, 0]
                        and r['runs'][0][2] == r['n'] == r['m'] and us['size'] == eu['size'])
                if pas == 1 and full:
                    const.add(idx)        # layout decided with the lax pointer test
                full = idx in const
                r['const'] = full
                ent = dict(idx=idx, kind='const' if full else 'piecewise', us_start=us['addr'],
                           us_size=us['size'], eu_start=eu['addr'], eu_size=eu['size'])
                if not full:
                    ent['runs'] = [[4 * a, 4 * b, 4 * c] for a, b, c in r['runs']]
                    ent['ambiguous'] = self._amb_ranges(r['amb'])
                    ent['context'] = [[4 * a, 4 * b, 4 * c] for a, b, c in r['ctx']]
                secs_json.append(ent)
            tmp = Map(self._json(secs_json))
            self.data_res_prev = res
            evp = self.collect_evidence(res)
            ev = self.merge_ev(self.ev_code, {a: d for a, d in evp.items() if len(d) == 1})
            ev = {a: d for a, d in ev.items() if len(d) == 1}
        self.data_res = res
        self.secs_json = secs_json
        self.evidence = ev
        # ambiguous words: settle with exact-address evidence (code refs, data pointers)
        evk = sorted(ev)
        nres = namb = 0
        self.amb_report = []
        for ent in secs_json:
            if ent['kind'] != 'piecewise':
                continue
            resolved = {}
            for off, ln, cands in ent['ambiguous']:
                namb += ln // 4
                a0 = ent['us_start'] + off
                for a in evk[bisect.bisect_left(evk, a0):bisect.bisect_left(evk, a0 + ln)]:
                    vals = list(ev[a])
                    cand = [ent['eu_start'] + a - ent['us_start'] + 4 * dd for dd in cands]
                    ok = len(vals) == 1 and vals[0] in cand
                    if ok:
                        wk, wv = f'0x{a & ~3:X}', vals[0] - (a & 3)
                        if resolved.get(wk, wv) != wv:
                            resolved[wk] = None      # conflicting evidence inside one word
                        else:
                            resolved[wk] = wv
                            nres += 1
                    self.amb_report.append(dict(us=a, evidence=sorted(vals), candidates=cand,
                                                settled=ok, from_code=a in self.ev_code))
            ent['resolved'] = {k: v for k, v in resolved.items() if v is not None}
        self.log(f'data: {namb} ambiguous US words; {nres} addresses in them settled by pointer evidence')
        for ent in secs_json:
            r = res.get(ent['idx'])
            if ent['kind'] == 'piecewise':
                cov = sum(x[2] for x in ent['runs'])
                cx = sum(x[2] for x in ent['context'])
                moves = len({x[1] - x[0] for x in ent['runs']})
                self.log(f'  sec {ent["idx"]:2d} piecewise: US 0x{ent["us_start"]:X}+0x{ent["us_size"]:X}'
                         f' -> EU 0x{ent["eu_start"]:X}+0x{ent["eu_size"]:X}: {len(ent["runs"])} runs'
                         f' ({moves} distinct shifts), mapped 0x{cov:X} B (+0x{cx:X} by context), refused 0x{ent["us_size"] - cov - cx:X} B'
                         f' (ambiguous {sum(x[1] for x in ent["ambiguous"])} B); seeds {r["nseeds"]}, blocks {r["nblocks"]}'
                         f' (+{r["ndropped"]} dropped as contradicted by evidence)')
            elif ent['kind'] == 'const':
                self.log(f'  sec {ent["idx"]:2d} const: US 0x{ent["us_start"]:X} -> EU 0x{ent["eu_start"]:X}'
                         f' ({ent["eu_start"] - ent["us_start"]:+#x}), size 0x{ent["us_size"]:X}')
            else:
                self.log(f'  sec {ent["idx"]:2d} nobits: US 0x{ent["us_start"]:X} -> EU 0x{ent["eu_start"]:X}'
                         f' ({ent["eu_start"] - ent["us_start"]:+#x}), size 0x{ent["us_size"]:X}'
                         f'{"" if ent["us_size"] == ent["eu_size"] else " SIZE DIFFERS 0x%X" % ent["eu_size"]}')
        self.map = Map(self._json(secs_json))

    @staticmethod
    def _amb_ranges(amb):
        out = []
        for i in sorted(amb):
            c = amb[i]
            if out and out[-1][0] + out[-1][1] == 4 * i and out[-1][2] == c:
                out[-1][1] += 4
            else:
                out.append([4 * i, 4, c])
        return out

    def _json(self, secs_json):
        f = self.funcs
        return dict(
            version=1,
            us=dict(path=self.U.path, sha256=self.U.sha256, size=len(self.U.data)),
            eu=dict(path=self.E.path, sha256=self.E.sha256, size=len(self.E.data)),
            code=dict(us_start=self.dom_us[0], us_end=self.dom_us[1], eu_start=self.dom_eu[0],
                      eu_end=self.dom_eu[1],
                      functions=dict(us=[a for a, _ in f], us_end=[b for _, b in f],
                                     eu=self.code['eu'], status=self.code['status'])),
            sections=secs_json)

    # ---------------------------------------------------------------- checks
    def checks(self):
        M = self.map
        fails = []
        rep = {}
        # (a) OPD descriptors
        opd_idx = self.usi.find(self.us_funcs_raw[0][3]) if self.us_funcs_raw[0][3] else None
        if opd_idx is not None:
            us, eu = self.us_sec[opd_idx], self.eu_sec[opd_idx]
            A = self.U.words(us)
            eu_by_entry = {}
            B = self.E.words(eu)
            for q in range(0, len(B) - 1, 2):
                eu_by_entry.setdefault(B[q], []).append((eu['addr'] + 4 * q, B[q + 1]))
            tot = ok = ok_any = 0
            why = {}
            bad = []
            for p in range(0, len(A) - 1, 2):
                tot += 1
                d, e, t = us['addr'] + 4 * p, A[p], A[p + 1]
                try:
                    d2 = M.tr(d)
                    e2, t2 = M.tr(e), M.tr(t)
                except (KeyError, ValueError) as x:
                    try:
                        M.tr(d)
                        k = 'entry/toc refused'
                    except (KeyError, ValueError):
                        k = 'descriptor address refused'
                    why[k] = why.get(k, 0) + 1
                    if len(bad) < 40:
                        bad.append(f'0x{d:X} {{0x{e:X},0x{t:X}}}: {x}')
                    continue
                if self.E.u32(d2) == e2 and self.E.u32(d2 + 4) == t2:
                    ok += 1
                else:
                    why['EU descriptor differs'] = why.get('EU descriptor differs', 0) + 1
                    if len(bad) < 40:
                        bad.append(f'0x{d:X} -> 0x{d2:X}: EU has {{0x{self.E.u32(d2):X},'
                                   f'0x{self.E.u32(d2 + 4):X}}} want {{0x{e2:X},0x{t2:X}}}')
                if any(tt == t2 for _a, tt in eu_by_entry.get(e2, [])):
                    ok_any += 1
            pct = 100.0 * ok / tot
            rep['a_opd'] = dict(total=tot, ok=ok, pct=round(pct, 3), failures_by_reason=why,
                                found_by_entry_search=ok_any, sample_failures=bad)
            self.log(f'check a) OPD: {ok}/{tot} descriptors map to an equal EU descriptor '
                     f'({pct:.3f}%); failures {why}')
            if pct < 99.0:
                fails.append('a')
        # (b) address-like words in identically laid-out sections (exec sections and the TOC
        # left out). Words whose US and EU values differ are relocated pointers: the main
        # number. Equal-valued words are pointers into unshifted areas or plain integers
        # that fall in the address range; they are counted separately.
        rel = [0, 0, 0]          # total, agree, refused
        eqv_ = [0, 0, 0]         # total, consistent (tr(w) == w), refused
        bad = []
        per = {}
        toc_st = [0, 0, 0]
        for ent in self.secs_json:
            if ent['kind'] != 'const':
                continue
            idx = ent['idx']
            r = self.data_res[idx]
            A, B = r['A'], r['B']
            if idx == self.toc_idx:
                for w, w2 in zip(A, B):
                    if self.usi.find(w, True) is None:
                        continue
                    toc_st[0] += 1
                    try:
                        toc_st[1] += M.tr(w) == w2
                    except (KeyError, ValueError):
                        toc_st[2] += 1
                continue
            if self.us_sec[idx]['flags'] & SHF_EXEC:
                continue
            st = [0, 0, 0, 0, 0]
            for w, w2 in zip(A, B):
                if self.usi.find(w, True) is None:
                    continue
                c = rel if w != w2 else eqv_
                c[0] += 1
                st[0 if w != w2 else 3] += 1
                try:
                    t = M.tr(w)
                except (KeyError, ValueError):
                    c[2] += 1
                    st[2] += w != w2
                    continue
                if t == w2:
                    c[1] += 1
                    st[1 if w != w2 else 4] += 1
                elif w != w2 and len(bad) < 40:
                    bad.append(f'sec {idx}: US word 0x{w:08X} -> tr 0x{t:08X}, EU word 0x{w2:08X}')
            per[idx] = dict(relocated=st[0], relocated_agree=st[1], relocated_refused=st[2],
                            equal_valued=st[3], equal_valued_consistent=st[4])
        mapped = rel[0] - rel[2]
        pct_m = 100.0 * rel[1] / mapped if mapped else 100.0
        rep['b_pointers'] = dict(relocated_words=rel[0], agree=rel[1], target_refused=rel[2],
                                 disagree=mapped - rel[1], pct_of_mappable=round(pct_m, 3),
                                 equal_valued_words=eqv_[0], equal_valued_consistent=eqv_[1],
                                 equal_valued_refused=eqv_[2], per_section=per,
                                 sample_disagreements=bad)
        self.log(f'check b) relocated pointer words (US != EU value) in const data sections: '
                 f'{rel[1]}/{rel[0]} agree, {rel[2]} targets refused, {mapped - rel[1]} disagree '
                 f'({pct_m:.3f}% of mappable). Equal-valued address-like words: {eqv_[0]}, '
                 f'{eqv_[1]} consistent with the map, {eqv_[0] - eqv_[1] - eqv_[2]} not '
                 f'(integers in the address range, or pointers the map gets wrong), {eqv_[2]} refused')
        rep['b_toc_words'] = dict(pointer_words=toc_st[0], agree=toc_st[1], refused=toc_st[2],
                                  note='TOC words are often section anchors (base+0x8000 used with '
                                       'negative displacements), so disagreement is expected; '
                                       'see check d for what code actually reaches')
        self.log(f'   (TOC section {self.toc_idx}, not in b: {toc_st[1]}/{toc_st[0]} words agree, '
                 f'{toc_st[2]} refused; anchors make these differ legitimately)')
        if pct_m < 98.0:
            fails.append('b')
        # (d) code-reference evidence vs data map (all = partly circular; hold-out = not)
        rep['d_code_refs_all'] = self._ref_check(self.ev_code)
        rep['d_code_refs_holdout'] = self.holdout
        r1, r2 = rep['d_code_refs_all'], self.holdout
        self.log(f'check d) data addresses reached by identical code via TOC/anchors: '
                 f'{r1["agree"]}/{r1["total"]} agree, {r1["refused"]} refused, {r1["disagree"]} disagree '
                 f'(this evidence also built the map); hold-out: {r2["agree"]}/{r2["total"]} agree, '
                 f'{r2["refused"]} refused, {r2["disagree"]} disagree ({r2["pct_of_mappable"]:.3f}% of mappable)')
        pct_d = min(r1['pct_of_mappable'], r2['pct_of_mappable'])
        if pct_d < 99.0:
            fails.append('d')
        # (c) function entries vs EU function list
        if self.eu_funcs_raw:
            tot = ok = 0
            bad = []
            for k, (a, b) in enumerate(self.funcs):
                if self.code['status'][k] == 2 or a not in self.orig_starts:
                    continue
                tot += 1
                e = self.code['eu'][k]
                if e in self.eu_start_set:
                    ok += 1
                elif len(bad) < 40:
                    bad.append(f'func_{a:08X} -> 0x{e:X}')
            nxt = sorted(self.orig_starts) + [self.dom_us[1]]
            whole = {a: nxt[i + 1] for i, a in enumerate(nxt[:-1])}
            same = [k for k in range(len(self.funcs)) if self.code['status'][k] == 0 and
                    self.funcs[k][0] in whole and all(
                        self.code['status'][kk] == 0 and
                        self.code['eu'][kk] - self.funcs[kk][0] == self.code['eu'][k] - self.funcs[k][0]
                        for kk in range(k, bisect.bisect_left(self.f_starts, whole[self.funcs[k][0]])))]
            endok = sum(1 for k in same if self.eu_end_of.get(self.code['eu'][k]) ==
                        self.code['eu'][k] + min(whole[self.funcs[k][0]], self.dom_us[1]) - self.funcs[k][0])
            # TOC of matched functions
            us_toc = {x[0]: x[2] for x in self.us_funcs_raw}
            eu_toc = {x[0]: x[2] for x in self.eu_funcs_raw}
            tt = tok = 0
            for k, (a, b) in enumerate(self.funcs):
                e = self.code['eu'][k]
                if self.code['status'][k] == 2 or e not in eu_toc or a not in us_toc:
                    continue
                tt += 1
                try:
                    tok += M.tr(us_toc[a]) == eu_toc[e]
                except (KeyError, ValueError):
                    pass
            pct = 100.0 * ok / tot
            n_eu = len(self.eu_funcs_raw)
            covered = len({self.code['eu'][k] for k in range(len(self.funcs))
                           if self.code['status'][k] != 2 and self.funcs[k][0] in self.orig_starts}
                          & self.eu_start_set)
            rep['c_entries'] = dict(mapped=tot, are_eu_starts=ok, pct=round(pct, 3),
                                    identical_funcs=len(same), identical_with_same_eu_end=endok,
                                    eu_function_count=n_eu, eu_starts_covered=covered,
                                    toc_checked=tt, toc_ok=tok, sample_failures=bad)
            self.log(f'check c) mapped entries that are EU function starts: {ok}/{tot} ({pct:.3f}%);'
                     f' identical funcs whose EU function also ends at the mapped end: {endok}/{len(same)};'
                     f' EU starts covered {covered}/{n_eu}; func TOC tr ok {tok}/{tt}')
            if pct < 99.5 or tok != tt:
                fails.append('c')
        else:
            self.log('check c) skipped (no --eu-functions)')
        # (e) call graph: every external b/bl of an identical piece lands on tr(US target)
        votes = self.call_votes()
        tot = ok = unm = 0
        bad = []
        for t, d in votes.items():
            tt = self.code_tr(t)
            for t2, n in d.items():
                tot += n
                if tt is None:
                    unm += n
                elif tt == t2:
                    ok += n
                elif len(bad) < 40:
                    bad.append(f'0x{t:X}: map 0x{tt:X}, {n} call sites go to 0x{t2:X}')
        rep['e_call_graph'] = dict(call_sites=tot, agree=ok, callee_unmapped=unm,
                                   disagree=tot - ok - unm, sample=bad)
        self.log(f'check e) external b/bl in identical code: {ok}/{tot} land on tr(US target), '
                 f'{unm} target a changed piece with no mapping, {tot - ok - unm} disagree '
                 f'(the refinement used these votes, so this is consistency, not independent)')
        # TOC bases
        tocs = sorted({x[2] for x in self.us_funcs_raw})
        tr_t = {}
        for t in tocs:
            try:
                tr_t[f'0x{t:X}'] = '0x%X %s' % M.info(t)
            except (KeyError, ValueError) as x:
                tr_t[f'0x{t:X}'] = f'ERROR {x}'
                fails.append('toc')
        if self.eu_funcs_raw:
            eu_t = sorted({x[2] for x in self.eu_funcs_raw})
            rep['toc_eu_function_list'] = [f'0x{t:X}' for t in eu_t]
        rep['toc_bases'] = tr_t
        self.log(f'TOC bases: {tr_t}' + (f'  (EU function list TOCs: {rep["toc_eu_function_list"]})'
                                         if self.eu_funcs_raw else ''))
        return rep, fails


def cmd_build(a):
    b = Builder(a.us_elf, a.eu_elf, a.us_functions, a.eu_functions, window=a.window)
    b.build_code()
    b.build_data()
    rep, fails = b.checks()
    j = b._json(b.secs_json)
    j['changed_functions'] = [dict({k: v for k, v in c.items() if not k.startswith('_')},
                                   us=f'0x{c["us"]:X}',
                                   eu=(f'0x{c["eu"]:X}' if c['eu'] is not None else None))
                              for c in b.changed]
    j['ambiguous_evidence'] = [dict(us=f'0x{x["us"]:X}', evidence=[f'0x{v:X}' for v in x['evidence']],
                                    candidates=[f'0x{v:X}' for v in x['candidates']])
                               for x in b.amb_report]
    j['checks'] = rep
    with open(a.out, 'w') as f:
        json.dump(j, f, separators=(',', ':'))
    print(f'wrote {a.out}')
    if fails:
        print(f'FAILED checks: {fails}')
        return 2
    return 0


def cmd_tr(a):
    m = load(a.map)
    rc = 0
    for s in a.addrs:
        try:
            e, kind = m.info(int(s, 0))
            print(f'0x{int(s, 0):08X} -> 0x{e:08X}  {kind}')
        except (KeyError, ValueError) as x:
            print(f'0x{int(s, 0):08X} -> error: {x.args[0] if x.args else x}')
            rc = 1
    return rc


# ----------------------------------------------------------------------------- patch scan
HEX = re.compile(r'(?<![0-9A-Za-z_])0x([0-9A-Fa-f]+)')
FUNC = re.compile(r'(?<![0-9A-Za-z])((?:spu_\d{4}_at_[0-9A-Fa-f]{8}_spu_)?)func_([0-9A-Fa-f]{8})(?![0-9A-Za-z])')
SPUDIR = re.compile(r'spu/spu_(\d{4})_at_([0-9A-Fa-f]{8})')
SPUDIR_ID = re.compile(r'(?<![0-9A-Za-z/])spu_(\d{4})_at_([0-9A-Fa-f]{8})')


def _hint(line, pos):
    pre = line[:pos].rstrip()
    if re.search(r'lr\s*=\s*$', pre):
        return 'lr (return address)'
    if re.search(r'vm_(read|write)\d*\(\s*$', pre):
        return 'vm access address'
    if re.search(r'(==|!=)\s*$', pre):
        return 'compared for equality'
    if re.search(r'(<|>|<=|>=)\s*$', pre):
        return 'range bound (<, >; likely a size/limit, not an address)'
    if re.search(r'[+\-*/&|^]\s*$', pre) or re.search(r'(-=|\+=)\s*$', pre):
        return 'operand of arithmetic (likely offset/size, not an address)'
    if '/*' in line[:pos] or line.lstrip('+- ').startswith('*') or '//' in line[:pos]:
        return 'in comment'
    return 'other'


def cmd_patch(a):
    m = load(a.map)
    us_elf = Elf(m.j['us']['path'])
    eu_elf = Elf(m.j['eu']['path'])
    lo = min(s['us_start'] for s in m.secs + [dict(us_start=m.dom[0])])
    hi = max(s['us_start'] + s['us_size'] for s in m.secs)
    fstart = set(m.f_us)
    out = {}
    spu = {}
    cur_file = None
    with open(a.patch, errors='replace') as f:
        lines = f.read().split('\n')
    in_hunk = False
    for ln, line in enumerate(lines, 1):
        if line.startswith('diff '):
            mm = re.search(r' b/(\S+)$', line)
            cur_file = mm.group(1) if mm else line
            in_hunk = False
            sm = SPUDIR.search(line)
            if sm:
                spu.setdefault(sm.group(0), []).append(ln)
            continue
        if line.startswith('+++') or line.startswith('---'):
            continue
        if line.startswith('@@'):
            in_hunk = True
            continue
        if not in_hunk or not line or line[0] not in '+- ':
            continue
        typ = {'+': 'added', '-': 'removed', ' ': 'context'}[line[0]]
        is_spu = cur_file is not None and cur_file.startswith('spu/')
        seen = set()
        for mt in FUNC.finditer(line):
            v = int(mt.group(2), 16)
            spu_name = bool(mt.group(1)) or is_spu
            key = ('func', v, spu_name, (mt.group(1) or cur_file) if spu_name else None)
            seen.add(mt.start(2) - 5)
            e = out.setdefault(key, dict(form='func_', us=v, lines=[],
                                         spu_file=(cur_file if is_spu else mt.group(1)) if spu_name else None))
            e['lines'].append([ln, typ])
        for mt in SPUDIR_ID.finditer(line):
            spu.setdefault('spu/' + mt.group(0), []).append(ln)
        for mt in HEX.finditer(line):
            v = int(mt.group(1), 16)
            if not lo <= v <= hi:
                continue
            key = ('const', v, is_spu, cur_file if is_spu else None)
            e = out.setdefault(key, dict(form='0x', us=v, lines=[], hints=set(),
                                         spu_file=cur_file if is_spu else None))
            e['lines'].append([ln, typ])
            e['hints'].add(_hint(line[1:], mt.start() - 1))
    res = []
    for key, e in sorted(out.items(), key=lambda kv: (kv[0][2], kv[0][1], kv[0][0])):
        v = e['us']
        r = dict(form=e['form'], us=f'0x{v:08X}', lines=e['lines'])
        if 'hints' in e:
            r['context'] = sorted(e['hints'])
        if e['spu_file']:
            r['spu_file'] = e['spu_file']
            r['eu'] = f'0x{v:08X}'
            r['kind'] = 'SPU local-store address (unchanged)'
            res.append(r)
            continue
        try:
            ev, kind = m.info(v)
            r['eu'] = f'0x{ev:08X}'
            r['kind'] = kind
            if e['form'] == 'func_' and v not in fstart:
                r['note'] = 'US address is not a function start in the US list'
        except (KeyError, ValueError) as x:
            r['eu'] = None
            r['reason'] = x.args[0] if x.args else str(x)
        res.append(r)
    # SPU image folders: file offset moves with the containing section
    spu_res = []
    for name, lns in sorted(spu.items()):
        off = int(SPUDIR.search(name).group(2), 16)
        va = None
        for s in us_elf.alloc():
            if s['type'] != SHT_NOBITS and s['off'] <= off < s['off'] + s['size']:
                va = s['addr'] + off - s['off']
        r = dict(folder=name, us_file_offset=f'0x{off:08X}', lines=sorted(set(lns)))
        try:
            ev = m.tr(va)
            eo = None
            for s in eu_elf.alloc():
                if s['type'] != SHT_NOBITS and s['addr'] <= ev < s['addr'] + s['size']:
                    eo = s['off'] + ev - s['addr']
            same = us_elf.data[off:off + 0x1000] == eu_elf.data[eo:eo + 0x1000]
            r.update(us_vaddr=f'0x{va:08X}', eu_vaddr=f'0x{ev:08X}', eu_file_offset=f'0x{eo:08X}',
                     eu_folder=re.sub(r'_at_[0-9A-Fa-f]{8}', f'_at_{eo:08X}', name),
                     first_4k_identical=same)
        except (KeyError, ValueError, TypeError) as x:
            r['error'] = str(x)
        spu_res.append(r)
    stubs = []
    if a.stubs:
        with open(a.stubs, errors='replace') as f:
            for ln, line in enumerate(f, 1):
                for mt in HEX.finditer(line):
                    v = int(mt.group(1), 16)
                    if v in {int(t, 16) for t in m.j['checks'].get('toc_bases', {})}:
                        try:
                            ev, kind = m.info(v)
                            stubs.append(dict(us=f'0x{v:08X}', eu=f'0x{ev:08X}', kind=kind, line=ln))
                        except (KeyError, ValueError) as x:
                            stubs.append(dict(us=f'0x{v:08X}', eu=None, reason=str(x), line=ln))
    j = dict(patch=a.patch, map=a.map,
             note=('every func_XXXXXXXX name and every 0x constant inside the EBOOT address range '
                   '(0x%X..0x%X) on added/removed/context lines; 0x constants in that range are not '
                   'necessarily addresses: see "context"' % (lo, hi)),
             entries=res, spu_folders=spu_res, stubs_toc=stubs)
    nmap = sum(1 for r in res if r.get('eu'))
    j['summary'] = dict(entries=len(res), mapped=nmap, unmappable=len(res) - nmap,
                        unmappable_list=[dict(us=r['us'], form=r['form'], reason=r['reason'],
                                              lines=[x[0] for x in r['lines']])
                                         for r in res if not r.get('eu')])
    with open(a.out, 'w') as f:
        json.dump(j, f, indent=1)
    print(f'{len(res)} entries, {nmap} mapped, {len(res) - nmap} not; '
          f'{len(spu_res)} SPU folders; {len(stubs)} stubs.cpp TOC constants -> {a.out}')
    for r in j['summary']['unmappable_list']:
        print(f'  {r["form"]:5s} {r["us"]}  lines {r["lines"][:6]}: {r["reason"]}')
    for r in spu_res:
        print(f'  {r["folder"]} -> {r.get("eu_folder")} identical={r.get("first_4k_identical")}')
    for r in stubs:
        print(f'  stubs.cpp:{r["line"]} {r["us"]} -> {r.get("eu")}')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sp = ap.add_subparsers(dest='cmd', required=True)
    b = sp.add_parser('build')
    b.add_argument('us_elf')
    b.add_argument('eu_elf')
    b.add_argument('us_functions')
    b.add_argument('-o', '--out', required=True)
    b.add_argument('--eu-functions')
    b.add_argument('--window', type=lambda s: int(s, 0), default=0x2000)
    t = sp.add_parser('tr')
    t.add_argument('map')
    t.add_argument('addrs', nargs='+')
    p = sp.add_parser('patch')
    p.add_argument('map')
    p.add_argument('patch')
    p.add_argument('--stubs')
    p.add_argument('-o', '--out', required=True)
    a = ap.parse_args()
    return {'build': cmd_build, 'tr': cmd_tr, 'patch': cmd_patch}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())

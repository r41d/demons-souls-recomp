#!/usr/bin/env python3
"""arshape.py - Arabic shaping + simplified bidi + wrapping for pre-rendered game text.

Pure Python 3, standard library only (unicodedata is used for bidi classes and in the self-test).

The game renderer (CCM fonts) draws code points left to right, one glyph per code point, with no
shaping and no bidi. So we do the work offline and store *visual-order presentation-form* text:

    logical Arabic  --shape()-->  logical presentation forms  --visual()-->  left-to-right glyph order

API
    shape(text, keep_harakat=False, token_re=None)  -> str   (logical order, presentation forms)
    visual(line, base='rtl', token_re=None)         -> str   (one line or '\\n'-separated lines)
    wrap(text, width_fn, max_width, ...)            -> list[str] visual lines, first = start of text
    layout(text, width_fn=None, max_width=None,...) -> str   shape + (wrap) + visual, '\\n' joined
    required_codepoints(texts, ...)                 -> dict  glyphs a corpus needs after shaping
    ccm_width_fn(ccm_path)                          -> width_fn from a CCM font's advances

Markup tokens (game tags such as <?select?> or <?blockName@12?>, %-style placeholders, plus any
substring matching a user regex) are atomic: never shaped, never split by wrap, never reversed
internally, and treated as a strong LTR unit by visual().

Run  python3 arshape.py --test               for the self-test
     python3 arshape.py --corpus FILE         to list the presentation forms a UTF-8 corpus needs
     python3 arshape.py --show "text"         to print shaped / visual code points
"""
import re
import sys
import unicodedata

# --------------------------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------------------------
# Game markup measured in the US FMGs: <?name?> and <?name@N?> (e.g. <?cancel?>, <?blockName@N?>).
# %-placeholders are essentially absent in the US text (only '%%' twice) but are supported anyway.
DEFAULT_TOKEN_PATTERN = r'<\?[^?>]*\?>|%%|%(?:\d+\$)?[-+ #0]*\d*(?:\.\d+)?[sdiufFeEgGxXoc]'


class Token(str):
    """An atomic markup unit. Subclass of str so it can be joined back into text."""
    __slots__ = ()


def _token_regex(token_re):
    if token_re is None:
        return re.compile(DEFAULT_TOKEN_PATTERN)
    if isinstance(token_re, str):
        return re.compile('(?:%s)|(?:%s)' % (token_re, DEFAULT_TOKEN_PATTERN))
    return token_re  # a compiled pattern replaces the default entirely


def tokenize(text, token_re=None):
    """Split text into a list of single characters and Token objects."""
    rx = _token_regex(token_re)
    out, pos = [], 0
    for m in rx.finditer(text):
        if m.start() == m.end():
            continue
        out.extend(text[pos:m.start()])
        out.append(Token(m.group(0)))
        pos = m.end()
    out.extend(text[pos:])
    return out


# --------------------------------------------------------------------------------------------
# Shaping tables
# --------------------------------------------------------------------------------------------
# base -> (isolated, final, initial, medial); None = form does not exist in Unicode
FORMS = {
    0x0621: (0xFE80, None, None, None),
    0x0622: (0xFE81, 0xFE82, None, None),
    0x0623: (0xFE83, 0xFE84, None, None),
    0x0624: (0xFE85, 0xFE86, None, None),
    0x0625: (0xFE87, 0xFE88, None, None),
    0x0626: (0xFE89, 0xFE8A, 0xFE8B, 0xFE8C),
    0x0627: (0xFE8D, 0xFE8E, None, None),
    0x0628: (0xFE8F, 0xFE90, 0xFE91, 0xFE92),
    0x0629: (0xFE93, 0xFE94, None, None),
    0x062A: (0xFE95, 0xFE96, 0xFE97, 0xFE98),
    0x062B: (0xFE99, 0xFE9A, 0xFE9B, 0xFE9C),
    0x062C: (0xFE9D, 0xFE9E, 0xFE9F, 0xFEA0),
    0x062D: (0xFEA1, 0xFEA2, 0xFEA3, 0xFEA4),
    0x062E: (0xFEA5, 0xFEA6, 0xFEA7, 0xFEA8),
    0x062F: (0xFEA9, 0xFEAA, None, None),
    0x0630: (0xFEAB, 0xFEAC, None, None),
    0x0631: (0xFEAD, 0xFEAE, None, None),
    0x0632: (0xFEAF, 0xFEB0, None, None),
    0x0633: (0xFEB1, 0xFEB2, 0xFEB3, 0xFEB4),
    0x0634: (0xFEB5, 0xFEB6, 0xFEB7, 0xFEB8),
    0x0635: (0xFEB9, 0xFEBA, 0xFEBB, 0xFEBC),
    0x0636: (0xFEBD, 0xFEBE, 0xFEBF, 0xFEC0),
    0x0637: (0xFEC1, 0xFEC2, 0xFEC3, 0xFEC4),
    0x0638: (0xFEC5, 0xFEC6, 0xFEC7, 0xFEC8),
    0x0639: (0xFEC9, 0xFECA, 0xFECB, 0xFECC),
    0x063A: (0xFECD, 0xFECE, 0xFECF, 0xFED0),
    0x0641: (0xFED1, 0xFED2, 0xFED3, 0xFED4),
    0x0642: (0xFED5, 0xFED6, 0xFED7, 0xFED8),
    0x0643: (0xFED9, 0xFEDA, 0xFEDB, 0xFEDC),
    0x0644: (0xFEDD, 0xFEDE, 0xFEDF, 0xFEE0),
    0x0645: (0xFEE1, 0xFEE2, 0xFEE3, 0xFEE4),
    0x0646: (0xFEE5, 0xFEE6, 0xFEE7, 0xFEE8),
    0x0647: (0xFEE9, 0xFEEA, 0xFEEB, 0xFEEC),
    0x0648: (0xFEED, 0xFEEE, None, None),
    # Alef maksura is dual-joining in Unicode; its initial/medial forms live in the FB block
    # (named "UIGHUR KAZAKH KIRGHIZ ALEF MAKSURA ..."). Word-internal use is rare in Arabic.
    0x0649: (0xFEEF, 0xFEF0, 0xFBE8, 0xFBE9),
    0x064A: (0xFEF1, 0xFEF2, 0xFEF3, 0xFEF4),
    # common extras (Persian/Urdu/Maghrebi letters, alef wasla)
    0x0671: (0xFB50, 0xFB51, None, None),
    0x067E: (0xFB56, 0xFB57, 0xFB58, 0xFB59),
    0x0686: (0xFB7A, 0xFB7B, 0xFB7C, 0xFB7D),
    0x06A4: (0xFB6A, 0xFB6B, 0xFB6C, 0xFB6D),
    0x06AF: (0xFB92, 0xFB93, 0xFB94, 0xFB95),
    0x06CC: (0xFBFC, 0xFBFD, 0xFBFE, 0xFBFF),
}
ISO, FIN, INI, MED = range(4)

# alef variant -> (isolated ligature, final ligature) for LAM + alef
LAM = 0x0644
LAM_ALEF = {
    0x0622: (0xFEF5, 0xFEF6),
    0x0623: (0xFEF7, 0xFEF8),
    0x0625: (0xFEF9, 0xFEFA),
    0x0627: (0xFEFB, 0xFEFC),
}

RIGHT_JOINING = {0x0622, 0x0623, 0x0624, 0x0625, 0x0627, 0x0629, 0x062F, 0x0630, 0x0631, 0x0632,
                 0x0648, 0x0671}
NON_JOINING = {0x0621, 0x200C}          # hamza, ZWNJ
JOIN_CAUSING = {0x0640, 0x200D}         # tatweel, ZWJ
# Dual joining letters without presentation forms (rare; passed through unshaped)
EXTRA_DUAL = set(range(0x063B, 0x0640))
HARAKAT = set(range(0x064B, 0x0653))    # fathatan .. sukun (the set the option is about)


def _is_transparent(cp):
    return (cp in HARAKAT or 0x0610 <= cp <= 0x061A or 0x0653 <= cp <= 0x065F or cp == 0x0670
            or 0x06D6 <= cp <= 0x06DC or 0x06DF <= cp <= 0x06E4 or cp in (0x06E7, 0x06E8)
            or 0x06EA <= cp <= 0x06ED)


def joining_type(unit):
    """'D' dual, 'R' right, 'C' join causing, 'T' transparent, 'U' non joining."""
    if isinstance(unit, Token) or len(unit) != 1:
        return 'U'
    cp = ord(unit)
    if _is_transparent(cp):
        return 'T'
    if cp in RIGHT_JOINING:
        return 'R'
    if cp in JOIN_CAUSING:
        return 'C'
    if cp in NON_JOINING:
        return 'U'
    if cp in FORMS or cp in EXTRA_DUAL:
        return 'D'
    return 'U'


def shape(text, keep_harakat=False, token_re=None):
    """Logical Arabic -> logical presentation forms. Tokens and non-Arabic text pass through.

    keep_harakat=False drops all transparent marks (U+064B-U+0652 and other Arabic combining marks),
    which is what a spacing-only bitmap font renderer needs. keep_harakat=True keeps them, in logical
    order right after their base (after the ligature for lam+mark+alef).
    """
    # NFC first: decomposed input (alef + combining madda / hamza) would otherwise lose the mark when
    # harakat are dropped. Tokens are ASCII, so normalising the whole string leaves them intact.
    text = unicodedata.normalize('NFC', text)
    units = tokenize(text, token_re)
    n = len(units)
    jt = [joining_type(u) for u in units]

    # neighbour indices, skipping transparent marks
    prev_nt = [None] * n
    last = None
    for i in range(n):
        prev_nt[i] = last
        if jt[i] != 'T':
            last = i
    next_nt = [None] * n
    last = None
    for i in range(n - 1, -1, -1):
        next_nt[i] = last
        if jt[i] != 'T':
            last = i

    form = [None] * n
    for i in range(n):
        if jt[i] in ('T', 'U'):
            continue
        p, q = prev_nt[i], next_nt[i]
        jp = p is not None and jt[p] in ('D', 'C') and jt[i] in ('D', 'R', 'C')
        jn = q is not None and jt[i] in ('D', 'C') and jt[q] in ('D', 'R', 'C')
        form[i] = MED if (jp and jn) else FIN if jp else INI if jn else ISO

    out = []
    i = 0
    while i < n:
        u = units[i]
        t = jt[i]
        if t == 'T':
            if keep_harakat:
                out.append(u)
            i += 1
            continue
        if isinstance(u, Token) or t == 'U' and ord(u) not in FORMS:
            out.append(u)
            i += 1
            continue
        cp = ord(u)
        q = next_nt[i]
        if cp == LAM and q is not None and not isinstance(units[q], Token) and ord(units[q]) in LAM_ALEF and form[i] in (INI, MED):
            iso_l, fin_l = LAM_ALEF[ord(units[q])]
            out.append(chr(fin_l if form[i] == MED else iso_l))
            if keep_harakat:
                out.extend(units[k] for k in range(i + 1, q))   # marks on the lam
            i = q + 1                                          # marks on the alef follow naturally
            continue
        forms = FORMS.get(cp)
        if forms is None:
            out.append(u)
        else:
            f = form[i] if form[i] is not None else ISO
            g = forms[f]
            if g is None:  # cannot happen with the tables above, kept as a safety net
                g = forms[FIN] if f == MED and forms[FIN] else forms[ISO]
            out.append(chr(g))
        i += 1
    return ''.join(out)


# --------------------------------------------------------------------------------------------
# Simplified bidi (UAX #9 subset: P2/P3, W1-W7, N1-N2, I1-I2, L1, L2, L4)
# --------------------------------------------------------------------------------------------
MIRROR = {'(': ')', ')': '(', '[': ']', ']': '[', '{': '}', '}': '{', '<': '>', '>': '<',
          '«': '»', '»': '«', '‹': '›', '›': '‹'}


def _bidi_class(unit):
    if isinstance(unit, Token):
        # a tag is an icon or a run-time insert: a neutral object, so in an Arabic line two tags in a row
        # come out in right-to-left order like the words around them, not in English order
        return 'ON'
    c = unicodedata.bidirectional(unit)
    if not c:
        return 'L'
    if c in ('LRE', 'RLE', 'LRO', 'RLO', 'PDF', 'LRI', 'RLI', 'FSI', 'PDI', 'BN'):
        return 'BN'     # explicit embeddings/isolates are not supported: ignored
    return c


def _visual_units(units, base):
    """Reorder one line (no newlines) of units. Returns the reordered unit list."""
    n = len(units)
    if n == 0:
        return []
    types = [_bidi_class(u) for u in units]
    # remove BN (and do not display them)
    keep = [i for i in range(n) if types[i] != 'BN']
    units = [units[i] for i in keep]
    types = [types[i] for i in keep]
    orig = list(types)
    n = len(units)
    if n == 0:
        return []

    if base == 'auto':
        para = 1
        for t in types:
            if t == 'L':
                para = 0
                break
            if t in ('R', 'AL'):
                para = 1
                break
    else:
        para = 1 if base == 'rtl' else 0
    e = 'R' if para else 'L'
    sos = eos = e

    # W1 NSM -> previous type
    prev = sos
    for i in range(n):
        if types[i] == 'NSM':
            types[i] = prev
        prev = types[i]
    # W2 EN after AL -> AN
    last_strong = sos
    for i in range(n):
        t = types[i]
        if t in ('L', 'R', 'AL'):
            last_strong = t
        elif t == 'EN' and last_strong == 'AL':
            types[i] = 'AN'
    # W3 AL -> R
    types = ['R' if t == 'AL' else t for t in types]
    # W4 single separators between numbers
    for i in range(1, n - 1):
        a, t, b = types[i - 1], types[i], types[i + 1]
        if t == 'ES' and a == 'EN' and b == 'EN':
            types[i] = 'EN'
        elif t == 'CS' and a == b and a in ('EN', 'AN'):
            types[i] = a
    # W5 ET sequences adjacent to EN -> EN
    i = 0
    while i < n:
        if types[i] == 'ET':
            j = i
            while j < n and types[j] == 'ET':
                j += 1
            if (i > 0 and types[i - 1] == 'EN') or (j < n and types[j] == 'EN'):
                for k in range(i, j):
                    types[k] = 'EN'
            i = j
        else:
            i += 1
    # W6 other separators/terminators -> ON
    types = ['ON' if t in ('ES', 'ET', 'CS') else t for t in types]
    # W7 EN after L (or L sos) -> L
    last_strong = sos
    for i in range(n):
        t = types[i]
        if t in ('L', 'R'):
            last_strong = t
        elif t == 'EN' and last_strong == 'L':
            types[i] = 'L'
    # N1/N2 neutrals
    neutral = ('B', 'S', 'WS', 'ON')

    def strong_dir(t):
        return 'L' if t == 'L' else 'R' if t in ('R', 'EN', 'AN') else None

    i = 0
    while i < n:
        if types[i] in neutral:
            j = i
            while j < n and types[j] in neutral:
                j += 1
            left = strong_dir(types[i - 1]) if i > 0 else sos
            right = strong_dir(types[j]) if j < n else eos
            d = left if left == right else e
            for k in range(i, j):
                types[k] = d
            i = j
        else:
            i += 1
    # I1/I2 levels
    levels = []
    for t in types:
        if para == 0:
            levels.append(1 if t == 'R' else 2 if t in ('AN', 'EN') else 0)
        else:
            levels.append(2 if t in ('L', 'EN', 'AN') else 1)
    # L1 trailing whitespace (and segment separators) -> paragraph level
    for i in range(n - 1, -1, -1):
        if orig[i] in ('WS', 'S', 'B'):
            levels[i] = para
        else:
            break
    for i in range(n):
        if orig[i] in ('S', 'B'):
            levels[i] = para
            k = i - 1
            while k >= 0 and orig[k] == 'WS':
                levels[k] = para
                k -= 1
    # L2 reverse
    order = list(range(n))
    hi = max(levels)
    lo_odd = min(l for l in levels if l % 2) if any(l % 2 for l in levels) else hi + 1
    for lev in range(hi, lo_odd - 1, -1):
        i = 0
        while i < n:
            if levels[order[i]] >= lev:
                j = i
                while j < n and levels[order[j]] >= lev:
                    j += 1
                order[i:j] = order[i:j][::-1]
                i = j
            else:
                i += 1
    out = []
    for idx in order:
        u = units[idx]
        if levels[idx] % 2 and not isinstance(u, Token) and u in MIRROR:
            u = MIRROR[u]
        out.append(u)
    return out


def visual(line, base='rtl', token_re=None):
    """Logical (already shaped) text -> visual left-to-right order. Each '\\n' line separately.

    base: 'rtl' (default, game text in an Arabic build), 'ltr', or 'auto' (first strong char,
    falling back to RTL).
    """
    return '\n'.join(''.join(_visual_units(tokenize(l, token_re), base)) for l in line.split('\n'))


# --------------------------------------------------------------------------------------------
# Wrapping
# --------------------------------------------------------------------------------------------
def _split_words(text, token_re):
    """Split at ASCII spaces outside tokens. Returns list of words (strings)."""
    words, cur = [], []
    for u in tokenize(text, token_re):
        if not isinstance(u, Token) and u == ' ':
            words.append(''.join(cur))
            cur = []
        else:
            cur.append(u)
    words.append(''.join(cur))
    return words


def wrap(text, width_fn, max_width, keep_harakat=False, base='rtl', token_re=None, shaped=False):
    """Shape, break in LOGICAL order at spaces, then reorder each line to visual order.

    width_fn(visual_string) -> width in the same unit as max_width. Existing '\\n' are kept as hard
    breaks. A single word wider than max_width gets a line of its own (never split). Returns a list
    of visual lines; lines[0] holds the start of the text (to be drawn at the top).
    """
    s = text if shaped else shape(text, keep_harakat, token_re)
    lines = []
    for para in s.split('\n'):
        words = _split_words(para, token_re)
        cur = None
        for w in words:
            cand = w if cur is None else cur + ' ' + w
            if cur is not None and width_fn(visual(cand, base, token_re)) > max_width:
                lines.append(cur)
                cur = w
            else:
                cur = cand
        lines.append(cur if cur is not None else '')
    return [visual(l, base, token_re) for l in lines]


def layout(text, width_fn=None, max_width=None, keep_harakat=False, base='rtl', token_re=None):
    """Full pipeline for one game string: shape, optional wrap, visual order, '\\n' joined."""
    if width_fn is None or max_width is None:
        return visual(shape(text, keep_harakat, token_re), base, token_re)
    return '\n'.join(wrap(text, width_fn, max_width, keep_harakat, base, token_re))


def ccm_width_fn(ccm_path, missing_advance=None, token_width=0):
    """Width function from a CCM font (French PoC ccm.Ccm: glyph[6] = advance).

    Tokens count as token_width (icons are drawn by the game; measure them separately).
    """
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ccm import Ccm
    c = Ccm(open(ccm_path, 'rb').read())

    def fn(s):
        w = 0
        for u in tokenize(s):
            if isinstance(u, Token):
                w += token_width
                continue
            gi = c.index(ord(u))
            if gi is None or gi >= len(c.glyphs):
                w += missing_advance if missing_advance is not None else 0
            else:
                w += c.glyphs[gi][6]
        return w
    return fn


# --------------------------------------------------------------------------------------------
# Corpus glyph needs
# --------------------------------------------------------------------------------------------
def is_presentation_form(cp):
    return 0xFB50 <= cp <= 0xFDFF or 0xFE70 <= cp <= 0xFEFF


def required_codepoints(texts, keep_harakat=False, token_re=None):
    """Shape every string and collect the code points that must exist in the font.

    Returns {'presentation': sorted list, 'other': sorted list (non-presentation, excluding
    tokens and control chars), 'unshaped_arabic': Arabic-block code points that survived shaping
    (letters without presentation forms, Arabic digits/punctuation, kept harakat)}.
    """
    pres, other = set(), set()
    for t in texts:
        for u in tokenize(shape(t, keep_harakat, token_re), token_re):
            if isinstance(u, Token):
                continue
            cp = ord(u)
            if cp < 0x20:
                continue
            (pres if is_presentation_form(cp) else other).add(cp)
    return {'presentation': sorted(pres), 'other': sorted(other),
            'unshaped_arabic': sorted(c for c in other if 0x0600 <= c <= 0x06FF)}


# --------------------------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------------------------
def _cps(s):
    return ' '.join('%04X' % ord(c) for c in s)


def _selftest():
    fails = []
    total = [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            fails.append(name)
            print('FAIL %s\n  got  %s\n  want %s' % (name, _cps(got) if isinstance(got, str) else got,
                                                    _cps(want) if isinstance(want, str) else want))
        else:
            print('ok   %s' % name)

    # 1. every table entry checked against the Unicode database (decomposition + name)
    tag = ('<isolated>', '<final>', '<initial>', '<medial>')
    bad = []
    for base, forms in FORMS.items():
        for k, g in enumerate(forms):
            if g is None:
                continue
            dec = unicodedata.decomposition(chr(g))
            if dec != '%s %04X' % (tag[k], base):
                bad.append((hex(base), k, hex(g), dec))
    # forms that exist in Unicode but are missing from the table
    for cp in list(range(0xFB50, 0xFE00)) + list(range(0xFE70, 0xFF00)):
        try:
            dec = unicodedata.decomposition(chr(cp)).split()
        except ValueError:
            continue
        if len(dec) == 2 and dec[0] in tag and int(dec[1], 16) in FORMS:
            if FORMS[int(dec[1], 16)][tag.index(dec[0])] != cp:
                bad.append(('missing', hex(cp), dec))
    for alef, (iso, fin) in LAM_ALEF.items():
        if unicodedata.decomposition(chr(iso)) != '<isolated> 0644 %04X' % alef:
            bad.append(('lamalef iso', hex(iso)))
        if unicodedata.decomposition(chr(fin)) != '<final> 0644 %04X' % alef:
            bad.append(('lamalef fin', hex(fin)))
    # joining types: right-joining letters must have no initial/medial form, dual must have all
    for base, forms in FORMS.items():
        t = joining_type(chr(base))
        if t == 'R' and (forms[INI] or forms[MED]):
            bad.append(('R with ini/med', hex(base)))
        if t == 'D' and not all(forms):
            bad.append(('D missing forms', hex(base)))
    check('tables match unicodedata (%d letters, 4 lam-alef)' % len(FORMS), bad, [])

    # 2. shaping, expected code points written out by hand (logical order)
    U = lambda *c: ''.join(chr(x) for x in c)
    # السلام: alef iso, lam ini, seen med, lam+alef final ligature, meem iso
    check('shape السلام', shape('السلام'), U(0xFE8D, 0xFEDF, 0xFEB4, 0xFEFC, 0xFEE1))
    check('shape لا', shape('لا'), U(0xFEFB))
    check('shape لأ', shape('لأ'), U(0xFEF7))
    check('shape الآن', shape('الآن'), U(0xFE8D, 0xFEF5, 0xFEE5))
    check('shape علا (final lam-alef)', shape('علا'), U(0xFECB, 0xFEFC))
    check('shape لإ', shape('بلإ'), U(0xFE91, 0xFEFA))
    # مرحبا بك: meem ini, reh fin, hah ini, beh med, alef fin, space, beh ini, kaf fin
    check('shape مرحبا بك', shape('مرحبا بك'),
          U(0xFEE3, 0xFEAE, 0xFEA3, 0xFE92, 0xFE8E, 0x20, 0xFE91, 0xFEDA))
    # اضغط: alef iso, dad ini, ghain med, tah fin ; للبدء: lam ini, lam med, beh med, dal fin, hamza iso
    check('shape اضغط START للبدء', shape('اضغط START للبدء'),
          U(0xFE8D, 0xFEBF, 0xFED0, 0xFEC2) + ' START ' + U(0xFEDF, 0xFEE0, 0xFE92, 0xFEAA, 0xFE80))
    # المستوى 12: alef iso, lam ini, meem med, seen med, teh med, waw fin, alef maksura iso
    check('shape المستوى 12', shape('المستوى 12'),
          U(0xFE8D, 0xFEDF, 0xFEE4, 0xFEB4, 0xFE98, 0xFEEE, 0xFEEF) + ' 12')
    check('shape single letters', shape('ب ه ي'), U(0xFE8F, 0x20, 0xFEE9, 0x20, 0xFEF1))
    check('shape ة after joiner', shape('مدينة'), U(0xFEE3, 0xFEAA, 0xFEF3, 0xFEE8, 0xFE94))
    check('shape harakat dropped', shape('بِسْمِ'), U(0xFE91, 0xFEB4, 0xFEE2))
    check('shape harakat kept', shape('بِسْمِ', keep_harakat=True),
          U(0xFE91, 0x0650, 0xFEB4, 0x0652, 0xFEE2, 0x0650))
    check('shape lam-fatha-alef ligature', shape('لَا'), U(0xFEFB))
    check('shape lam-fatha-alef kept', shape('لَا', keep_harakat=True), U(0xFEFB, 0x064E))
    check('shape tatweel', shape('ـبـ'), U(0x0640, 0xFE92, 0x0640))
    check('shape hamza breaks join', shape('بءب'), U(0xFE8F, 0xFE80, 0xFE8F))
    check('shape extras پچڤگی', shape('پچڤگی'), U(0xFB58, 0xFB7D, 0xFB6D, 0xFB95, 0xFBFD))
    check('shape alef wasla', shape('بٱ'), U(0xFE91, 0xFB51))
    check('shape ZWNJ', shape('ب‌ب'), U(0xFE8F, 0x200C, 0xFE8F))
    check('shape token breaks join', shape('ب<?select?>ب'), U(0xFE8F) + '<?select?>' + U(0xFE8F))
    check('shape latin untouched', shape('Hello, world 42%'), 'Hello, world 42%')

    # 3. visual order (left to right as drawn)
    vis = lambda s, **k: visual(shape(s), **k)
    check('visual السلام', vis('السلام'), U(0xFEE1, 0xFEFC, 0xFEB4, 0xFEDF, 0xFE8D))
    check('visual مرحبا بك', vis('مرحبا بك'),
          U(0xFEDA, 0xFE91, 0x20, 0xFE8E, 0xFE92, 0xFEA3, 0xFEAE, 0xFEE3))
    check('visual اضغط START للبدء', vis('اضغط START للبدء'),
          U(0xFE80, 0xFEAA, 0xFE92, 0xFEE0, 0xFEDF) + ' START ' + U(0xFEC2, 0xFED0, 0xFEBF, 0xFE8D))
    check('visual المستوى 12', vis('المستوى 12'),
          '12 ' + U(0xFEEF, 0xFEEE, 0xFE98, 0xFEB4, 0xFEE4, 0xFEDF, 0xFE8D))
    check('visual number first', vis('12 بك'), U(0xFEDA, 0xFE91) + ' 12')
    check('visual two latin words', vis('اضغط Select Button الآن'),
          U(0xFEE5, 0xFEF5, 0xFE8D) + ' Select Button ' + U(0xFEC2, 0xFED0, 0xFEBF, 0xFE8D))
    check('visual brackets mirrored', vis('بك (اضغط)'),
          '(' + U(0xFEC2, 0xFED0, 0xFEBF, 0xFE8D) + ') ' + U(0xFEDA, 0xFE91))
    check('visual brackets round latin', vis('اضغط (START) الآن'),
          U(0xFEE5, 0xFEF5, 0xFE8D) + ' (START) ' + U(0xFEC2, 0xFED0, 0xFEBF, 0xFE8D))
    check('visual guillemets', vis('«بك»'), '«' + U(0xFEDA, 0xFE91) + '»')
    # UAX#9: digits after Arabic become AN (W2), so ET '%' does not attach (W5) and lands left of the
    # number, as browsers render it: visual '%12.5'
    check('visual percent + decimal', vis('بك 12.5%'), '%12.5 ' + U(0xFEDA, 0xFE91))
    check('visual range 1/2', vis('بك 1/2'), '1/2 ' + U(0xFEDA, 0xFE91))
    check('visual arabic-indic digits', vis('بك ١٢٣'), '١٢٣ ' + U(0xFEDA, 0xFE91))
    check('visual final punctuation', vis('مرحبا!'), '!' + U(0xFE8E, 0xFE92, 0xFEA3, 0xFEAE, 0xFEE3))
    check('visual question mark', vis('بك؟'), '؟' + U(0xFEDA, 0xFE91))
    check('visual token kept', vis('اضغط <?select?> للبدء'),
          U(0xFE80, 0xFEAA, 0xFE92, 0xFEE0, 0xFEDF) + ' <?select?> ' + U(0xFEC2, 0xFED0, 0xFEBF, 0xFE8D))
    check('visual token with @N', vis('بك <?blockName@12?>'), '<?blockName@12?> ' + U(0xFEDA, 0xFE91))
    check('visual token not mirrored', vis('<?cancel?>بك'), U(0xFEDA, 0xFE91) + '<?cancel?>')
    check('visual percent placeholder', vis('بك %d'), '%d ' + U(0xFEDA, 0xFE91))
    check('visual custom regex', visual(shape('بك {NAME} بك', token_re=r'\{[A-Z]+\}'),
                                        token_re=r'\{[A-Z]+\}'),
          U(0xFEDA, 0xFE91) + ' {NAME} ' + U(0xFEDA, 0xFE91))
    check('visual custom regex off -> braces mirrored', vis('بك {} بك'),
          U(0xFEDA, 0xFE91) + ' {} ' + U(0xFEDA, 0xFE91))
    check('visual latin + digits run', vis('بك Level 12 بك'),
          U(0xFEDA, 0xFE91) + ' Level 12 ' + U(0xFEDA, 0xFE91))
    check('visual multi-line', vis('بك\nمرحبا'),
          U(0xFEDA, 0xFE91) + '\n' + U(0xFE8E, 0xFE92, 0xFEA3, 0xFEAE, 0xFEE3))
    check('visual pure latin rtl para', visual('Hello world'), 'Hello world')
    check('visual ltr para', visual('Press ' + shape('بك') + ' now', base='ltr'),
          'Press ' + U(0xFEDA, 0xFE91) + ' now')
    check('visual auto ltr', visual('Hi ' + shape('بك'), base='auto'), 'Hi ' + U(0xFEDA, 0xFE91))
    check('visual trailing space at visual left (end of RTL line)', visual(shape('بك ')),
          ' ' + U(0xFEDA, 0xFE91))

    # 4. wrapping: width = number of code points
    w = wrap('واحد اثنان ثلاثة اربعة', len, 10)
    want = [visual(shape('واحد اثنان')), visual(shape('ثلاثة اربعة'))]
    check('wrap 2 lines, first = start of text', w, want)
    check('wrap first line starts logically', w[0][-1], chr(0xFEED))   # waw iso drawn rightmost
    w = wrap('كلمة', len, 1)
    check('wrap long word alone', w, [visual(shape('كلمة'))])
    w = wrap('اضغط <?select?> للتأكيد الآن', len, 18)
    check('wrap keeps token atomic', w,
          [visual(shape('اضغط <?select?>')), visual(shape('للتأكيد الآن'))])
    check('wrap hard newline', wrap('بك\nبك بك', len, 100),
          [visual(shape('بك')), visual(shape('بك بك'))])
    check('layout joins lines', layout('واحد اثنان ثلاثة', len, 10),
          visual(shape('واحد اثنان')) + '\n' + visual(shape('ثلاثة')))
    check('shape للتأكيد', shape('للتأكيد'),
          U(0xFEDF, 0xFEE0, 0xFE98, 0xFE84, 0xFEDB, 0xFEF4, 0xFEAA))

    # 5. corpus glyph needs
    r = required_codepoints(['السلام', 'لا <?cancel?> 12'])
    check('required presentation forms', r['presentation'],
          sorted({0xFE8D, 0xFEDF, 0xFEB4, 0xFEFC, 0xFEE1, 0xFEFB}))
    check('required other', r['other'], sorted({0x20, ord('1'), ord('2')}))

    # 6. round trip property: visual(visual) == original for single-direction Arabic line
    s = shape('مرحبا بك في العالم')
    check('double reversal of pure Arabic', visual(visual(s)), s)

    print('\n%d/%d passed' % (total[0] - len(fails), total[0]))
    return not fails


def _main(argv):
    if '--test' in argv:
        return 0 if _selftest() else 1
    if '--corpus' in argv:
        path = argv[argv.index('--corpus') + 1]
        lines = open(path, encoding='utf-8').read().split('\n')
        r = required_codepoints(lines, keep_harakat='--keep-harakat' in argv)
        print('presentation forms needed: %d' % len(r['presentation']))
        print(' '.join('%04X' % c for c in r['presentation']))
        print('other code points: %d' % len(r['other']))
        print(' '.join('%04X' % c for c in r['other']))
        if r['unshaped_arabic']:
            print('Arabic-block code points left unshaped:', ' '.join('%04X' % c for c in r['unshaped_arabic']))
        return 0
    if '--show' in argv:
        t = argv[argv.index('--show') + 1]
        s = shape(t)
        print('logical :', _cps(t))
        print('shaped  :', _cps(s))
        print('visual  :', _cps(visual(s)))
        print(visual(s))
        return 0
    print(__doc__)
    return 0


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))

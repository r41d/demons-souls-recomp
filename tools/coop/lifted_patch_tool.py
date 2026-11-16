"""Lifted-code.patch section tool. usage (repo root):
  lp.py pristine F...   write <S>/pristine/F = port/recompiled/F (LF) with its patch section reversed
  lp.py regen F...      replace F's sections with diff(pristine, port/recompiled/F as LF); verify; write the patch
The port files are CRLF on this PC (git apply with autocrlf); the patch is LF."""
import os, subprocess, sys, shutil
S = os.path.dirname(os.path.abspath(__file__)); ROOT = os.getcwd()   # working files go next to this file (git-ignored)
PATCH = os.path.join(ROOT, 'patches', 'lifted-code.patch'); PORT = os.path.join(ROOT, 'port')
CR = bytes([13]); NL = bytes([10])


def LF(b):
    return b.replace(CR + NL, NL)


def split(b):
    lines = b.split(NL); out = []; head = []; cur = None; key = None; i = 0
    while i < len(lines):
        l = lines[i]
        if l.startswith(b'diff -ruN ') and i + 1 < len(lines) and lines[i + 1].startswith(b'--- a/'):
            if cur is not None: out.append((key, cur))
            key = lines[i + 1][6:].split(b'\t')[0].decode(); cur = [l, lines[i + 1]]; i += 2; continue
        if l.startswith(b'--- a/') and i + 1 < len(lines) and lines[i + 1].startswith(b'+++ b/'):
            if cur is not None: out.append((key, cur))
            key = l[6:].split(b'\t')[0].decode(); cur = [l]; i += 1; continue
        (cur if cur is not None else head).append(l); i += 1
    if cur is not None: out.append((key, cur))
    return head, out


def join(head, secs):
    return NL.join(head + [l for _, c in secs for l in c])


def run(*a):
    return subprocess.run(list(a), capture_output=True)


raw = open(PATCH, 'rb').read(); head, secs = split(raw)
assert join(head, secs) == raw, 'split/join not lossless'
cmd, files = sys.argv[1], sys.argv[2:]
for f in files:
    key = 'recompiled/' + f
    mine = [c for k, c in secs if k == key]
    assert len(mine) == 1, (f, len(mine))
    sec = NL.join(mine[0]) + NL
    if cmd == 'pristine':
        os.makedirs(S + '/pristine', exist_ok=True)
        p = S + '/pristine/' + f
        open(p, 'wb').write(LF(open(os.path.join(PORT, key), 'rb').read()))
        open(S + '/sec.patch', 'wb').write(sec)
        r = run('patch', '-R', '-s', '--binary', '--no-backup-if-mismatch', p, S + '/sec.patch')
        assert r.returncode == 0, r.stdout + r.stderr
        print('pristine', f, os.path.getsize(p))
    elif cmd == 'regen':
        old = mine[0]; d = old[0].startswith(b'diff')
        a_lab = old[1 if d else 0].decode()[4:]; b_lab = old[2 if d else 1].decode()[4:]
        e = S + '/edited_lf_' + f
        open(e, 'wb').write(LF(open(os.path.join(PORT, key), 'rb').read()))
        r = run('diff', '-u', '--label', a_lab, '--label', b_lab, S + '/pristine/' + f, e)
        assert r.returncode == 1, r.stderr
        new = r.stdout.rstrip(NL).split(NL)
        if d: new = [old[0]] + new
        secs = [(k, new if k == key else c) for k, c in secs]
        print('regen', f, len(sec), '->', len(NL.join(new)))
if cmd == 'regen':
    out = join(head, secs)
    if not out.endswith(NL): out += NL
    for f in files:
        key = 'recompiled/' + f; c = [c for k, c in secs if k == key][0]
        open(S + '/sec.patch', 'wb').write(NL.join(c) + NL)
        t = S + '/verify_' + f; shutil.copy(S + '/pristine/' + f, t)
        r = run('patch', '-s', '--binary', '--no-backup-if-mismatch', t, S + '/sec.patch')
        assert r.returncode == 0, r.stdout + r.stderr
        assert open(t, 'rb').read() == LF(open(os.path.join(PORT, key), 'rb').read()), 'MISMATCH ' + f
        os.remove(t)
    shutil.copy(PATCH, S + '/lifted-code.patch.bak'); open(PATCH, 'wb').write(out); print('patch written + verified')

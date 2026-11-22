"""Install the Arabic translation (USA disc, BLUS-30443) into this folder's game, built from YOUR game files.

  Windows:        .venv\\Scripts\\python translations\\arabic\\install_arabic.py
  Linux / macOS:  .venv/bin/python translations/arabic/install_arabic.py
  --uninstall     put the original English files back
  --game PATH     another game folder (the one with PS3_GAME, or its PS3_GAME/USRDIR)

What it does: reads the game's own English font, text, menu layout, menu textures and title movie, and writes
Arabic versions of them (apply_ar.py): menu and item text (ar_text.json, our own translation), the fonts with the
Arabic letter forms added (Noto Sans Arabic, SIL OFL), right-to-left menu layout, the title logo and the title movie
(the movie needs ffmpeg: one on PATH, or else the imageio-ffmpeg package, which this fetches into this Python
like numpy; without either the movie keeps the English logo). The originals are copied to
game/arabic_backup first, so --uninstall can put them back. Nothing from the game is in this folder. Don't run it
while the game is running. Run it again after an update to rebuild.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def usrdir_of(game):
    for p in (os.path.join(game, 'PS3_GAME', 'USRDIR'), game):
        if os.path.isdir(os.path.join(p, 'MSG')) and os.path.isdir(os.path.join(p, 'FONT')):
            return p
    sys.exit(f'no game files found in {game} (expected PS3_GAME/USRDIR with MSG and FONT)')


def backup_dir(usrdir):
    return os.path.join(os.path.dirname(os.path.dirname(usrdir)), 'arabic_backup')


def replace(src, dst):
    """Copy src over dst without writing through dst (a hard link to the disc copy stays untouched)."""
    if os.path.exists(dst):
        os.remove(dst)
    shutil.copyfile(src, dst)


def uninstall(usrdir, quiet=False):
    bk = backup_dir(usrdir)
    if not os.path.isdir(bk):
        if not quiet:
            print('The Arabic translation is not installed (no arabic_backup folder).')
        return
    n = 0
    for d, _, fs in os.walk(bk):
        for f in fs:
            if f == 'INSTALLED.txt':
                continue
            rel = os.path.relpath(os.path.join(d, f), bk)
            replace(os.path.join(d, f), os.path.join(usrdir, rel))
            n += 1
    shutil.rmtree(bk)
    print(f'Original English files restored ({n} files).')


def need_numpy():
    try:
        import numpy  # noqa: F401
    except ImportError:
        print('Installing numpy into this Python (needed to draw the fonts and the logo)...')
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '-q', 'numpy>=1.24'],
                       check=True)


def need_ffmpeg():
    """An ffmpeg for the title movie: the one on PATH, or else the imageio-ffmpeg package's (a pip wheel that carries
    an ffmpeg build), fetched into this Python like numpy and linked as <venv>/ffmpeg/ffmpeg[.exe], whose folder is
    put first on PATH for apply_ar.py. False when there is neither (the movie then keeps the English logo)."""
    if shutil.which('ffmpeg'):
        return True
    try:
        import imageio_ffmpeg  # noqa: F401
    except ImportError:
        print('Fetching ffmpeg into this Python (imageio-ffmpeg, for the title movie)...', flush=True)
        r = subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '-q',
                            'imageio-ffmpeg>=0.5'])
        if r.returncode:
            return False
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:   # no build for this system
        print('imageio-ffmpeg has no ffmpeg here:', e)
        return False
    d = os.path.join(sys.prefix, 'ffmpeg')
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, 'ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
    if not (os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(exe)):
        if os.path.exists(dst):
            os.remove(dst)
        try:
            os.link(exe, dst)
        except OSError:
            shutil.copy2(exe, dst)
    os.environ['PATH'] = d + os.pathsep + os.environ.get('PATH', '')
    return shutil.which('ffmpeg') is not None


def install(usrdir):
    if not (os.path.exists(os.path.join(usrdir, 'MSG', 'NA_ENGLISH', 'menu.msgbnd')) or
            os.path.exists(os.path.join(usrdir, 'MSG', 'NA_ENGLISH', 'menu.msgbnd.dcx'))):
        sys.exit('This is not the USA disc (no MSG/NA_ENGLISH). The Arabic translation is made for BLUS-30443.')
    uninstall(usrdir, quiet=True)         # always build from the original English files
    need_numpy()
    if not need_ffmpeg():
        print('Note: no ffmpeg (none on PATH and imageio-ffmpeg could not be fetched), so the title movie keeps the '
              'English logo. Install ffmpeg (Windows: winget install Gyan.FFmpeg) and run this again.')
    out = tempfile.mkdtemp(prefix='des_arabic_')
    try:
        subprocess.run([sys.executable, os.path.join(HERE, 'apply_ar.py'), usrdir, out], check=True)
        bk = backup_dir(usrdir)
        files = []
        for d, _, fs in os.walk(out):
            for f in fs:
                rel = os.path.relpath(os.path.join(d, f), out)
                dst = os.path.join(usrdir, rel)
                if not os.path.exists(dst):
                    continue          # only replace files the game has
                os.makedirs(os.path.dirname(os.path.join(bk, rel)), exist_ok=True)
                shutil.copyfile(dst, os.path.join(bk, rel))
                files.append(rel)
        for rel in files:
            replace(os.path.join(out, rel), os.path.join(usrdir, rel))
        with open(os.path.join(bk, 'INSTALLED.txt'), 'w', encoding='utf-8') as f:
            f.write('Arabic translation installed over these files (originals in this folder):\n')
            f.write(''.join(r + '\n' for r in sorted(files)))
        print(f'Arabic translation installed ({len(files)} files; originals in {bk}).')
    finally:
        shutil.rmtree(out, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description='Install the Arabic translation of Demon\'s Souls (USA disc).')
    ap.add_argument('--uninstall', action='store_true', help='put the original English files back')
    ap.add_argument('--game', default=os.path.join(ROOT, 'game'), help='the game folder (default: game/)')
    a = ap.parse_args()
    usrdir = usrdir_of(a.game)
    if a.uninstall:
        uninstall(usrdir)
    else:
        install(usrdir)


if __name__ == '__main__':
    main()

"""The game's language, for the launchers' "Language" setting.

  python translations/translations.py list  [--game DIR]          the choices for this disc, as JSON
  python translations/translations.py apply <id> [--game DIR]     make the game folder match a choice

Three kinds of choice:
  - "original": the disc's own language (English on the USA disc; on the European disc, English).
  - "official": the European disc's other languages (French, German, Spanish, Italian). They are on the disc already:
    the launcher only sets the environment the entry gives (PS3_LANG), nothing is installed.
  - "translation": a custom translation in translations/<folder>/ (its translation.json says which disc it is for and
    which script installs it). Installing builds its files from the player's own game files and keeps the originals;
    removing puts them back. Only one translation is installed at a time.

`apply` prints one line per step and ends with "OK <id>" (exit 0), or prints the reason on stderr (exit 1).
`list` prints {"disc": "US" | "EU" | null, "installed": <id> | null, "choices": [...]}, each choice
{"id", "label", "kind", "env": {...}, "available": bool, "note": str}. The launcher shows the labels, keeps the id,
sets "env" when it starts the game, and calls `apply <id>` before starting when the id is a translation that is not
installed yet, or when a translation is installed and the choice is now something else.

New translation: a folder translations/<name>/ with translation.json:
  {"id": "ar", "name": "Arabic", "native": "العربية", "disc": "US", "installer": "install_arabic.py",
   "backup": "arabic_backup", "needs": "ffmpeg for the title movie (optional)"}
The installer takes --game <folder> and --uninstall, and keeps the originals in <folder>/<backup>.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'setup'))

OFFICIAL_EU = [('fr', 'Français (French)'), ('de', 'Deutsch (German)'), ('es', 'Español (Spanish)'),
               ('it', 'Italiano (Italian)')]


def disc_of(game):
    try:
        import region
        return region.detect(game)
    except Exception:
        return None


def translations():
    out = []
    for name in sorted(os.listdir(HERE)):
        p = os.path.join(HERE, name, 'translation.json')
        if os.path.isfile(p):
            with open(p, encoding='utf-8') as f:
                t = json.load(f)
            t['folder'] = os.path.join(HERE, name)
            out.append(t)
    return out


def installed(game):
    for t in translations():
        if os.path.isdir(os.path.join(game, t.get('backup', t['id'] + '_backup'))):
            return t
    return None


def choices(game):
    disc = disc_of(game)
    ch = [{'id': 'en', 'label': 'English', 'kind': 'original', 'env': {}, 'available': True, 'note': ''}]
    if disc == 'EU':
        for code, label in OFFICIAL_EU:
            ch.append({'id': code, 'label': label, 'kind': 'official', 'env': {'PS3_LANG': code}, 'available': True,
                       'note': 'On the European disc'})
    for t in translations():
        ok = disc is not None and t.get('disc') == disc
        label = f"{t['native']} ({t['name']})" if t.get('native') else t['name']
        note = t.get('needs', '')
        if not ok:
            note = f"Needs the {'USA' if t.get('disc') == 'US' else 'European'} disc"
        ch.append({'id': t['id'], 'label': label, 'kind': 'translation', 'env': {}, 'available': ok, 'note': note})
    return disc, ch


def run_installer(t, game, uninstall):
    cmd = [sys.executable, os.path.join(t['folder'], t['installer']), '--game', game] + (['--uninstall'] if uninstall else [])
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                         errors='replace', bufsize=1)
    for line in p.stdout:
        print(line.rstrip(), flush=True)
    return p.wait()


def apply(choice, game):
    disc, ch = choices(game)
    c = next((x for x in ch if x['id'] == choice), None)
    if c is None:
        sys.exit(f'No language "{choice}" for this game folder.')
    if not c['available']:
        sys.exit(f"{c['label']}: {c['note']}.")
    cur = installed(game)
    if cur and cur['id'] == choice:
        print(f"{c['label']} is already installed.", flush=True)
        print('OK ' + choice, flush=True)
        return
    if cur:
        print(f"Removing {cur['name']} (the original files go back)...", flush=True)
        if run_installer(cur, game, True):
            sys.exit(f"Could not remove {cur['name']}.")
    if c['kind'] == 'translation':
        t = next(x for x in translations() if x['id'] == choice)
        print(f"Installing {t['name']}: building it from your game files (about two minutes)...", flush=True)
        if run_installer(t, game, False):
            sys.exit(f"Could not install {t['name']} (the messages above say why).")
    print('OK ' + choice, flush=True)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')   # labels are not ASCII; launchers read UTF-8
    ap = argparse.ArgumentParser(description='The game language for the launchers.')
    ap.add_argument('action', choices=('list', 'apply'))
    ap.add_argument('choice', nargs='?')
    ap.add_argument('--game', default=os.path.join(ROOT, 'game'))
    a = ap.parse_args()
    game = os.path.abspath(a.game)
    if a.action == 'list':
        disc, ch = choices(game)
        cur = installed(game)
        print(json.dumps({'disc': disc, 'installed': cur['id'] if cur else None, 'choices': ch}, ensure_ascii=False))
    else:
        if not a.choice:
            sys.exit('apply needs a language id (see list).')
        apply(a.choice, game)


if __name__ == '__main__':
    main()

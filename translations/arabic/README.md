# Arabic translation (USA disc, BLUS-30443)

Install / uninstall: see the main README ("Arabic translation"). This folder holds only our own work: the Arabic
text, the tools, two OFL fonts and the logo picture. Everything the game needs is built from the player's own game
files when `install_arabic.py` runs. **Never commit game files or the game's English text here.**

| File | What it does |
| --- | --- |
| `install_arabic.py` | Builds the Arabic files from the game in `game/`, backs up the originals to `game/arabic_backup`, and installs. `--uninstall` puts the originals back. |
| `apply_ar.py` | Builds every Arabic file into an output folder (fonts, text, menu layout, title logo, title movie). |
| `ar_text.json` | The translation: `{menu|item: {FMG id: {string id: Arabic}}}`, logical order. Reviewed by native speakers. Items left out stay English. |
| `arshape.py` | Arabic shaping into presentation forms (U+FE70-FEFF), lam-alef, bidi to visual order, and line wrapping. The game draws code points left to right with no shaping, so text is stored shaped and in visual order. |
| `make_ar_font.py` | Draws the Arabic letter forms (Noto Sans Arabic) into dsfont24 / talkfont24 and points 0-9 at Arabic-Indic digits. Only changed BC3 blocks are re-encoded. |
| `drb_rtl.py`, `drbparse.py` | Right-aligns the menu text boxes in `MENU/menu.drb`, and points the options "HUD" label at a new Arabic string. The file never grows: a larger `menu.drb` made the game stall at boot. |
| `make_ar_title.py`, `logo_art.py`, `title_logo_ar.png` | The title logo: the picture (`logo_art.py` draws it from Noto Naskh Arabic), split into the two sprites of the `Title` texture in `MENU/menu.tpf` (BC1). |
| `make_ar_title_movie.py` | `MOVIE/ds_title.pam`, the logo forming out of smoke. The original's smoke is the English logo's own shape, so the effect is redrawn from the Arabic logo on the original's timeline, then encoded with ffmpeg (MPEG-2, same settings) and written into the original file's video packets. The header and the audio stay byte for byte. |
| `fmg.py`, `ccm.py`, `make_font.py` | FMG text, CCM font and BC3 helpers. |
| `fonts/` | Noto Sans Arabic and Noto Naskh Arabic, SIL Open Font License 1.1 (`fonts/OFL.txt`). |

What stays English:
- pictures with text (NOW LOADING, YOU DIED, area titles);
- the L / R hand labels, which game code draws;
- the alignment of the button help line, which game code places.

See docs/NOTES.md fix 160.

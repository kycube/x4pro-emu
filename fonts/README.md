# `fonts/` — typeface candidates, local only

This directory is **gitignored except for this file**. It holds font binaries used to build system
and reading fonts for the device (`tools/xtfont.py`, `tools/fontcompare.py`), and it deliberately
never reaches the public repository, for two reasons:

1. **Licensing.** Some of these are the owner's own copies of fonts that may not be redistributed —
   `bookerly/` (Amazon), `georgia/` (Microsoft), `misans/` (Xiaomi's licence forbids redistribution
   *and* adaptation, so an `.xtf` built from it may not be shipped either). A package built from
   them is for the owner's own device; anyone else supplies their own copy of the font.
2. **Size.** 19 MB of binaries that the repository does not need: everything open-licensed here is
   one command away from its canonical source.

## What is here

| directory | licence | redistributable? |
|---|---|---|
| `bookerly/` | Amazon, proprietary | **no** — the owner's own copy |
| `georgia/` | Microsoft, proprietary | **no** — the owner's own copy |
| `misans/` | Xiaomi IP licence — no redistribution, no adaptation | **no**, and no derived `.xtf` either |
| `atkinson/` `charis-sil/` `gentium/` `ibm-plex-sans/` `ibm-plex-serif/` `inter/` `libre-baskerville/` `literata/` `noto-serif/` `pt-sans/` `pt-serif/` `source-serif/` | SIL Open Font Licence 1.1 (`OFL.txt` in each) | yes, with the licence alongside |

Which of these is worth using, and why, is `docs/fonts.md` — including the measured finding that
several famous names (Literata, Noto, Source Serif, Inter, Merriweather) ship from Google Fonts with
**no hinting program**, which matters a great deal at the 20 px and 24 px cells the UI draws in.

## Refetching the open ones

All of them come from `github.com/google/fonts`; each family's path is in `docs/fonts.md`. For example:

```bash
curl -sfL https://raw.githubusercontent.com/google/fonts/main/ofl/charissil/CharisSIL-Regular.ttf -o fonts/charis-sil/CharisSIL-Regular.ttf
curl -sfL https://raw.githubusercontent.com/google/fonts/main/ofl/charissil/OFL.txt -o fonts/charis-sil/OFL.txt
```

Several are **variable** fonts (`*-variable.ttf`): rendering one gives its default instance, usually
Regular. A specific weight needs a static instance first (`fonttools varLib.instancer`), which
matters here because e-ink at 20 px generally wants more weight than Regular.

## Looking at them the way the device would

```bash
.venv/bin/python tools/fontcompare.py /tmp/sheet.png --current \
  "Charis SIL Bold=fonts/charis-sil/CharisSIL-Bold.ttf:15,20" \
  "PT Sans Bold=fonts/pt-sans/PTSans-Bold.ttf:16,22"
```

`--current` puts the device's own font on top as the baseline. Judge from that sheet, never from a
specimen rendered on the Mac: the UI is 1 bpp with no anti-aliasing, and it is unkind.

# Gamebooks on the stock firmware

A choose-your-own-adventure book for the reader, as a Lua app on the stock `xteink_app` 7.6.8: pages
of text in the system font, 1-bit pixel-art plates, choices you tap, the side buttons turning pages,
five endings with a tally. **It needs no firmware change**: 7.6.8 shows "Mini Apps" in the main menu
by itself once `/XTApps` on the card holds an app (`docs/stock-7.6.8.md`), so putting the folder on
the card is the whole install.

The first book is **The Keeper of Gull Rock** (`stories/gull-rock/`): a relief keeper rowed out to a
lighthouse whose light has been dark for three nights. Thirteen passages, eight plates, five endings,
one flag (reading the keeper's log is what makes the best ending reachable). Written and drawn for
this project in October 2026; the plates are code, not generated images.

| piece | where |
|---|---|
| the story | `stories/gull-rock/story.txt` |
| the plates and the page ornament | `stories/gull-rock/scenes.py` (on `tools/pixart.py`) |
| the build tool | `tools/gamebook.py` (`check`, `build`, `plates`) |
| the engine | `tools/gamebook_engine.lua`, appended into each app's `index.lua` |
| the card-ready app | `apps/gull-rock/` (`index.lua`, `manifest.json`, `app.xtapp`) |
| tests | `tests/test_gamebook.py` (fast: parser, wrapping, art round trip, the app is up to date; emulator: plays the log branch to "Two Keepers" on 7.6.8 against `tests/golden/gamebook-*.png`) |

## Putting it on the reader

Over the reader's own USB mode (menu → USB): the card mounts on the Mac; copy the folder so it lands
at `XTApps/gull-rock/` on the card, eject, leave USB mode, then menu → **Mini Apps** → the book.

```bash
cp -R apps/gull-rock "/Volumes/<the card>/XTApps/"
```

For a card image (the emulator): `tools/xtapp.py install CARD.img apps/gull-rock`.

## Writing a story

```
title: The Keeper of Gull Rock
app_id: gull-rock              the card directory; apps/<app_id>/ is the build output
start: landing

== landing                     a passage
@image storm                   its plate: a name in scenes.py's SCENES
@set read_log                  a flag set on entering
Text. A blank line separates paragraphs.
@end The Light Holds           this passage is an ending, with that title...
The ship was saved.            ...and the paragraph after @end is its epigraph
* Climb the tower -> stair     a choice
* Follow the song -> hymn ? read_log      shown only with the flag (`? !read_log`: without)
```

`tools/gamebook.py check stories/<name>` prints the passage graph and refuses what would break on the
device: a missing target, a passage without choices, a flag tested but never set, an `@image` that is
not in `SCENES`, and **any character outside printable ASCII** — the font widths the wrapping uses
were measured for those 95 only (`docs/lua-apps.md` §4b), so curly quotes and dashes are refused
rather than guessed. It also warns about unreachable passages and widows (a short last word alone on
its line), which is what to reword before a build. Going back to `start` clears the flags.

`tools/gamebook.py build stories/<name> apps/<app_id>` writes the app; `plates` writes every plate
and the lettering as PNGs to look at.

## Drawing the plates

A plate is 160 × 100 logical pixels shown at 3× (480 × 300), drawn in Python with `tools/pixart.py`:
paint tones (0–255) with gradients, polygons and pools of light, quantise through a 4 × 4 Bayer
matrix (so every tone becomes one of the classic patterns — checker, dots, stripes), then ink crisp
black and white detail on top (`s.black(...)`, `s.white(...)`, `s.pattern(shape, tone)` after the
quantise). What works on this panel, learned the hard way on the first drafts:

- **Big solid masses, patterns only where light falls off.** A scene patterned everywhere reads as
  grey noise at reading distance (the first storm, cottage and stair were all redrawn for this).
- **Silhouette against light.** A black figure on a black or dotted ground disappears; put the light
  *behind* it (the ship sits in the beam's solid core, Elias against a wall lit from below).
- **Keep detail chunky.** One-pixel lines are three screen pixels wide; anything finer than two
  logical pixels vanishes into the patterns.

The cover title and each ending's title are Libre Baskerville (OFL, `fonts/libre-baskerville/`)
rasterised to 1 bit at build time — the host has one text size, so display type is art too.

## How the engine works (and why)

Everything here follows from the 7.6.8 probe (`apps/probe-io/`, `docs/lua-apps.md` §4c):

- **One file.** A folder app cannot read its own files or `require` modules, so the story (pre-wrapped
  lines) and the art (run strings) are string literals in `index.lua`: Gull Rock is 55 KB; the host
  loaded 200 KB and failed at 700 KB, and the tool refuses past 200 KB.
- **Plates go through layers.** A frame keeps only its first 1024 drawing commands and drops the rest
  silently; a plate is 3,500–6,400 line segments. Each plate is painted once into
  `ctx.layers:create(480, 300)` (no cap there) and blitted with `g:layer` — one command. Text drawn
  into a layer does not appear, so text goes on the frame; a page is about 40 commands.
- **The art encoding** is per row of (gap, run) pairs in a printable alphabet (`tools/gamebook.py`
  docstring); the engine draws each logical run as three screen lines.
- **Pages are laid out on the device** once per passage, because the visible choices depend on the
  flags: text in 30 px lines, a plate page's text from y 320, a page without a plate under the
  ornament. Choices follow the text; when they would sit alone on a page, or when the last paragraph
  would split across the break, that whole paragraph moves to the next page with them. A passage
  that fits on one page has no footer, so its choices may use that room. Endings get a page of their
  own, centred: ornament, title, epigraph, "Ending n of 5 – k found so far", Begin again.
- **Input:** tap a choice box; tap the right two thirds (or swipe left, or the lower side button) for
  the next page, the left third (or swipe right, or the upper button) for the previous one.
- **Refresh:** a page turn is `ctx.invalidate()` (a fast refresh); a new passage also calls
  `ctx.display:full_refresh()`, and the two coalesce into one full refresh, so a plate never ghosts
  into the next scene (seen in the emulator: one `full` per passage change).
- **Progress** lives in `ctx.state` (passage, page, flags, endings found; flat strings and numbers
  only). `ctx.save()` is a stub in 7.6.8; the host saves `ctx.state` itself when the app is left
  (encrypted, into `state.xtsv`). **Confirmed on the reader (2026-10-12):** after a few
  pages, Home pad → Exit, and back in, the cover offers **Continue**; the app exits cleanly. The
  emulator does the same since qemu-patches 0019/0020 (the AES model was there; the GDMA could not
  reach PSRAM; `tests/test_lua_state.py`); the gamebook's own emulator test never leaves the app.

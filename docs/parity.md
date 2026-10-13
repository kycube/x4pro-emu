# Device parity: the same screens on the emulator and on the reader, pixel for pixel

`tools/parity.py` (squad S2-device, NEXT_PHASE §9 D4). One step script is walked twice: by
`x4emu` on the emulator, and by the owner on the reader, following a printed runbook. On both sides
the stock firmware itself takes the picture (its **Screen Capture**, a `.xic` on the card), so the
comparison is between two files the same firmware wrote. The tool pairs them, diffs them with
per-screen masks, and explains every differing pixel or fails.

## What it proves, and what it does not

- **Proved per screen, when the report passes:** the stock's own compositor drew the same 480×800
  frame on the emulator as on the device, except in named regions (the clock, the battery, the date)
  and in regions whose inputs were shown to differ (the bookshelf, the gamebook's saved game). That
  covers layout, fonts, icons, the menu scrim, the reader's pagination of the parity book and the Lua
  app's drawing.
- **Proved on the emulator side only:** each emulator `.xic` equals the emulator's panel screenshot of
  the same screen (0 px outside the masks), so the panel model shows what the firmware composed.
  Together: device frame = emulator frame = emulator panel.
- **Not proved:** the device's *panel*. A capture is the firmware's frame buffer, not the glass; the
  waveform, ghosting and grey levels are outside it (docs/grayscale.md). Input timing is not proved
  either: the owner's finger and x4emu's taps only have to reach the same screens.
- **The light panel has no device capture.** Tapping Screen Capture closes the panel first and the
  capture holds the screen below it (as docs/xic.md found on 7.2.4; the same on 7.6.8). The script
  keeps the panel as a screen without a capture: the report shows the emulator's picture of it for
  the owner's eye.

## The pieces

| Command | What it does | Touches |
|---|---|---|
| `parity.py emu SCRIPT [--session DIR] [--name N]` | builds a redacted, developer-patched scratch copy of the dump and the card, boots it, walks the script, captures every screen, checks each `.xic` against its panel shot and each screen against its reference in `parity/<name>/` | QEMU instance N (default `parity`); the scratch copy is deleted afterwards |
| `parity.py runbook SCRIPT [--out F.md]` | the owner's numbered checklist, in on-device words | nothing |
| `parity.py prepare-card SCRIPT MOUNT [--yes]` | on the card mounted in USB mode: checks the preconditions, lists the captures already there, and with `--yes` writes the parity book to `/Parity/Parity Book.epub` (read back, no `._` file) | the reader's card, only with `--yes`, one new folder |
| `parity.py collect SCRIPT MOUNT` | copies the session's new `screenshot_*.xic` off the card | reads the card |
| `parity.py compare SCRIPT DEVICE_DIR EMU_DIR` | pairs, diffs, writes `report.{html,md,json}` with device / emulator / difference pictures | nothing |
| `parity.py book OUT.epub` | the parity book alone (byte-stable, SHA-256 printed) | nothing |

A session lives in `--session DIR`, default `images/parity/<script name>/` (gitignored), with
`emu/`, `device/` and `report/` below it. `tests/test_parity.py` covers the compare logic on synthetic
captures and walks the emulator side end to end.

## The step script (`parity/stock-7.6.8.json`)

Everything is in the **portrait frame** the stock draws and captures in: 480 wide, 800 tall, origin
top left. x4emu's landscape panel pixel for a portrait point (px, py) is (py, 479 − px); the
hamburger at portrait (441, 88) is the (88, 38) the other stock tests tap. The module docstring of
`tools/parity.py` is the schema. In short:

- `capture` — how a Screen Capture is taken: the pull from (239, 5) to (239, 300) over 600 ms, the
  "Screen Ca" button at (280, 733), the toast's lifetime (`toast_ms` 5000) and box, and the runbook
  wording.
- `masks` — regions that always differ: `status-bar` (rows 0..59: clock, battery, the WiFi glyph the
  device shows and the redacted copy does not), `menu-date`, `reader-clock`, `reader-battery`.
- `preconditions` — what must hold for a state region to compare; checked on the mounted card by
  `prepare-card` (`absent`, `same-dirs`, `same-files`, `same-as-mirror`) or asked of the owner
  (`owner`). `scope: "all"` (the system font, the UI language) makes an unmet one *inconclusive*.
- `screens` — in walking order, each with its steps (tap / swipe / home / press / note, each with
  its runbook sentence), its masks and its state regions (`depends` names a precondition).

The eight screens of 7.6.8, in order: **home** (Bookshelf), **light** (no capture), **menu**,
**settings**, **all-files** (inside `/Parity`), **reader** (the parity book, first page),
**mini-apps** (Extensions), **gull-rock** (the cover). The walk takes about two and a half minutes on
the emulator and perhaps ten on the device.

### Why the script looks the way it does (the card as it really is)

The emulator's card is `images/device/sd-files`, the mirror of the reader's card from 2026-09-06, plus
`apps/gull-rock` as the owner installed it on 2026-10-12, plus the parity book. The real card has
moved on since September, so every screen was chosen or bounded so that it does not depend on that:

| On the device card today, possibly | Would change | So the script |
|---|---|---|
| books the owner added since September, more files in `/screenshots` | the All Files root listing (every root folder and book, "N files" under screenshots) | does not capture the root: it opens `/Parity`, a folder `prepare-card` creates holding only the parity book; the owner taps it by name, wherever it sorts |
| a book opened at some point (`/XTData/reading/recent.xtbr` exists) | Home's bookshelf, the dimmed Home behind the menu | captures Home and the menu before the parity book is opened; precondition `bookshelf`, state regions on both |
| reading positions | the reader page | reads a book neither side has opened before (new file, first page, no page turn) |
| a saved gamebook game (`/XTApps/gull-rock/state.xtsv`: the owner played and left with Exit on 2026-10-12, so the cover offers **Continue / Begin again** and may count endings) | the cover's choices | precondition `gamebook-state`, state region y 515..799; the plate and the title above it stay strict (checked in the emulator: after Begin and Exit the cover differs in 2,253 px, all at y 543..633; `parity/stock-7.6.8/variants/gull-rock-saved-game.png`, pinned by a fast test) |
| other apps in `/XTApps` | the Mini Apps list | precondition `apps` (exactly `gull-rock`) |
| a different system font | every line of text | precondition `system-font` (`selection.config` equal to the mirror's), scope all |
| NVS changed since the 2026-10-12 dump: language, reading defaults, a bound cloud account, brightness | the whole UI / the reader page / the Settings account row / the light panel's sliders | owner questions `language` (scope all), `reading-settings`, `cloud-account`; the light panel is not captured |
| WiFi on (the redacted copy has `net_en` 0) | the status bar glyph | inside the `status-bar` mask |

Things found while building it (7.6.8, emulator):

- **The toast.** "Screenshot saved" stays alive about 3.5 s of guest time after the capture and is
  composited onto any screen drawn meanwhile (a menu opened 3.4 s after a capture carried it, 4.4 s
  after did not); it does not disappear by itself on an idle panel. The walk waits 5 s of guest time
  after each capture, and the runbook tells the owner to count to five. `compare` names a difference
  that sits inside the toast's box as a leftover toast.
- The capture is the screen *as redrawn after the panel closes*: on the menu, the status-bar clock is
  not redrawn over the scrim, so the menu's capture has no clock while the panel screenshot taken
  before the pull does (inside the mask).
- Every capture is a mono `.xic` of 48,024 bytes (version 1, 1 level, 1 plane); the Gull Rock cover's
  dithered plate is plain 1 bpp too.
- The light panel also opens over the reader and over a Lua app, so Screen Capture works on every
  screen the script visits.

## Running the emulator side

```
.venv/bin/python tools/parity.py emu parity/stock-7.6.8.json            # -> images/parity/stock-7.6.8/emu
.venv/bin/python -m pytest tests/test_parity.py                          # fast tests + the walk
```

It needs `images/device/flash-2026-10-12-a.bin`, `images/device/sd-files`, mtools and the built QEMU.
A walk ends with one line per capture (`.xic vs panel 0 px outside the masks`) and `OK`; a step that
leads elsewhere stops the walk with the screen it reached (the references in `parity/stock-7.6.8/`).
After a deliberate change of the script or the firmware, `emu --update-references` rewrites them; look
at every one before committing.

## A device session

The coordinator runs it with the owner present; the owner performs every step on the reader. Device
rules (CLAUDE.md) apply throughout: nothing is written to the flash without a verified double
backup, nothing below 0x10000, otadata untouched, and nobody touches the reader during a transfer.

**1. Is the device what the tools expect?** Read-only:

```
tools/device.py slots          # live: app0 boots xteink_app 7.6.8, otadata entry 0 seq 3 VALID
```

If an OTA has landed since 2026-10-12 (another version, or app1 booting), stop: the script and the
patch sites are for 7.6.8 in app0.

**2. A fresh backup, tagged.** The 2026-10-12 pair is verified and covers app0 byte for byte, and the
writer re-checks that (it compares the slot's first 64 KB with the backup before writing). A new one
is still worth the four minutes: NVS has changed since 10:54 that day (the owner played the gamebook;
whatever 7.6.8 keeps in NVS for its apps, brightness, settings), and a disaster restore from the older
pair would roll that back. It also makes the newest verified pair exactly the state before the patch,
which is what `restore-stock` restores. Use a tag: `backup` names files by today's date, and on
2026-10-12 an untagged run would overwrite the existing pair in place.

```
tools/device.py backup --tag pre-parity          # two 16 MB reads, SHA-256 equal; appends docs/device/flash-backup-sha256.txt
tools/device.py slots --from-dump images/device/flash-<date>-pre-parity-a.bin
```

After that read the device resets; if it then deep-sleeps (the stock's boot-preflight wants the power
button held), the owner presses power.

**3. The patched app.** One byte, then the checksum and the appended SHA-256 (34 bytes in all, all
past the first 64 KB):

```
.venv/bin/python tools/stockpatch.py apply images/device/stock-app0-7.6.8.bin developer-menu -o SCRATCH/app0-7.6.8-dev.bin
.venv/bin/python -m esptool --chip esp32s3 image-info SCRATCH/app0-7.6.8-dev.bin   # checksum 0x1c valid, hash valid
# sha256 a185f5276e65e519bf0ae9f0b46075a2a8246ee4e7a03f3013ce1f85acda37cc (pristine f68533a8...)
```

**4. Writing app0.** `flash-crosspoint` is the writer for this: despite the name it takes any ESP app
image (`--firmware`: first byte 0xE9, fits the slot), refuses unless the target is the slot otadata
boots, checks that the partition table equals the backup's and that the slot's first 64 KB still
equal the backup's, writes at the slot offset only, reads back, confirms otadata unchanged and resets.
Do not pass `--preserve-stock` (app1 holds 7.5.4 and is not blank; it would abort). Plan first:

```
tools/device.py flash-crosspoint --firmware SCRATCH/app0-7.6.8-dev.bin --slot app0
```

Its plan, simulated offline from the 2026-10-12 dump (the real run reads the layout from the device,
which resets it into the ROM loader and back):

```
reading the partition table and otadata from /dev/cu.usbmodem… (which slot boots decides everything below)...
  the bootloader starts app0: otadata entry 0 has the highest valid sequence number (3, state VALID), and (3 - 1) % 2 = 0
verified backup: …/images/device/flash-2026-10-12-pre-parity-a.bin
plan: port /dev/cu.usbmodem…
  0. app0 is the slot the bootloader starts (otadata entry 0 has the highest valid sequence number (3, state VALID), and (3 - 1) % 2 = 0)
  2. write SCRATCH/app0-7.6.8-dev.bin (6250640 bytes) into app0 @0x10000
  otadata/bootloader/partition table untouched; the boot slot does not move
pass --yes to proceed
```

Then the same command with `--yes`. `--slot app1` refuses ("The bootloader starts app0, not app1").
After the reset the owner wakes the reader if needed and checks the light panel: three boxed buttons
at the bottom (Memory Developer, Screen Ca, BGIO Developer).

**5. The card (USB mode).** On the reader: ≡ → USB. On the Mac the card mounts as `/Volumes/NO NAME`.

```
tools/parity.py prepare-card parity/stock-7.6.8.json "/Volumes/NO NAME"          # check + plan
tools/parity.py prepare-card parity/stock-7.6.8.json "/Volumes/NO NAME" --yes    # writes /Parity/Parity Book.epub
diskutil eject "/Volumes/NO NAME"                                                # flush before leaving USB mode
```

It prints each precondition (expect `gamebook-state: NOT met`, the saved game) and writes
`images/parity/stock-7.6.8/device/precheck.json`, including the captures already on the card.

**6. The walk.** `tools/parity.py runbook parity/stock-7.6.8.json` is the owner's checklist: three
questions first (language, the Settings account row, the reading settings), then the eight screens,
seven captures. Note every "no".

**7. The captures off the card.** ≡ → USB again, then:

```
tools/parity.py collect parity/stock-7.6.8.json "/Volumes/NO NAME"     # read only
diskutil eject "/Volumes/NO NAME"
```

**8. Compare.** Run `emu` any time before (it needs no device):

```
tools/parity.py emu parity/stock-7.6.8.json
tools/parity.py compare parity/stock-7.6.8.json images/parity/stock-7.6.8/device images/parity/stock-7.6.8/emu \
    [--not-met reading-settings ...]       # one per owner answer "no"
open images/parity/stock-7.6.8/report/report.html
```

**9. Reverting, if the owner wants unpatched 7.6.8 back.** `restore-stock` writes app0 from the
newest verified backup, which after step 2 is the pre-patch state:

```
tools/device.py restore-stock --slot app0
# plan: write 6250640 bytes of xteink_app 7.6.8 from …/flash-2026-10-12-pre-parity-a.bin into app0 @0x10000 on /dev/cu.usbmodem…
# pass --yes to proceed
tools/device.py restore-stock --slot app0 --yes
```

One caution: **never take a backup while the patched app is on the device** unless it is moved out of
`images/device/` at once: it would become the newest verified pair, and `restore-stock` would then
restore the patched app (its plan says so: it names the image "pristine xteink_app 7.6.8" or "NOT a
pristine known stock build"). `restore-stock` stages the bytes it writes in a temporary file and never
rewrites the reference copy `images/device/stock-app0-7.6.8.bin` (fixed 2026-10-13, commit 1f8ccc5). The equivalent explicit path is
`tools/device.py flash-crosspoint --firmware images/device/stock-app0-7.6.8.bin --slot app0` (its
first 64 KB equal the backup's, so the sanity check passes). The card keeps `/Parity` and the
captures until the owner deletes them; the bookshelf then still lists the parity book (its reading
state is in `/XTData/reading/`), so a second session would find `bookshelf` not met.

## Reading the report

`report.html` (and `report.md`, `report.json`) starts with the status — **PASS** (every captured
screen identical or explained, none missing), **FAIL**, or **INCONCLUSIVE** (a scope-all
precondition is unmet) — the pairing (by order when the counts agree, by content otherwise, with
notes on skipped or suspicious captures) and the preconditions with their evidence. Then one section
per screen: the verdict (`identical`, `explained`, `DIFFERS`, `missing`, `not captured`), the pixel
counts per mask and per state region, the bounding box of the unexplained pixels, a hint when they
sit where the toast is drawn, and three pictures: the device capture, the emulator capture, and the
difference (the device capture lightened; blue = masked, amber = explained by an unmet precondition,
red = unexplained; grey outlines are masks, amber outlines state regions). The light panel shows the
emulator's picture alone. `compare` exits 0 / 1 / 2 for pass / fail / inconclusive or bad input.

A red pixel is a finding: either an emulator gap (a font metric, a layout rule, a missing glyph), a
precondition the script does not know yet (then add one), or a step that reached a different
screen on the device (then the runbook wording needs fixing).

## Adding a screen, or the next firmware

Add the screen to the script with its steps in portrait pixels and their runbook sentences, its
masks and state regions; run `emu --update-references`, look at the new reference and the walk's
`.xic` check, then `runbook` to read the owner's wording. For a new stock version, copy the script,
change `firmware` (the dump, `stock_version`; the patch sites come from `tools/stockver.d/`), and
re-find the coordinates the way this one was made: boot it, screenshot each screen
(`x4emu screenshot`), and read the rows off the upright picture.

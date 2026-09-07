# The stock `.xic` screen-capture format (Settings → Developer → Screen Capture)

Session 5 (2026-09-06), squad S2. How the stock `xteink_app` 7.2.4 (`images/device/stock-app0-7.2.4.bin`)
writes a screen capture, how to reach it in `x4emu`, and the `.xic` layout, decoded and verified
against a panel screenshot (0 pixels differ). The decoder, the developer-mode patcher and the
committed capture/panel pair are listed at the end.

## How developer mode is enabled: it is a compile-time stub in 7.2.4

The pull-down light panel ("BrightnessLayer") gains an extra row of buttons — **Memory**,
**Developer**, **Screen Capture** (and a "Dim light" toggle) — only when the app's developer-mode
predicate returns non-zero. In firmware 7.2.4 that predicate is a **stub that unconditionally
returns 0**:

```
app VA 0x4233abe0:  entry a1,32
        0x4233abe4:  movi.n a2, 0      ; <- return value, always 0
        0x4233abe6:  retw.n
```

This is the function whose result the developer log prints as `developer_mode=%u`. It is reached
through the literal pool near `brightness_dev_button` (app VA 0x42106964 holds the pointer
0x4233abe0; called from the panel-builder at 0x4213c6b5, its byte result gates the dev-button block
at 0x4213c730 `l8ui a8,a2,32 / beqz`). Evidence it is the gate, and that nothing in 7.2.4 flips it:

- **No NVS key.** The dump's `user_config` namespace has no developer/dev-mode key (`nvsedit list`:
  pwrTimingVer, cfg_init, activate, language, wallpMode, enDispMode, timeZone, lightBri, lightCT,
  fbLangDone, net_en, cloud_bind_st, otaPromptDay — that is all). The full key-name table in the app
  (near 0x11b59) lists every `user_config`/reader/BLE key; there is no developer key.
- **No card file.** The getter's body reads neither NVS nor a file; it just returns 0. (The
  `/sdcard/devtools/screenshot_push_url.txt` string is only read *after* a capture, by
  `DeveloperToolsService::_pushScreenshotToDevServer`, to optionally POST the shot to a dev server.)
- **No gesture.** Tapping the **Firmware Version** row on About Device repeatedly *does* reveal four
  hidden rows (Device ID, SN Code, Build Time, and **Diagnostics Test** → a "Test Mode" page with
  Factory Full Test / Aging Test) — an Android-style reveal — but it does **not** turn on
  developer_mode: after that reveal the pull-down still shows no Memory/Developer/Screen Capture
  buttons. The two mechanisms are unrelated.

**To reach Screen Capture in the emulator**, patch that one byte so the predicate returns 1, then
re-checksum the ESP image (last byte of the last 16-byte block = XOR of all segment bytes with seed
0xEF; the appended 32-byte SHA-256 covers everything up to it) and flash it into the app slot:

```
# start from a scratch copy of the dump (holds the owner's WiFi creds — keep it in scratch only)
cp images/device/flash-2026-09-06-a.bin SCRATCH/flash.bin
tools/nvsedit.py SCRATCH/flash.bin set-u8 user_config net_en 0
# tools/stockdev.py does the whole patch: file offset 0x4eabe4 (0x10000 + 0x4eabe4 in a flash
# image) changed 0x02 -> 0x12 (movi.n a2,0 -> a2,1), then ESP checksum byte and SHA-256 recomputed
tools/stockdev.py SCRATCH/flash.bin                          # or --check to only report
x4emu --name xicN run --flash SCRATCH/flash.bin --sd SCRATCH/sd.img --boot-hold-power
```

The patched app boots to Home normally (boot-preflight `decision=0`, hash accepted). This is an
emulator-only convenience for producing an oracle; the byte offset is `0x4eabe4` in
`stock-app0-7.2.4.bin` (segment 0x42000020, so VA 0x4233abe4).

## Path from Home to a capture (`x4emu`, landscape panel coordinates)

Coordinates are landscape 800×480 panel pixels of the portrait UI; portrait (px, py) = landscape
(py, 479 − px). With the patched app running (`--boot-hold-power`, wait for `refresh_count >= 2`,
then `wait-quiet --seconds 2`):

```
x4emu --name xicN swipe 5 240 300 240 --ms 600      # pull the light panel down from the top edge
x4emu --name xicN wait-quiet --seconds 1.5 --timeout 60
x4emu --name xicN tap 735 199 --ms 120              # "Screen Capture" button (portrait ~ (280,735))
# a "Screenshot saved" toast appears (screenshot it: x4emu screenshot toast.png)
x4emu --name xicN wait-quiet --seconds 3 --timeout 60
x4emu --name xicN stop
mdir  -i SCRATCH/sd.img@@1048576 ::/screenshots
mcopy -i SCRATCH/sd.img@@1048576 -o "::/screenshots/screenshot_YYYYMMDD_HHMMSS.xic" SCRATCH/
```

Other dev buttons in that row: **Developer** (portrait ~ (347,735), landscape `tap 735 347`) writes
`/sdcard/logs/dev_%s.log` and shows a "Log saved to SD" toast; **Memory** dumps the heap to the
console.

Note: the capture is taken from the last **fully painted** frame, not the live compositor — a capture
made while the (translucent) light panel is open still records the page underneath, so pull the panel,
tap Screen Capture, and the `.xic` holds the base screen (Home, in the verified run).

## Where the file lands and how it is named

- Directory `/sdcard/screenshots/` (created if missing via the mkdir wrapper at app 0x420112d0;
  the emulator card already has it). Also `/sdcard/logs/` for the dev log.
- Name pattern `screenshot_%s.xic` where `%s` is a timestamp formatted `%04d%02d%02d_%02d%02d%02d`
  (local time), e.g. `screenshot_20260906_230648.xic`. The dev log is `dev_%s.log` with the same `%s`.
- One capture is 48,024 bytes for the 480×800 mono screen (24-byte header + 48,000-byte payload).

## Header layout (24 bytes, little-endian)

Byte offsets, with the field meaning established from the writer (app 0x42026138 packs the payload
and 0x422153ec / 0x42026347 fills the header) and confirmed against a capture:

| Offset | Size | Value seen | Meaning |
|---|---|---|---|
| 0 | 4  | `58 49 43 00` | magic `"XIC\0"` |
| 4 | u16 | 480 | width in pixels (the portrait UI frame) |
| 6 | u16 | 800 | height in pixels |
| 8 | u8  | 1 | format version (the reader at 0x422153ec accepts 1 or 2) |
| 9 | u8  | 1 | levels: **1** for a mono capture, **4** when the display reports grey |
| 10 | u8 | 1 | planes: **1** for mono, **2** for the 4-level grey case (one 1-bpp plane per bit) |
| 11 | u8 | 0 | 0 (unknown; always 0 here) |
| 12 | u32 | 0 | must be 0 for version 1 (reader rejects the file otherwise) |
| 16 | u32 | 48000 | payload length in bytes = `stride * height * planes`, `stride = (width+7) >> 3` |
| 20 | u32 | 0 | 0 (unknown; always 0 here) |

Bytes 11 and 20–23 could not be pinned to any behaviour (always zero in mono captures) — treat as
reserved/unknown. Bytes 9 and 10 are read straight from the writer's disassembly (`movi a9,1 … movi
a9,4` / `movi a8,1 … movi a8,2`, gated on a "display is grey" flag), so the 4-level path is inferred,
not observed — the emulator's captures are all mono (`levels=1, planes=1`).

The single sample previously on the card,
`images/device/sd-files/XTData/system_fonts/misans-demibold/preview.xic` (320×96, payload 3840 =
320·96/8), has the same 24-byte header with `01 01 01 00` at bytes 8–11, so `.xic` is a general
1-bpp (or 2-plane grey) image container, not screenshot-specific.

## Payload encoding (verified)

- **1 bit per pixel**, one plane for a mono screen (`levels=1, planes=1`). A grey screen would add a
  second plane of the same size directly after the first (`planes=2`); not seen in the emulator.
- **Row-major, top row first.** `stride = (width + 7) >> 3 = 60` bytes per row, 800 rows.
- **MSB first** within each byte (bit 7 = leftmost pixel).
- **Polarity: a set bit (1) is a black pixel** (the writer stores the inverted pixel value).
- **Orientation: the upright portrait UI, 480 wide × 800 tall.** The emulator's `x4emu screenshot`
  is the landscape 800×480 panel; the `.xic` image equals that panel **rotated 90° counter-clockwise**
  (Pillow `Image.ROTATE_90`), i.e. portrait (px, py) → landscape (py, 479 − px), the same convention
  as the rest of the UI.

Pillow one-liner: `Image.frombytes('1',(480,800),payload,'raw','1;I',60)` then `.transpose(ROTATE_90)`
to compare against a landscape panel PNG.

### Verification (diff against the panel)

`tools/xic2png.py CAPTURE.xic --diff PANEL.png` counts the pixels differing from an `x4emu
screenshot` taken at the same moment:

- **Run 2, capture of Home vs the panel PNG taken just before the capture: 0 pixels differ.**
- Run 1, capture of Home vs the panel: 69 pixels differ, all inside the portrait status-bar clock
  (landscape x 24..39, y 407..430) — the minute rolled over between the panel shot and the capture;
  nothing outside the clock differs. This is the expected clock/battery difference, not an encoding
  error.

## The developer log line (`dev_%s.log`), captured

Tapping **Developer** in the same panel wrote (`developer_mode=1` only because the predicate was
patched):

```
phase=brightness_dev_button
uptime_ms=82678
developer_mode=1
light_on=1
brightness_value=10       brightness_min=0   brightness_max=10
color_temp_value=8        color_temp_min=0   color_temp_max=8
wifi_enabled=0            wifi_connected=0   wifi_name=
bt_enabled=0              bt_connected=0     bt_device_count=0   bt_device_name=
note=full memory dump is emitted to stdout by Memory::dump
```

## What the emulator got right / open questions

- The capture path exercised the SD write path end to end (mkdir, create, write, rename) with no
  card-write failure; the toast said "Screenshot saved" and the file was well-formed both runs. No
  model gap surfaced — this is a clean SD write, so QEMU needed no change.
- The 4-level grey `.xic` (`levels=4, planes=2`) is inferred from the writer, not observed; a grey
  screen (anti-aliased text through a GC refresh) would produce one to confirm the second plane's
  order and the level mapping. Bytes 11 and 20–23 are unknown (always 0 here).
- Whether the *real* device (untouched here) enables developer mode by some out-of-band means (a
  factory tool, a USB command, an OTA build with the predicate compiled live) is unknown; in the
  7.2.4 image on the device the predicate is the same stub, so on-device Screen Capture is likewise
  unreachable by a normal user gesture in this firmware version.

## Tools and tests

Everything above is implemented and guarded in the tree; the proof-of-concept scripts are gone.

- **`tools/xic2png.py`** — the decoder/encoder. `IN.xic OUT.png`, `--info` (the header fields by the
  names of the table above, as JSON), `--diff PANEL.png` (differing pixels against an `x4emu
  screenshot`, portrait captures un-rotated, exit 1 when they differ), `--encode IN.png OUT.xic`
  (version 1 / levels 1 / planes 1). `decode_xic`, `encode_xic`, `compare` are importable; the module
  docstring is the manual. The grey path is the documented two-plane guess.
- **`tools/stockdev.py`** — enables developer mode in a stock 7.2.4 image (`IMAGE [--check]`, a bare
  app image or a 16 MB flash image with app0 at 0x10000): verifies the stub bytes, writes the one
  byte at `0x4eabe4`, recomputes the ESP checksum byte and the appended SHA-256 and re-parses the
  result. `--check` reports patched / unpatched / unknown (exit 0 / 1 / 2). It refuses anything that
  is not the 7.2.4 stub, and warns that an image made from the dump still carries the owner's
  credentials.
- **`tests/data/stock-home-capture.xic`** + **`tests/data/stock-home-panel.png`** — the verified
  pair: a Home capture the stock wrote in the emulator and the panel screenshot taken just before it,
  0 pixels apart. Both are emulator-made and safe to commit. (The second run's pair is not committed:
  same screen, only the clock differs.)
- **`tests/test_xic.py`** — the decoder against that pair and against
  `tests/data/preview.xic`: header fields, the exact-match diff, the round trip, every rejected
  header, and the grey guess on a synthetic file. Fast, no emulator.
- **`tests/test_stockdev.py`** — patches a scratch copy of `images/device/stock-app0-7.2.4.bin`:
  exactly the instruction byte, the checksum byte and the 32 hash bytes change, `esptool image-info`
  calls both valid, `--check` flips, and a foreign image is refused. Fast, no emulator.
- **`tests/test_stock_capture.py`** — the whole path in one test: patch a scratch copy of the dump,
  boot, screenshot Home, pull the panel, tap Screen Capture, `mcopy` the `.xic` off the card, decode
  it and require 0 differing pixels outside the status bar (the clock may have rolled over).

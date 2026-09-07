# Grayscale: the waveform-level grey model (UC8279 X4)

The panel core (`models/epd_core.c`) has two ways to render a refresh that runs an external LUT bank
(`PSR` REG=1 with 0x20..0x24 written since power-on, the core's `EPD_MODE_GRAY`):

- **approx** (default): a fixed table maps the two RAM planes to four levels (`state.gray_approx` true
  after such a refresh). It ignores the LUT bytes.
- **waveform** (`waveform_gray`, off until validated against the device): the core parses the five
  uploaded tables into per-transition phase sequences and moves each pixel's reflectance by the
  frames its (old, new) class is driven, with a saturating linear model. Pixels keep their
  reflectance across refreshes; an OTP refresh (no LUT, or REG=0) still drives every pixel fully to
  its new bit, exactly as before.

Switch it on:

```
x4emu run … -- -global driver=x4pro.epd,property=waveform-gray,value=on
                -global driver=x4pro.epd,property=gray-k,value=28        # optional: the tunable
```

`x4pro_epd_gray_model()` reports `"approx"` / `"waveform"` for the JSON state (`gray_model`; the
board's state printer in `hw/xtensa/xteink_x4pro.c` has to emit it — one line,
`c->waveform_gray ? "waveform" : "approx"`). After a waveform refresh `state.gray_approx` is false.

## 1. LUT layout of the UC8279 X4 (0x20..0x24, 49 bytes)

Every table CrossPoint uploads (`Uc8279X4Driver.cpp`: `kXtfAa02`/`kXtfAa68`, 49 data bytes;
`kXtfPreBwMid`, 42 data bytes; the X3's `Uc8279X3Luts.h` banks, 42/49 bytes) reads as **7-byte
groups**, group *g* at offset 7·g:

| byte | meaning | evidence |
|---|---|---|
| 0 | group header: `0x01` in every populated group, `0x00` = unused group (skipped) | constant in all 30 tables seen; the AA bank's second group is `00 00 00 00 00 01 01` |
| 1..4 | four phases, each `level << 6 \| frames` (frames 0..63) | the frame totals of the five tables of a bank agree only under this reading (AA: 2+3+1+1 = 7 in all five; settle bank: 6+1+6+6 + 2+4 = 25 in all five; X3 XTH4 group 0: 16+6+2+3 = 27 in all five); the level bits sit exactly where a table differs from its VCOM twin (`0x41`/`0x81`/`0x83` vs `0x01`/`0x03`) |
| 5 | times to repeat the group | `0x01` everywhere; the family's "TIMES TO REPEAT, 0 = 0 time" (UC8179c datasheet, R21H) |
| 6 | second repeat/tail byte, `0x01` or `0x00` (X3 DU bank group 1) | role unknown; ignored by the model |

Level codes are the UC81xx family's (UC8179c datasheet, R20H/R21H, the same wording in the UC8176):
source tables `00` GND, `01` VDH (drives the pixel black), `10` VDL (drives white), `11` VDHR/floating;
VCOM table `00` VCOM_DC, `01` VCOMH, `10` VCOML, `11` floating. The blackening/whitening direction is
confirmed by the settle bank: its BW (black→white) table is all `0x8x` (VDL) and its WB table all
`0x4x` (VDH).

Where this differs from the datasheets at hand: the UC8179c/UC8176 tables are **6-byte** groups
(a level byte packing four 2-bit levels, four frame bytes, a repeat byte; 7 groups in KW mode). That
reading does not fit these tables (`0x86` would be a 134-frame phase). The UC8253 sibling's
manufacturer header is paraphrased in `Uc8253MurphyLuts.h` as "7-byte groups: a level-select byte,
four sub-phase frame counts, then repeat bytes", and its data (`01 4f 8f 0f 01 01 01`) carries the
levels inside the frame bytes just like here; the freeink X3 notes say the UC8279 format is "group-based
(7-byte groups, 7 groups per LUT in KW mode)". No UC8279 datasheet was available to settle the roles of
bytes 0 and 6 — for every table in use they are `0x01`/`0x00`, so the drive the model computes does
not depend on them.

Frame period: the X4 init programs `PLL 0x0E`. In the UC8179c table FRS `1110` = **150 Hz**
(6.67 ms/frame), so the AA bank runs 7 frames ≈ 47 ms and the settle bank 25 frames ≈ 167 ms. Not
timed on the device yet (`frame_us`, only used for `last_lut_us`; the BUSY time stays the `gray-ms`
table).

Parsed banks (the core's `epd_core_lut_phases`; frames at GND/VCOM_DC hold):

| table | AA `kXtfAa68` (7 frames) | settle `kXtfPreBwMid` (25 frames) |
|---|---|---|
| 0x20 VCOM | DC 2, DC 3, DC 1, DC 1 | DC 6,1,6,6; DC 2,4 |
| 0x21 WW | GND 2, GND 3, **VDH 1**, GND 1 | GND 6, **VDL 1**, GND 6, GND 6; GND 2, GND 4 |
| 0x22 BW | GND 2, **VDL 3**, GND 1, GND 1 | **VDL 6, VDL 1, VDL 6, VDL 6; VDL 2, VDL 4** |
| 0x23 WB | GND 2, **VDL 3**, GND 1, GND 1 (byte-identical to BW) | **VDH 6, VDH 1, VDH 6, VDH 6; VDH 2, VDH 4** |
| 0x24 BB | GND 2, GND 3, **VDL 1**, GND 1 | GND 6, GND 1, GND 6, GND 6; GND 2, **VDH 4** |

`kXtfAa02` (LUT_VER 0x02 units) is the same with 2 frames where 0x68 has 3.

## 2. What CrossPoint sends for an anti-aliased page (desk unit: LUT_VER 0x68)

Source: `src/activities/reader/EpubReaderActivity.cpp` (`renderGrayscalePass`, the non-tiled path —
`supportsStripGrayscale()` is false on this driver), `ReaderUtils.h`, `GfxRenderer.cpp` (2-bit glyphs:
`bmpVal` 0 black, 1 dark grey, 2 light grey, 3 white), `Uc8279X4Driver.cpp`.

1. **Base frame** — the page rendered in `BW` mode: every non-white glyph pixel (`bmpVal < 3`) is
   black. First AA page after a full clear: `displayStart` (DTM2 = base, DTM1 = white, `50 97`,
   `E0 02`, `E5 1E`, PON, `00 17 4D`, DRF — the OTP GC); later pages, and page turns: the base goes
   through `transitionGrayscaleBase`: DTM2 = new base, then `runGrayscalePrecondition`: PTIN, PTL
   full window, `00 37 4D` (REG=1), `03 20`, `E1 02`, `50 D7`, `E0 02`, `E5 5A`, the five 42-byte
   **settle tables** (0x20..0x24), PON, DRF, PTOUT; then DTM1 = new base.
2. **Planes** — the page is rendered twice more into a cleared buffer: `GRAYSCALE_LSB` sets a bit for
   dark-grey pixels (`bmpVal == 1`), `GRAYSCALE_MSB` for dark and light (`bmpVal == 1 || 2`). The
   driver folds them into absolute selectors: `plane0 = base | lsb` → DTM1, `plane1 = plane0 ^ msb` →
   DTM2, both streamed **bitwise inverted** (120 white padding gates, 480 visible rows, padding to 600).
   On the wire (DTM1, DTM2) per pixel: black `(1,1)`, dark `(0,1)`, light `(1,0)`, white `(0,0)`.
3. **AA refresh** (`displayGray`): `00 37 4D`, the five 49-byte AA tables, `50 97`, PON (if off),
   `00 37 4D`, DRF. CDI 0x97 = BDZ 1, BDV 01, DDX `01`: KW mode with NEW/OLD and RAM bit 1 = white
   (UC8179c R50H: DDX[0]=1 → {NEW,OLD} 11 LUTWW, 00 LUTKK, 10 LUTKW, 01 LUTWK). So the classes are:
   black → **WW**, dark → **BW**, light → **WB**, white → **BB**.
4. **Restore**: DTM1 and DTM2 = the recovered base (`plane0 & plane1`), no refresh; the next B/W page
   is a Fast paint with DTM1 = ~frame (`_redriveAfterGray`), i.e. an OTP DU that drives every pixel.

Emulator trace of exactly that (`x4emu run … --fast-epd --trace-epd`, the `test_touch_reader.py`
flow: Home → Browse Files → the test book; guest seconds, first bytes):

```
 9.987  0x10 60000   ff…        DTM1 = white seed          (first AA page: OTP GC base)
10.026  0x13 60000   ff…        DTM2 = base frame
10.065  0x50 97 · 0xe0 02 · 0xe5 1e · 0x00 17 4d · 0x12      DRF, core: full
10.109  0x10 60000              DTM1 = base (displayFinish sync)
10.152  0x10 60000              DTM1 = ~(base | lsb)       copyGrayscaleLsb
10.191  0x13 60000              DTM2 = ~(plane0 ^ msb)     copyGrayscaleMsb
10.191  0x00 37 4d
10.191  0x20 49  01 02 03 01 01 01 01 00 00 00 00 00 01 01 00 00 …
10.191  0x21 49  01 02 03 41 01 01 01 00 …
10.191  0x22 49  01 02 83 01 01 01 01 00 …
10.191  0x23 49  01 02 83 01 01 01 01 00 …
10.191  0x24 49  01 02 03 81 01 01 01 00 …
10.191  0x50 97 · 0x00 37 4d · 0x12                          DRF, core: gray (AA bank, 7 frames)
10.232  0x10 · 10.270 0x13      both planes = recovered base (no refresh)
13.558  0x13 60000              DTM2 = page 2 base          (page turn: settle transition)
13.558  0x91 · 0x90 00 00 03 1f 00 78 02 57 01 · 0x00 37 4d · 0x03 20 · 0xe1 02 · 0x50 d7 · 0xe0 02 · 0xe5 5a
13.558  0x20 42  01 06 01 06 06 01 01 01 02 04 00 00 01 01 00 00 …   (0x21..0x24 likewise)
13.562  0x12 · 0x92                                          DRF, core: gray (settle bank, 25 frames)
13.606  0x10 60000              DTM1 = page 2 base
13.650  0x10 · 13.688 0x13      the AA planes, then 0x00 37 4d, the 5×49 bank, 0x50 97, 0x00 37 4d, 0x12
```

No PON between the AA refreshes (the panel stays powered, as the driver comments say); nine DRFs and
fifteen LUT writes (sizes 42 and 49 only) up to the second reader page.

## 3. The model

Per pixel, `image[x]` (0 black .. 255 white) is the reflectance and persists. A LUT refresh looks up
the pixel's class `(old bit, new bit)` → table (honouring CDI DDX[0]), then runs that table's phases:

```
for each populated group, repeat times:
    for each phase: VDH: R = max(0, R - k*frames);  VDL: R = min(255, R + k*frames);  else hold
```

`k` = `gray_k` (QOM `gray-k`), the reflectance change per frame at VDH/VDL, **default 28**. The
per-class result is precomputed as a 256-entry map at each LUT refresh (`gray_map[4][256]`), so a
frame costs one lookup per pixel. `frame_us` (default 6667) only sizes `last_lut_us`.

Why 28: the settle bank's 25 driven frames must saturate (k ≥ 10.2); 3 frames should land at a "dark
grey"; 28 puts it at **84**, the level the previous approximation used for its darker grey, so the
owner's earlier look at the reader applies. The X3's AA bank (2 and 5 frames) would give 56 and 140.

Levels on the desk unit's bank, from a black base (k = 28):

| pixel | class | drive | reflectance |
|---|---|---|---|
| black | WW | 1 frame VDH | 0 (a white pixel labelled black would drop to 227) |
| dark grey | BW | 3 frames VDL | **84** |
| light grey | WB | 3 frames VDL | **84** — the same table as dark |
| white | BB | 1 frame VDL | 255 |

**Finding:** with `kXtfAa68` (and `kXtfAa02`) the BW and WB tables are byte-identical, and both grey
classes start from the same black base pixel, so a faithful model yields **three** levels
(0 / 84 / 255), not four. The driver's own comment says as much ("BW/WB carry the dark-gray
channel"). Four ordered levels need a bank whose BW and WB differ — the X3's does (5 vs 2 VDL
frames; `test_waveform_four_levels`). The previous approximation showed 0 / 170 / 85 / 255 with the
dark-grey glyph pixels *lighter* (170) than the light-grey ones (85).

The settle bank clears greys correctly in the model: a grey (84) whose new base is white gets 25
VDL frames → 255; one whose new base is black gets BB's 4 VDH frames → 0; white/black stay.

## 4. Validated and not

Validated (host tests, `models/tests/test_epd.c`): the 49-byte parse of `kXtfAa68` and the 42-byte
settle bank (phases, groups, repeat, zero padding of a short table); the reader sequence above
(base → AA → restore → settle → AA → OTP DU) with the levels of the table; four ordered levels with
a bank whose BW ≠ WB; the tunable; DDX[0] polarity; the approximation unchanged with the flag off; OTP
refreshes identical with the flag on or off; SSD1677 untouched by the flag (its 0x32 LUT is still the
fixed table). The fixture replay stays green.

Not validated: the levels themselves against the glass (the owner compares `reader-waveform.png`
with a photo of an AA page; `gray-k` is the knob), the frame rate for PLL 0x0E on a UC8279, whether
an OTP DU really drives WW/BB pixels (the model, like the approximation, drives every pixel fully —
ghost residue from AA pages is not modelled), the settle bank's exact effect, and the roles of group
bytes 0 and 6.

## 5. Renderings and tests

`tests/golden/reader-page1.png` is the approximation (a run of the reader flow with the default
reproduces it in 0 pixels). With `waveform-gray=on` the same page differs in **14,967 pixels**, all
grey and nothing else: the 7,622 pixels the approximation showed at 170 (the dark-grey glyph
pixels) become 84, and the 7,345 it showed at 85 (light-grey glyph pixels) become 84. Black (32,453)
and white (336,580) counts are identical. Renderings for the owner's comparison with a photo of the
device: `reader-approx.png` / `reader-waveform.png` (landscape panel), `reader-*-crop.png` (upright
portrait, the first four text lines, 3×) and `reader-crops-approx-top-waveform-bottom.png` in the
session's scratch `gray/` directory.

`make -C models test` runs the host tests; `tests/test_touch_reader.py` and `tests/test_boot.py` run
with the default (approximation) and their goldens are unchanged.

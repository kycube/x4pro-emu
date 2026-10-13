# Stock `xteink_app` 7.6.8: what moved since 7.5.4

Session of 2026-10-12. The device took an OTA to **7.6.8** (built 19:00:34 Sep 22 2026, ESP-IDF
v6.0.1, 6,250,640 bytes, ELF sha256 `d3caaddf…ec24833`); it sits in **app0** (flash 0x10000) of
`images/device/flash-2026-10-12-a.bin` and boots (`tools/device.py slots --from-dump`: otadata entry
0, seq 3, VALID). app1 still holds 7.5.4. `images/device/stock-app0-7.6.8.bin` is the extracted app.
This file is the re-location of the sites the tools need, with the evidence; the constants are in
**`tools/stockver.d/7.6.8.json`**. Numbers marked *(static)* were read from the binary but not
exercised. Because 7.6.8 is in app0, `stockdev.app_base` finds it and the three stock tools work on
a 16 MB flash copy directly (site offset in the flash image = 0x10000 + app offset).

**Headline: the Lua route is open in 7.6.8, and it was open in 7.5.4 without any patch.** An
unpatched 7.6.8 shows a **"Mini Apps"** row in the nav menu as soon as `/sdcard/XTApps` on the card
holds an app, opens the app list ("Extensions") and runs the unsigned `tests/data/hello-app` (its
tap counter counts). With no app on the card the menu has five rows (Read / All Files / USB / Cloud /
Settings), which is what the first look at 7.6.8 saw. An unpatched **7.5.4** (app1 of
`flash-2026-09-07-a.bin`) with the same card also shows the Mini Apps row, re-checked in this
session (`v754-menu-app.png`). That contradicts docs/stock-7.5.4.md §3/§4 ("five rows" on the
control boot, "the flag looks unreachable by data"); the code that sets the flag is in §2 below.
`lua-apps-row` therefore only matters for a card without apps.

**The string packs changed format**: 16 languages and u32 offsets. `tools/stockstrings.py` and
`tools/stockver.py` needed a small change to read them (an optional `"offset_width": 4` per pack;
§1). 7.2.4 and 7.5.4 behave byte for byte as before (old and new tool outputs and written images
compared on both).

## 1. What changed in the binary, for our purposes

| | 7.5.4 | 7.6.8 |
|---|---|---|
| DROM (segment 0) | 0x3c340020, 0x1d8f4c B, file 0x20 | **0x3c380020**, 0x2594f0 B (+513 KB), file 0x20 |
| IROM (segment 2) | 0x42000020, 0x33ad2c B, file 0x1e0020 | 0x42000020, **0x37f324** B (+273 KB), file **0x260020** |
| DRAM segments | 0x3fc9b600 (0x70a4), 0x3fca26a4 (0x34a0) | 0x3fc9b700 (0x6b00), 0x3fca2200 (0x3664) |
| IRAM | 0x40378000, 0x13564 | 0x40378000, 0x13608 |
| image size | 5,445,680 (0x531830) | 6,250,640 (0x5f6090); checksum byte at 0x5f606f, SHA-256 at 0x5f6070 |

So every VA moved again: IROM app file offset = VA − 0x41da0000 (7.5.4: VA − 0x41e20000); DROM
file offset = VA − 0x3c380000. Unlike the 7.2.4 → 7.5.4 step, the code around our sites was not
restructured: the developer stub's wrapper, the menu-slot predicate and the nav-menu builder recur
with their 7.5.4 bytes (only call/l32r displacements differ), so each site was found by an exact or
displacement-wildcarded byte search and then confirmed by its callers (§2).

Other differences that matter to the stock tools:

- **The packs are now 16 languages with u32 offsets.** The UI text lives in an `XTTM` resource
  container (DROM 0x3c430914; 7.5.4 0x3c3e6d64): magic, u16 version **10** (7.5.4: 9; the code that
  compares the magic, at 0x4218fdac, then accepts versions 5..10), a byte at +12 that went 0 → 1,
  then a directory of (u32 offset, u32 size) pairs; entry 2 is the counts pack, entry 3 the label
  pack, entry 4 the bitmap directory.
  A headed pack is now `u16 group_count; u8 lang_count = 16; u8 1; u8 lang_ids[16] = 0..15; u32
  blob_size; u32 offsets[groups][16]; blob` (7.5.4: `u16 groups; u16 lang_count = 4; ids[4]; u16
  blob_size; u16 offsets`). The directory sizes match this reading exactly (labels: 24 + 18,624 +
  85,036 = 0x19504). What header byte 3 means to the firmware's own reader is *not* verified (the
  reader was not found); the tool requires it to be 1 for width 4.
- **Languages 4..15** are de, it, fr, es, pl, nl, sv, ga, pt, da, cs, el (the order of the toast
  pack's language-name groups 913..924: Deutsch, Italiano, Français, Español, Polski, Nederlands,
  Svenska, Gaeilge, Português, Dansk, Čeština, Ελληνικά). zh-CN, en, zh-TW, ja keep ids 0..3.
- **Tool change** (the smallest that describes this): `PackSpec.offset_width` (2 default, or 4) in
  `tools/stockver.py` (schema docstring updated, `versions` prints "u32 offsets"), and in
  `tools/stockstrings.py` the width-4 header parse, u32 reads/writes of offsets and blob_size, the
  u32 ceiling in `set`/`compact`, sixteen `LANG_NAMES`, and `list`/`get --lang` resolved against the
  pack's own languages. The width-2 code path is the old one, unchanged.
- **Labels: 269 → 291 groups.** difflib over the zh-CN/en/zh-TW/ja tuples aligns 267 of 7.5.4's 269
  groups 1:1. Ids 0..48 are unchanged (Read 9, Extensions 11, Mini Apps 12, All Files 13, USB 14,
  Settings 16); 7.5.4's "Font" (49) is replaced by seven font-picker groups 49..55 ("All", "No fonts
  available", "No TTF fonts", "No XTF fonts", "No BIN fonts", "Please put font files in the Fonts
  folder", "Size"), two are inserted at 148..149 ("Vertically Center Text", …), "Get Fonts" (211) is
  replaced by ten font-download groups 219..228, "Open App" / "Uninstall App" are inserted at 250..251
  and "TTF" / "XTF" / "BIN" at 269..271. Moves of the named groups: Bluetooth 55→61, Developer 69→75,
  Memory 70→76, Boost 72→78, Full Refresh 73→79, Light 74→80, Screen Capture 76→82, Sleep Lock
  77→83, Language 103→109, Cloud 152→160, Upgrade 184→192, Device information 185→193, System Font
  207→215. No renames among them. Preload List is 47, Statistics 10, BGIO 77, Dark Mode 151.
- **Counts: 18 → 17 groups** ("%d fonts", 7.5.4 group 2, is gone).
- **Toasts: 874 → 925 groups**, still headerless, now 16 languages (blob 363,939 bytes). With the
  tool change it is readable, so 7.6.8's file carries `packs.toasts` instead of a
  `toasts_not_loadable` note. 7.5.4's file is unchanged (its toasts stay refused).
- New strings around apps: `bgio.decrypt_xtapp`, `xteink.xtapp.wrap.%s`, `xtapps_keys`,
  `:install-xtapp`, `xtapp_dec`, `xtapps/v1`, `xtapp-save/v1`, `xtapp-flash-root-v1`, "Installed app
  limit reached (64)", an uninstall dialog (toasts 907..912), and `.xtapp` / `.xtfont` / `.ttf` in a
  file-manager extension list. `XTAP` occurs 3 times (7.5.4: 2). *(static, not examined: the
  plain, unsigned card app still lists and runs, so none of this gates it.)*
- `XTF0` still occurs five times, `XTZB` not at all, as in 7.5.4.

## 2. The re-located sites

App offsets are into the bare app image; in the 2026-10-12 flash image add **0x10000** (app0).
Confirmed with `tools/appdis.py images/device/stock-app0-7.6.8.bin VA`.

| site | 7.5.4 | 7.6.8 app offset / VA | bytes | how it was found |
|---|---|---|---|---|
| developer-mode stub (`developer-menu`, `stockdev.py`) | 0x4edad4 / 0x4230dad4 | **0x5afb60 / 0x4234fb60** | `36 41 00 0c 02 1d f0` → byte +4 `02`→`12` | 7.5.4's 20-byte wrapper FUN_4214ab34 (`entry / l32i a8,a2,88 / l32i a10,a8,176 / l32r / callx8 / s8i a10,a2,32 / retw.n`) occurs exactly once, as FUN_4217c978 (file 0x3dc978), equal except the two l32r displacement bytes. Its literal (0x42146fb4) is 0x4234fb60, a 7-byte return-0 stub whose only reference besides the literal is that `callx8`, in a cluster of 5..9-byte stubs at 0x4234fb34..0x4234fb60. The wrapper's single caller FUN_4217d0f0 is 335 bytes like 7.5.4's light-panel opener FUN_4214b2b4, same eight callees, 19 bytes different (displacements). **Boot-verified**: Memory / Developer / Screen Capture / BGIO in the light panel. |
| gate A, menu-slot predicate (`hidden-menu-rows`) | 0x4f951a / 0x4231951a in 0x42319508 | **0x5bcc36 / 0x4235cc36** in the predicate **0x4235cc24** | `b6 29 07` → `b6 29 ff` | 7.5.4's 31-byte predicate occurs byte for byte exactly once (file 0x5bcc24), last load `l8ui a2,a8,176` included. Single caller: the 84-byte builder FUN_4217db28 (4 bytes differ from 7.5.4's FUN_4214bce8, displacements), which reads the order table `00 01 02 04 05 06 03 07` (DROM 0x3c3e9940); the opener that calls it (0x4217df5f) reads the slot→page table `02 1c 16 09 0b 0a 0d 01` (0x3c3e9948, at 0x4217df28). Both tables unchanged. **Boot-verified**: Preload List and Statistics rows. |
| Lua row (`lua-apps-row`) | 0x4f9522 / 0x42319522 | **0x5bcc3e / 0x4235cc3e** | `22 08 b0` → `22 a0 01` (`l8ui a2,a8,176` → `movi a2,1`) | Same load as 7.5.4, 8 bytes after gate A. **Who writes presenter+176**: FUN_4217db7c (67 bytes), called by the opener at 0x4217df58 right before the builder, clears the flag and then sets it to `FUN_421ed3b0(presenter+172, cb, 0, 1) != 0`, an enumeration of `/sdcard/XTApps` (string 0x3c3c1158) with a limit of 1. 7.5.4 has the same pair, FUN_4214bd3c / FUN_421b09e0 (string 0x3c37f670). **Boot-verified**: unpatched, the row appears with an app on the card and not without; patched, it appears either way (empty card: "No apps available"). |
| counts pack | header 0x3c3e6de0, 18 groups, u16 | header **0x3c430990** (file 0xb0990), **17 groups**, u32, blob 0x3c430de8 (2,714 B) ending 0x3c431882, limit 0x3c431884 | | XTTM directory entry 2 (offset 0x7c, size 0xef2 = 24 + 1,088 + 2,714), then every offset validated. Free tail 2 bytes (two zeros before the label header). *Not boot-verified.* |
| labels pack | header 0x3c3e712c, 269 groups, u16 | header **0x3c431884** (file 0xb1884), **291 groups**, u32, blob 0x3c43615c (85,036 B) ending at the limit **0x3c44ad88** | | XTTM entry 3 (0xf70, 0x19504); entry 4, the bitmap directory (`30 00 00 00 2c 00 00 00 …`), starts at 0x3c44ad88, so the stock free tail is **0 bytes** (7.5.4: 2). `compact` frees 784 bytes on the pristine image. **Boot-verified**: an in-place "All Files" → "Files" plus, after `compact`, an appended "USB" → "Transfer" (u32 offset 84,242, past the u16 ceiling) both show in the nav menu (`s-menu-relabel.png`). |
| toasts pack | offsets 0x3c47fb10 (u32), 874 groups x 4, not loadable | offsets **0x3c4e7f80**, blob **0x3c4f66c0** (363,939 B), **925 groups x 16**, u32, limit 0x3c54f463 | | The 7.2.4 blob head (NUL, 取消, Cancel, キャンセル, then Abbrechen, Annulla …) at 0x3c4f66c0; walking back in 64-byte steps while every word is 0 or a string start gives 925 groups from 0x3c4e7f80, ending exactly at the blob, preceded by IROM function pointers. Last string ends 0x3c54f462, where `user_config` begins. Group 0 = Cancel, 3 = "Region set successfully". `compact` frees 3,103 bytes. *Not boot-verified.* |
| LANG_COUNT | 4 | **16** (ids 0..15 in both headed packs) | | |
| `.xtf` CRC-32 checks | FUN_42112dbc, header check 0x42113caa–0x42113ccf | routine **FUN_4213942c** (46 bytes, the 7.5.4 body except the l32r displacement), header check **0x4213a31a–0x4213a33f** (`movi a12,52 … call8 … xor −1 … beq`, instruction for instruction 7.5.4's), five call sites as in 7.5.4 (0x421397c8, 0x4213a32c, 0x4213aacc, 0x4213ab3b, 0x4213ae9e) *(static)* | | Readers of the `0xEDB88320` literal at 0x42105050 (8 in 7.6.8, 2 in 7.5.4); a second byte-identical copy, FUN_42136c4c, has seven other callers (not examined). `tools/xtfont.py` not run on 7.6.8. |
| wallpaper / lock-screen NVS keys | present | `wallpMode`, `lockscrWallp`, `shutWallp`, `lockscrIdle`, `lockscrShut`, `lockscrLuaApp`, `lockscrMode` present once each *(static, references not followed)* | | `bytes.count` on the image. |

Layout note from the boot (unchanged from 7.5.4): with all three patches the menu has eight entries
and the eighth, Settings, falls off the panel; seven rows are visible (`p-menu.png`: Read, Preload
List, Statistics, All Files, USB, Cloud, Mini Apps). Scrolling was not tried.

## 3. Verification (emulator, scratch copies only)

Scratch flash = `tools/mkflash.py OUT --raw images/device/flash-2026-10-12-a.bin` followed at once by
`tools/nvsedit.py OUT redact` (every copy); card = `mksd.py --size 256M --src images/device/sd-files`
+ `xtapp.py install … tests/data/hello-app`, and a second card without the app. Boots with
`x4emu --name r768<x> run … --boot-hold-power`, `boot-preflight … decision=0` on every boot.
Screenshots (in the session scratch directory `…/scratchpad/r768/`, not in the repository):

- unpatched, card with the app: Home (`base-home.png`, "Bookshelf Empty"), menu with six rows Read /
  All Files / USB / Cloud / **Mini Apps** / Settings (`base-menu.png`), Mini Apps → "Extensions"
  listing "Hello Lua" (`base-apps.png`), tap → the app's rectangle, text and circle, "taps: 0"
  (`base-hello.png`);
- unpatched, card without apps: five rows Read / All Files / USB / Cloud / Settings
  (`base-noapp-menu.png`);
- unpatched 7.5.4 (app1 of a redacted copy of `flash-2026-09-07-a.bin`), card with the app: the same
  six rows including Mini Apps (`v754-menu-app.png`);
- all three patches (`stockpatch.py apply OUT all` on the flash copy; esptool on the sliced app:
  checksum 0xfd valid, validation hash valid), card with the app: Home (`p-home.png`), menu with seven
  visible rows (`p-menu.png`), Mini Apps (668,150) → "Extensions" with "Hello Lua" (`p-apps.png`; the
  first tap fell into the status-bar clock refresh and drew nothing, `p-after-tap1.png`, the retry
  worked), the app (`p-hello.png`), a tap inside it → "taps: 1" (`p-hello-tap.png`);
- all three patches, card without apps, fresh instance: pull-down light panel with Memory /
  Developer, Screen Capture and BGIO / Developer buttons (`p-light.png`), then the menu still shows
  Mini Apps (`q-menu-noapp.png`) and it opens "No apps available" (`q-apps-noapp.png`);
- label relabel (above): `s-menu-relabel.png` shows Read / **Files** / **Transfer** / Cloud / Settings.

The hello app's screen differs from `tests/golden/stock-lua-hello.png` (made on 7.2.4) by 1,143
pixels (0.30 %), all in its three text lines (x 117..132, 158..174, 597..616; rectangle, line and
circle identical): the text renders differently in 7.6.8, so that golden would not hold unchanged on 7.6.8 (`tests/test_lua_apps.py` was not run).

`tools/stockpatch.py list images/device/stock-app0-7.6.8.bin` reports all three sites `original`
at the offsets above; `tools/stockdev.py … --check` finds the stub (unpatched, exit 1);
`tools/stockstrings.py … list --pack labels --lang en` reads the 291 groups with the known ids.
`tests/test_stockver.py::test_the_7_6_8_ota_image_is_identified_with_its_own_sites` and
`tests/test_stockstrings.py::test_7_6_8_wide_packs_read_and_write` pin this (they skip without the
image).

## 4. Not found / not done

- The firmware's own pack-string reader (who interprets header byte 3 and the XTTM +12 byte) was not
  located; the u32 layout rests on the container directory sizes and on every offset validating, and
  on the relabel boot.
- 7.2.4's `presenter+172` flag (docs/stock-7.5.4.md says nothing writes it) was not re-examined in
  the light of the 7.5.4 / 7.6.8 writer found here.
- The new `.xtapp` install / decrypt / uninstall machinery and the 64-app limit: strings only.
- The light-panel developer buttons draw their labels clipped ("Screen Ca…") in `p-light.png`; not
  compared with 7.5.4's panel.
- `tools/device.py` was only used as `slots --from-dump` here; its writers' plans for this layout (7.6.8
  booting from app0, 7.5.4 in app1) were walked offline later for the parity session (`docs/parity.md`,
  "A device session", steps 4 and 9).

## 5. How to redo the analysis

```
.venv/bin/python tools/ghidra_stock.py --image images/device/stock-app0-7.6.8.bin --version 7.6.8 analyze
G=".venv/bin/python tools/ghidra_stock.py --image images/device/stock-app0-7.6.8.bin --version 7.6.8"
$G xrefs 0x3c3e9940          # the order table -> the builder FUN_4217db28
$G callers 0x4235cc24        # the predicate's single caller
$G callers 0x4234fb60        # the developer stub's single caller (the wrapper)
.venv/bin/python tools/appdis.py images/device/stock-app0-7.6.8.bin 0x4235cc24 0 0x1f
.venv/bin/python tools/stockver.py                                   # validates tools/stockver.d/*.json
```

(zsh does not split `$G`; write it out or use a wrapper script.) The recipe, for the next OTA:
(1) search the image for the previous version's predicate bytes exactly and for the wrapper shape
with the displacement bytes wildcarded; if they recur, confirm by callers (predicate ← builder ←
opener reading the slot→page table; stub ← wrapper ← light-panel opener) and stop; if not, fall back
to docs/stock-7.5.4.md §5 (tables → opener → builder → predicate by xrefs). (2) For the packs, read
the `XTTM` container's directory (search for the magic; the entries give each pack's offset and
size) and check the header shape against the directory size before trusting it; for the toasts,
find the 7.2.4 blob head and walk the offset table back with the current language count and width.
(3) Align label ids with difflib over the first four languages. (4) Patch a redacted scratch copy,
boot, and believe only the screenshot; boot an unpatched copy with **and** without an app on the
card before concluding anything about the Mini Apps row.

# Stock `xteink_app` 7.5.4: what moved since 7.2.4

Session 8 (2026-09-07). The device took an OTA to **7.5.4** (built 12:25:04 Sep 5 2026, ESP-IDF
v6.0.1, 5,445,680 bytes, ELF sha256 `052d7443…caeb5`); it sits in **app1** (flash 0x7F0000) of
`images/device/flash-2026-09-07-a.bin` and boots (otadata entry 1, seq 2). app0 still holds 7.2.4.
`images/device/stock-app1-7.5.4.bin` is the extracted app. Every address in `docs/stock-firmware.md`
is 7.2.4; this file is the re-location of the sites the tools need, with the evidence, and the
constants are in **`tools/stockver.d/7.5.4.json`** (the tools select it by the image's
`esp_app_desc`). Numbers marked *(unverified)* were read from the binary but not exercised.

**Headline: the Lua host is still in 7.5.4 and still takes unsigned apps.** The XTApps enumeration
and the script host are wired into code, not dead strings (`/sdcard/XTApps` has 46 code
references, `index.lua` 28, `lockscreen.lua` 9, `%s/%.190s/app.xtapp` 1, `sys.battery`/`sys.status`
4 each, `set_as_lockscreen_app` 1; `Lua 5.5`, `xteink.xtapp.appdek.wrap.v2`, `assets.xtab` present).
Verified end to end in the emulator: with the three patches below, the nav menu gains a **"Mini
Apps"** row (7.2.4's "Lua Apps", renamed), it opens the app list ("Extensions"), the unsigned
313-byte-container `tests/data/hello-app` from the card is listed, and its `index.lua` runs
(rectangle, text, circle, tap counter). Nothing in the OTA signed-locked that route. 7.2.4 is not
needed as a fallback for the Lua plan.

## 1. What changed in the binary, for our purposes

| | 7.2.4 | 7.5.4 |
|---|---|---|
| DROM (segment 0) | 0x3c380020, 0x1ac214 B, file 0x20 | **0x3c340020**, 0x1d8f4c B (+184 KB), file 0x20 |
| IROM (segment 2) | 0x42000020, 0x3764e8 B, file 0x1b0020 | 0x42000020, **0x33ad2c** B (−244 KB), file **0x1e0020** |
| DRAM segments | 0x3fc9b600 (0x3ddc), 0x3fc9f3dc (0x5ee8) | 0x3fc9b600 (0x70a4), 0x3fca26a4 (0x34a0) |
| IRAM | 0x40378000, 0x135f0 | 0x40378000, 0x13564 |
| image size | 5,503,680 (0x53fac0) | 5,445,680 (0x531830); checksum byte at 0x53180f, SHA-256 at 0x531810 |

So **every VA moved**: IROM app file offset = VA − 0x41e20000 (7.2.4: VA − 0x41e50000); DROM file
offset = VA − 0x3c340000. The code was recompiled differently (244 KB less), so the 7.2.4 byte
patterns do not recur: none of the three code sites was found by an exact byte search; they were
found by instruction shape and by cross-references, as described per site below.

Other differences that matter to the stock tools:

- **The label pack grew from 234 to 269 groups** and inserted groups early (three at 5–7), so every
  label id from 5 up moved: Read 6→9, Extensions 8→11, **Lua Apps 9→12 and renamed "Mini Apps"**
  (轻应用 / 輕應用 / ミニアプリ), All Files 10→13, USB Mode 11→14 ("USB"), Settings 13→16,
  Bluetooth 50→55, Developer 64→69, Memory 65→70, Boost 66→72, Full Refresh 67→73, Light 68→74,
  Screen Capture 70→76, Sleep Lock 71→77, Language 91→103, Cloud Sync 137→152 ("Cloud"), Upgrade
  168→184, About Device 169→185 ("Device information"), System Font 192→207. 225 of the 234 old
  groups align 1:1 (difflib over the four-language tuples); the rest are renames at the aligned
  position or removals ("Display" 131, "Button Settings" 171). New labels include "BGIO" (71, a new
  developer button), "Dark Mode" (143), a "General / About device / Reading settings / Display &
  personalization / Network & connections" settings vocabulary (213–232).
- **The toast pack changed format**: 874 groups (was 688) with **u32 offsets** (the blob is 72,033
  bytes, past the u16 ceiling 7.2.4 was already close to). `tools/stockstrings.py` cannot describe
  it (its PackSpec is u16), so `packs.toasts` is absent from the version file and the tool refuses
  the toast pack on 7.5.4 with a clear message. The numbers are recorded under `toasts_not_loadable`
  for a future reader.
- The nav-menu builder was restructured (§2, `lua-apps-row`): slot 3 is now walked in the loop
  through an **order table** `00 01 02 04 05 06 03 07` (DROM 0x3c3a6e78) instead of being appended
  after it, so the Mini Apps row lands between Cloud and Settings.
- The `XTZB` tag string is gone; the string `XTF0` occurs five times in DROM instead of two
  (*(guess)*: 7.5.4 embeds `.xtf` font data; not examined).
- The counts pack is byte-identical in content (18 groups, 689-byte blob).

## 2. The re-located sites

All app offsets are into the bare app image; in the 2026-09-07 flash image add **0x7F0000** (app1),
not 0x10000. Confirmed with `tools/appdis.py images/device/stock-app1-7.5.4.bin VA`: the instruction
at each site is the one named.

| site | 7.2.4 | 7.5.4 app offset / VA | bytes | how it was found |
|---|---|---|---|---|
| developer-mode stub (`developer-menu`, `stockdev.py`) | 0x4eabe0 / 0x4233abe0 | **0x4edad4 / 0x4230dad4** | `36 41 00 0c 02 1d f0` → byte +4 `02`→`12` | 7.2.4's 20-byte wrapper FUN_4213c6ac (`entry / l32i a8,a2,84 / l32i a10,a8,176 / l32r / callx8 / s8i a10,a2,32 / retw.n`) recurs in 7.5.4 as FUN_4214ab34 (file 0x32ab34, presenter field 84→88); its `l32r` literal is 0x4230dad4, a 7-byte return-0 stub with exactly that one caller, in the same compile-time-stub cluster (0x4230bxxx–0x4230dxxx; 7.2.4: 0x4233axxx). The wrapper's caller FUN_4214b2b4 has the callee pattern of 7.2.4's light-panel opener FUN_4213ce68. **Boot-verified**: the light panel shows Memory / Developer / Screen Capture (+ the new BGIO). |
| gate A, menu-slot predicate (`hidden-menu-rows`) | 0x4f516a / 0x4234516a in the predicate 0x42345158 | **0x4f951a / 0x4231951a** in the predicate **0x42319508** | `b6 29 07` → `b6 29 ff` (`bltui a9,2,+7`) | The predicate is the only 31-byte function in 7.5.4 IROM matching 7.2.4's byte for byte except its last load (`l8ui a2,a8,176`, was 172); it is called from the nav-menu builder FUN_4214bce8 (file 0x32bce8), whose loop reads the unchanged slot→page-id table `02 1c 16 09 0b 0a 0d 01` (DROM **0x3c3a6e80**; 7.2.4 0x3c3dc8b0). **Boot-verified**: Preload List and Statistics rows appear. |
| Lua row (`lua-apps-row`) | 0x2ed873 / 0x4213d873, `82 02 ac` (`l8ui a8,a2,172` in the builder) | **0x4f9522 / 0x42319522** | `22 08 b0` → `22 a0 01` (`l8ui a2,a8,176` → `movi a2,1`) | **The 7.2.4 site has no counterpart.** 7.5.4's builder no longer skips slot 3 and appends it behind `presenter+172`; it walks all eight slots through the order table and the predicate alone gates slot 3 with `presenter+176` (the same never-written flag). The equivalent edit is that load, inside the predicate, 8 bytes after gate A. **Boot-verified**: a "Mini Apps" row appears, opens the app list, the hello app runs. |
| counts pack | header 0x3c3f7a08 | header **0x3c3e6de0** (file 0xa6de0), 18 groups, blob 0x3c3e6e7a (689 B), limit 0x3c3e712c | | DROM scan for the header shape (`u16 groups, u16 4, 00 01 02 03, u16 blob_size`) + validation of every offset. Parsed by `stockstrings.py`; *not boot-verified* (no relabel run). |
| labels pack | header 0x3c3f7d54, 234 groups | header **0x3c3e712c** (file 0xa712c), **269 groups**, blob 0x3c3e799e (16,248 B) ending 0x3c3eb916, limit **0x3c3eb918** | | Same scan; two alignment zeros then the bitmap directory `30 00 00 00 2c 00 00 00 …`, so the free tail is 2 bytes as in 7.2.4. `stockstrings.py … get 12` → Mini Apps, `get 76` → Screen Capture. *Not boot-verified*. |
| toasts pack | offsets 0x3c490124 (u16), blob 0x3c4916a4, 688 groups | offsets **0x3c47fb10 (u32)**, blob **0x3c4831b0** (72,033 B), **874 groups**, limit 0x3c494b11 *(unverified)* | | The 7.2.4 blob head (NUL, 取消, Cancel, キャンセル, 国内 / Domestic …) found at 0x3c4831b0; walking back in 16-byte steps while all four u32 words are 0 or point at a string start gives 874 groups (preceded by a table of IROM function pointers, not by a header); group 0 = Cancel, 3 = "Region set successfully", 873 = "TF card unavailable; cannot export the report". No tool reads it. |
| LANG_COUNT | 4 | 4 (ids 0,1,2,3 in both headed packs) | | |
| `.xtf` CRC-32 checks | routine 0x4210d754, header check 0x4210e52e–0x4210e553 | routine **FUN_42112dbc** (46 bytes, byte-identical body, polynomial literal 0x421057d8), header check **0x42113caa–0x42113ccf** (`movi a12,52 … call8 … xor −1 … beq` against the header word), data-CRC loop in FUN_42113e7c at 0x4211445c and 0x421144cb — all *(static, unverified)* | | Found through the `0xEDB88320` literal's readers and the `movi a12,52` shape; the three call sites are instruction-for-instruction those of 7.2.4 (0x4210e540, 0x4210ecb3, 0x4210ed23). The scheme is unchanged; `tools/xtfont.py` packages should install as before (not run on 7.5.4). |
| wallpaper / lock-screen NVS keys | strings present | `wallpMode`, `lockscrWallp`, `shutWallp`, `lockscrIdle`, `lockscrShut`, `lockscrLuaApp`, `lockscrMode` all present, 4–7 code references each from the same three config functions (0x421ce7b0, 0x421ceda0, 0x421d0480) *(static)* | | `strings` + `ghidra_stock.py --version 7.5.4 xrefs`. |

Layout note from the boot: with **all three** patches the menu has eight entries and the eighth,
Settings, falls off the panel at the 85 px row pitch (screenshot `p-menu.png`: Read, Preload List,
Statistics, All Files, USB, Cloud, Mini Apps). With `lua-apps-row` alone the menu is six rows and
Settings stays reachable (`l-menu.png`). Whether the eight-row list scrolls was not tried.

## 3. Verification (emulator, scratch copies only)

Scratch flash image = `tools/mkflash.py --raw images/device/flash-2026-09-07-a.bin`, `nvsedit.py
set-u8 user_config net_en 0`, card = `mksd.py --src images/device/sd-files` + `xtapp.py install
tests/data/hello-app`. The three sites were written into app1 with a 40-line scratch script over
`stockdev.refresh_image`/`verify_image` (base 0x7F0000); `esptool image-info` on the sliced app:
checksum 0xa3 valid, validation hash valid. Unpatched control boot first (`base-home.png`,
`base-menu.png`: five rows Read / All Files / USB / Cloud / Settings). Patched boot: Home
(`p-home.png`, `boot-preflight … decision=0`), hamburger (88,38) → seven rows (`p-menu.png`), tap
Mini Apps (668,150) → "Extensions" page listing "Hello Lua" (`p-apps.png`), tap the app (225,355) →
the script's drawing (`p-hello.png`); a second instance, pull-down from the portrait top edge →
light panel with Memory / Developer / Screen Capture / BGIO (`p-light.png`). Screenshots are in the
session scratch directory (`…/scratchpad/a1/`), not in the repository.

`tools/stockpatch.py list images/device/stock-app1-7.5.4.bin` reports all three sites `original`
at the offsets above; `tools/stockdev.py … --check` finds the stub; `tools/stockstrings.py`
reads the counts and label packs and refuses the toasts with a message naming the version file.

## 4. Not found / not done

- **`packs.toasts` for `stockstrings.py`**: the pack exists but in a u32-offset form the tool cannot
  describe; left out on purpose (a u16 parse would be wrong rather than refused).
- **Gate B / who writes `presenter+176`**: 7.2.4's twin stub 0x4233abe8 wrote the flag behind
  `presenter+140`; the 7.5.4 nav-menu opener (0x4214c040–0x4214c190) shows no such store or stub
  call, so the flag looks unreachable by data. Not chased: the patch makes it moot. No NVS route to
  enable Mini Apps was found in the time box.
- The icon table, the `Display` method names, the layout constants, the meaning of the five `XTF0`
  hits: untouched (same open items as 7.2.4).
- `tools/device.py` still targets app0 and assumes app0 boots; nothing here changes that.

## 5. How to redo the analysis

```
.venv/bin/python tools/ghidra_stock.py --version 7.5.4 analyze          # ~1 min, images/ghidra/stock-7.5.4/
.venv/bin/python tools/ghidra_stock.py --version 7.5.4 xrefs 0x3c3a6e80  # the slot->page table -> the menu opener
.venv/bin/python tools/ghidra_stock.py --version 7.5.4 callers 0x42319508
.venv/bin/python tools/appdis.py images/device/stock-app1-7.5.4.bin 0x42319508 0 0x1f
.venv/bin/python tools/stockver.py                                        # validates tools/stockver.d/*.json
```

`ghidra_stock.py` takes `--image PATH` and/or `--version TAG` before or after the subcommand
(default unchanged: 7.2.4); the project, exports and staleness check follow the chosen image. The
recipe that found the sites, for the next OTA: (1) search DROM for the slot→page table bytes and
follow its READ xref to the menu opener, then the `call8` to the builder and the builder's `callx8`
to the predicate; (2) search IROM for the 20-byte wrapper shape (`entry; l32i; l32i; l32r; callx8;
s8i …,32; retw.n`, offsets wildcarded) and resolve its `l32r` to the developer stub, checking the
stub has that single caller; (3) scan DROM for the `u16 n, u16 4, 00 01 02 03, u16 size` header and
validate offsets; for the toasts, find the 7.2.4 blob head and walk the offset table back (try u16,
then u32); (4) the CRC routine is the 46-byte reader of the `0xEDB88320` literal with the
`movi a12,52` caller; (5) patch a scratch copy of the booting slot with `refresh_image`, boot, and
believe only the screenshot.

# The stock app's UI layer: a map for modifications

Survey of `images/device/stock-app0-7.2.4.bin` (`xteink_app` 7.2.4, ESP-IDF v6.0.1, no source),
made 2026-09-07 for one goal: make the stock UI prettier with the emulator as the workbench. It says
what is a **data-only** change (a file on the card, bytes replaced in place in the image) and what
needs a **code patch**, with the evidence (app VAs, file offsets, strings, class names). Everything
was read from the binary and the card files with `strings`, `xtensa-esp-elf-c++filt`, small Python
scripts over the image's segment table, and `xtensa-esp-elf-objdump` (`tools/appdis.py` style); nothing
was run on the device. Guesses are marked *(guess)*. Addresses are app VAs unless "file" is said;
file offset = VA − segment load address + segment file offset (table below).

Scratch material (disassembly windows, string lists, vtable dumps) is in the session's scratchpad
under `survey/` and is not needed to use this document.

## 1. The binary's shape

`esptool image-info`: 8 segments, entry 0x40379908, checksum 0x89 and the appended SHA-256 both valid,
"MMU page size: 64 KB", image size 5,503,680 bytes (0x53FAC0).

| # | Load VA | Length | File offset of data | What |
|---|---|---|---|---|
| 0 | 0x3c380020 | 0x1ac214 (1.7 MB) | 0x20 | DROM: `.rodata` — strings, vtables/typeinfo, i18n packs, bitmaps, the two built-in wallpapers, the built-in fonts |
| 1 | 0x3fc9b600 | 0x3ddc | 0x1ac23c | DRAM `.data` (copied to RAM by the bootloader) |
| 2 | 0x42000020 | 0x3764e8 (3.5 MB) | 0x1b0020 | IROM: all the app's C++ code, flash-mapped through the cache MMU |
| 3 | 0x3fc9f3dc | 0x5ee8 | 0x526510 | DRAM `.data`, second part |
| 4 | 0x40378000 | 0x135f0 (79 KB) | 0x52c400 | IRAM: ESP-IDF's ISRs, flash driver, ROM patches (copied) |
| 5–7 | 0x50000000, 0x500002f0, 0x600fe000 | 8 / 36 / 96 B | 0x53f9f8… | RTC data/RTC fast memory |

Padding follows the last segment; the ESP checksum byte sits at file 0x53fa9f and the SHA-256 at
0x53faa0..0x53fabf (`tools/stockdev.py` recomputes both; `docs/xic.md`). The bootloader verifies the
SHA-256 at every boot, so **every byte change needs the re-checksum step** — `stockdev.py`'s
`refresh_image()` is that step and is importable.

**Map of DROM** (from a 16 KB-window entropy/text scan, then confirmed by content):

| VA range | Content |
|---|---|
| 0x3c380020–0x3c3b4000 | the app's string table: paths, format strings, Lua names, log tags (208 KB) |
| 0x3c3b4000–0x3c3fc000 | vtables, typeinfo (mangled names inline), constant structs, the two i18n packs at 0x3c3f7a08 and 0x3c3f7d54 (§6), a 100 KB high-entropy block at 0x3c3c0000–0x3c3d4000 (compressed; not identified — hyphenation patterns or zlib tables *(guess)*) |
| 0x3c3fc020–0x3c4402c4 | ~278 KB of 1-bpp bitmap data: dither tiles, icons and glyph-like shapes (rendered as strips in the scratch dir); the table that names them was not found (§5) |
| 0x3c4402c4–0x3c4579dc | embedded `.xic` **"domestic"** wallpaper, 480×800, 4 levels, 2 planes (96,024 B) |
| 0x3c4579e0–0x3c46f0f8 | embedded `.xic` **"overseas"** wallpaper, same format — the "Get Started" sleep screen |
| 0x3c46f0f8–0x3c48c000 | the built-in U8g2 fonts (§4) and other compressed data |
| 0x3c48fe90 | a packed table tagged `XTZB` (3-byte records), referenced from 0x421c8230–0x421c8344; not decoded |
| 0x3c490000–0x3c4ac000 | a second, larger multi-language string blob (zh-CN / en / zh-TW / ja) with its offset table at ~0x3c491540 (toasts, dialogs); header form not identified |
| 0x3c4ac000–0x3c4d4000 | high-entropy data (compressed; likely more font/glyph data *(guess)*) |
| 0x3c4d4000–0x3c52c234 | ESP-IDF, newlib, mbedTLS, WiFi/BLE stack rodata (link order puts the libraries last) |

**Free space in the slot.** app0 is 0x7E0000 bytes; the image is 0x53FAC0, so **0x2A0540 bytes
(2.63 MB) after the image are erased flash** (0xFF). Nothing maps them today.

**Position dependence.** The image is not relocatable: `l32r` literal pools hold absolute VAs, vtables
hold absolute function pointers, `call8` targets are PC-relative but the code they reach is fixed.
So a modification is one of: (a) an in-place byte edit (an immediate, a branch, a pointer in a table),
(b) replacing data of the **same length** in place, or (c) new code in the free area. For (c):

- New code must live in the flash-mapped IROM window. ESP-IDF's second-stage bootloader maps one DROM
  and one IROM segment (a segment count limit of two mapped segments in `bootloader_utility.c`'s
  `unpack_load_app`, from memory of the IDF sources — verify against v6.0.1 before relying on it), so
  a *new* segment is not the route; **extending segment 2** is: raise its length field (file 0x1b001c),
  insert the new bytes after its payload (the following segments 3–7 shift; they are read sequentially
  from the file and copied to RAM, so they do not care), re-checksum. The new code is then at VA
  0x42376508 and up, mapped like the rest of IROM (64 KB pages; the bootloader maps the whole segment
  length). The image grows by the insert; 2.6 MB is available.
- The trampoline into it is an in-place edit of an existing `call8`/`callx8` (a `call8` reaches ±512 KB,
  so a far target needs `l32r` + `callx8` — 6 bytes — in place of an existing 6-byte sequence, or a
  hook through a vtable pointer in DROM, which is simply a 4-byte data edit: replace the function
  pointer of a virtual method with the VA of new code that calls the original). The vtable route is the
  cleanest: every page/layer view has a 29-entry vtable in DROM (§2).
- Not implemented here; the pieces exist: `stockdev.py` (checksum/hash), `appdis.py` (disassembly),
  the segment table above.

## 2. The C++ architecture (from typeinfo names)

Names survive only as typeinfo strings (no `_Z` function symbols) plus a few `__PRETTY_FUNCTION__`
strings, e.g. `void XTEink::Runtime::PageRouter::registerPage(PageId, IPagePresenter*, IPageView*,
PageKind)` at DROM 0x3c387cfb. 589 mangled type names demangle to 560 classes; the app's own live
under `XTEink::`. Typeinfo objects sit right before their name string; a class's vtable is found by
scanning for pointers to its typeinfo (vtable[-1]); `survey/vt.py` does this.

**Shell and pages (Model-View-Presenter).** `Runtime::AppShell` (two vtables at 0x3c3b4468 and
0x3c3b4524, 45/44 entries), `Runtime::PageRouter`, `Runtime::IPagePresenter` / `IPageView`,
`Runtime::PagePresenterBase<DisplayData>`, `Runtime::LayerPresenterBase<DisplayData, OpenData,
CloseData>`, `Runtime::View::PageViewBase<DisplayData>` / `ITypedPageView<>`. Every screen is a
`XTEink::Views::XxxPagePresenter` + `XxxPageView` pair over a `XTEink::Views::XxxDisplayData` struct;
every overlay is `XxxLayerPresenter` + `XxxLayerView`. 65 views / 64 presenters:

- Pages: AboutDevice, AccountSettings, Apps, BluetoothSettings, BootPassword, BootPasswordSettings,
  ButtonSettings, Certification, CloudSync, Comic, DisplaySettings, FactoryTest, Files,
  FirstBootLanguage, FirstBootRegion, **Home**, ImageView, Images, Language, PowerOffCharging,
  PreloadList, Provisioning, Reading, ReadingAdvanced, ReadingToc, ScriptHost, **Settings**,
  Statistics, Subscriptions, SystemFontSettings, TimeZone, TimeZoneSelect, UpgradeSettings,
  UsbTransfer, Wallpapers, WifiSettings.
- Layers: AppInstall, BookActionMenu, BookshelfPreferences, **Brightness** (the pull-down light panel),
  BtDevice, Confirm, DatePicker, ExternalFontManager, FileBookMenu, FilesPreferences, FontFileMenu,
  ImageFlatMenu, Keyboard, **NavMenu**, ReadingAutopage, ReadingFontPicker, ReadingMenu,
  ReadingOptionPicker, ReadingSleepCover, SavedBluetoothDevice, SavedWifiNetwork, SleepStatus,
  SystemFontInstall, SystemFontMaintenance, SystemUpdate, TimePicker, WallpaperTargetMenu,
  WebFileTransfer, WifiNetwork.
- Vtables (29 entries each, same shape): `NavMenuLayerView` 0x3c3e6118 (own methods 0x4216b2ac,
  0x4216bbf0, 0x4216bd50, 0x4216bdd8, 0x4216bfd8, 0x4216c2a8; constructor region 0x4216b5c0–0x4216b930),
  `SettingsPageView` 0x3c3f3f04 (0x4219ed40, 0x4219fcb4, 0x4219fe14, 0x4219fe9c, 0x421a009c,
  0x421a036c), `HomePageView` 0x3c3f0adc (0x421931b0, 0x42193e74, 0x42193fd4, 0x4219405c, 0x4219425c,
  0x4219452c), `BrightnessLayerView` 0x3c3e2324 (0x4215d8b4, 0x4215e91c, 0x4215ea78, 0x4215eafc,
  0x4215ecf0, 0x4215efb0), `ShellView` 0x3c3f7918, `NavMenuLayerPresenter` 0x3c3dc860 (18 entries).
  Entries at 0x4234xxxx are the template base's small thunks shared by all views.

**Widgets** (`XTEink::Widgets::`, base `Runtime::View::WidgetBase`, 22-entry vtables): ActionWidget
(0x3c3b4a38), BlockWidget, DisplayWidget, DragHandleWidget, ListItemWidget (0x3c3e2508, 188 bytes per
instance), ListWidget (0x3c3e2568), NativeWidget, WheelColumnWidget. The nav menu view embeds an array
of **8 ListItemWidgets** (loop of 8 × 188 bytes at 0x4216b76d); five are visible (Read, All Files, USB
Mode, Cloud Sync, Settings) — the i18n pack also carries "Extensions", "Lua Apps", "Statistics",
"Subscriptions" labels for the same menu (§6), so the other entries are feature-gated in code.

**Display and panel.** `XTEink::Display` (64-entry vtable at 0x3c4a104c — the compositor/driver front),
`EPD_GFX` (Adafruit-GFX-style drawing surface; the string `U8G2_FOR_ADAFRUIT_GFX` is present),
`EPDBase`, `EPD4GrayDriverBase`, `EPDPanelInterface`, `SPI3W`, `SPI3W_DC`, `UC8279_800x480` (vtable
0x3c4a1564, 64 entries), `UC8279_800x480_lut`, `UC8179_800x480(_lut)`, `UC8179Base`, `ZHX_UC8279Base`,
`SSD1677_800x480`, `PackedPlane::{IPackedPlaneSource, MemoryPackedPlaneSource}`,
`ImageCodec::{IByteSource, ISeekableByteSource, IPackedPlaneSink, SeekableNativePlaneSource}`,
`Domain::Images::Cache::{XicStreamWriter, NativeXicStreamWriterPort}` (the `.xic` writer, `docs/xic.md`).

**Fonts.** `Domain::Font::{U8g2Face, IU8g2GlyphBackend, RuntimeFontResolver, SystemFontRuntime,
SystemFontWorkspaceOwner, FontPrewarmService, MissingGlyphFace, PsaFontSha256, IFontSha256}`,
`Domain::Font::Xtf::{ExternalXtfFace, LayeredSystemXtfSource, LayeredSystemXtfAssetHandle,
IXtfGlyphCache, IXfp3ReadSource, IXtfStructureProbeSource, IXtfStructureValidationObserver}`,
`Theming::{ThemeXtfFace, ViewEnvironmentU8g2Backend}`, `XTEink::GlyphCache`; reading-side
`Domain::Reading::Font::{XtfFontProvider, BinFontProvider, XtfFontBackendAdapter, BinFontBackendAdapter,
ReadingShapedAdvanceLineMeasurer}`, `Domain::Reading::Render::BitmapGlyphRenderer`,
`Domain::Reading::{U8g2LineMeasurer, BitmapFontLineMeasurer, SnapshotLineMeasurer}`.

**Input.** `TouchBase`, `GT911Driver`, `Runtime::Input::InputAggregator`, `Runtime::IInputRouter`,
`Runtime::Input::IHidInputReceiver`, BLE HID adapters (`Domain::Bluetooth::{KeyboardAdapter,
GamepadAdapter, PagerAdapter, HanlinAdapter, Free2Adapter, IneMouseAdapter, IineGamepadAdapter}`).
Tasks: `xteink_ui` (created at 0x420028b0's function), `xteink_input`, `epd_flush` (created near
0x421c86c4), `xteink_sdmon`, `xteink_nvs`, `pwr_monitor` (`tools/tcbwalk.py`).

**Services.** `Domain::{Light::LightService, Power::PowerService, Network::NetworkService,
Bluetooth::BluetoothService, Usb::UsbMscService, Library::LibraryRepository, Images::ImagesRepository,
Apps::AppDekStore, Cloud::…, Storage::…, Status::SystemStatusStore, Radio::RadioResourceManager}`,
`Runtime::Tasks::{BackgroundIoExecutor, SystemCriticalExecutor}`, `Runtime::Diag::DeveloperToolsService`.

**A scripting host exists.** Lua 5.5 is linked (`Lua 5.5`, `index.lua`, `lockscreen.lua`, `Lua Apps`,
`/sdcard/XTApps/<app>/{manifest.json, index.lua, app.xtapp, assets.xtab}`, classes
`Script::{XtappAssetSource, XtabAssetSource, IAssetSource, IAppFontHandleResolver}`,
`Runtime::Script::AppFontSession`, `Views::ScriptHostPageView`, `Views::AppsPageView`). The Lua API
surface (string table 0x3c3935ce–0x3c3939a0): lifecycle `on_load/on_enter/on_tick/on_input/on_draw/
on_leave/on_unload`, drawing `clear`, `stroke`, `color`, `invert`, `circle`, `layer`, `draw_with`,
`layers`, offscreen `lua.offscreen`, refresh control `full_refresh`, `defer_auto_full`, `invalidate`,
`request_refresh`, `flush_once`, `set_tick_rate`, gestures `double_tap`, `swipe_up/down/left/right`,
`gesture`, status `XTEink.SystemStatusProxy.{status,clock,power,radio,network,bluetooth}`, `i18n`
(`lang/%s.tsv`), `set_as_lockscreen_app`, `quit`. Manifest keys: `app_id, display_name, author,
description, app_icon_s, app_icon_l, splash, display, permissions (sys.battery, sys.status,
lockscreen), interval_sec, preload_assets`. Packages are wrapped (`xteink.xtapp.wrap.%s`,
`xteink.xtapp.appdek.wrap.v2`, `dek_wrap_alg`, `dek_wrap_key_id`, `dek_wrap_ephemeral_pub`,
`wrapped_dek_pack`, `hmac_code/assets/data/fonts`, `iv_*`): **whether an unwrapped
`manifest.json` + `index.lua` directory is accepted is not established** — the loader has both the
`%s/%s/app.xtapp` path (referenced at 0x42002cd8, 0x42106150, 0x42187f2c) and `index.lua`; only a
run in the emulator (a plain app directory on the card) or a read of the loader will tell. If it is,
"a new screen" becomes a Lua file, which changes the whole cost picture (§10).

## 3. The drawing path

**Text.** Two engines, selected by the card: the built-in path is **U8g2** glyph tables drawn through
`U8G2_FOR_ADAFRUIT_GFX` onto `EPD_GFX` (`Domain::Font::U8g2Face`, `Theming::ViewEnvironmentU8g2Backend`
vtable 0x3c3df354: 4 methods 0x4214c838, 0x4214ca30, 0x4214c8c0); the external path is the card's
`.xtf` fixed-cell bitmaps (`Xtf::ExternalXtfFace`, `Theming::ThemeXtfFace`) through the same
`BitmapGlyphRenderer`. No FreeType, no HarfBuzz, no stb_truetype strings exist: **the app never
rasterises outlines**; every glyph is a pre-rendered 1-bpp bitmap. Shaping is a per-glyph advance
walk (`ReadingShapedAdvanceLineMeasurer`, `U8g2LineMeasurer`), hyphenation is Liang patterns
(`LiangHyphenator`).

**Primitives.** `EPD_GFX` is an Adafruit-GFX descendant (rect/line/pixel/bitmap family) *(guess from
the class name and the U8G2 adapter, not from decoded method names)*; widgets call it through the
`Display` object. Images: `.xic` (native 1-bpp / 2-plane container, `docs/xic.md`), `.xtg` and `.xth`
(listed next to `.xic` in the supported-extension strings; not decoded), plus decoders for PNG
(`No IHDR chunk is found`, `Incorrect PNG signature`), JPEG (`baseline-jpeg`, `progressive-jpeg-sof2`),
GIF and WebP (extension lists `.bmp,.jpg,.jpeg,.png,.gif,.webp,.xtg,.xic`).

**Framebuffers.** Allocation tags name them: `epd.frame_slot0.plane0` … `epd.frame_slot3.plane0`
(four full-frame slots), `epd.layer_base.plane1`, `epd.gray.scratch`; grey waveform banks
`UC8279_gray_full`, `UC8279_gray_aa`, `UC8179_gray_aa`, and `_displayGrayscaleFull`. The frame is
portrait 480×800 at 1 bpp (60-byte rows) — exactly the `.xic` capture layout; `Display` composes pages
and layers (the light panel is drawn translucent over the base frame; Screen Capture records the base
frame, `docs/xic.md`).

**Refresh scheduling** (observed in the emulator, `docs/log.md` sessions 4–5, and the strings): the UI
task marks a page dirty; the `epd_flush` task sends DTM1 (old plane, pre-sent right after the previous
refresh), then on the next repaint DTM2 (new plane) + DRF. Partial updates use PTIN/PTL windows;
"Full Refresh" is a button in the light panel and a Lua verb (`full_refresh`, `defer_auto_full` — an
auto-full-after-N-partials policy exists *(guess from the name)*); `Display refresh %lu` is a Display
Settings row (`enDispMode` NVS key). `XTEink::EPD::{RefreshResult, RefreshFailureReason,
RefreshFailureStage, TrackedFlushLifecycleEvent}` are the flush-pipeline events. A UX change that
only touches what a page draws therefore lives entirely inside the view's paint method; a change to
*when/how* it refreshes lives in `Display` (vtable 0x3c4a104c) or the presenter's dirty-marking.

## 4. Fonts

**Built-in.** Two U8g2 fonts in DROM, registered under the names `u8g2:20:v2` and `u8g2:28:v2`
(font registry code at 0x421058e0–0x42105940):

| Name | VA (file) | Glyphs | Max glyph | Ascent/descent |
|---|---|---|---|---|
| `u8g2:20:v2` | 0x3c46fa24 (0xefa24) | 51 | 38×31 | 14 / −4 |
| `u8g2:28:v2` | 0x3c47c298 (0xfc298) | 41 | 47×35 | 17 / −5 |

Both are standard U8g2 font blobs (23-byte header, RLE glyphs; `bdfconv` output) with only 41–51
glyphs — a fallback set (digits, punctuation, a few letters) for the status bar when no card font is
loaded ("External font failed to load. Using built-in font." toast). Where the full built-in CJK/Latin
set comes from (the 0x3c46f0f8–0x3c48c000 and 0x3c4ac000–0x3c4d4000 compressed blocks *(guess)*) was
not pinned.

**External (`mode=external`, the device's current state).** `XTData/system_fonts/selection.config`:
`format_version=1`, `mode=external`, `font_id=misans-demibold`, `small_asset_id=<sha256 of
system_small.xtf>`, `body_asset_id=<sha256 of system_medium.xtf>`. The package
`XTCache/system_font_downloads/misans-demibold-r1.xtfont` is a **ZIP** (`PK`) holding `manifest.json`
and `assets/`; installation unzips into `XTData/system_fonts/<font_id>/`. `manifest.json` (format
`xteink-system-font-package`, `format_version` 3, tool `XTFont 0.5.0`) declares `roles.system_small`
(cell 20×20, `advance_y` 18) and `roles.system_body` (cell 24×24, `advance_y` 22), each with `font`
(the `.xtf`: `bpp` 1, `glyph_count` 43101, `sha256`, `size`) and `hot` (a `.hot.xtfp` glyph-cache
tier file, `format_version` 3.3, three tiers), `required_features` `["xtf-v2","system-family-1bpp",
"xfp3-hot-v3.3"]`, and a 320×96 `.xic` preview. The `.base.xtfp` files are **not** in the manifest and
carry later timestamps: the device generates them.

**`.xtf` v2 layout** (both card files, 64-byte header, little-endian):

| Offset | Field | small / medium |
|---|---|---|
| 0 | magic `XTF0` | |
| 4 | u16 version | 2 |
| 6 | u16 | 0x140 (header + first 16 range entries *(guess)*) |
| 8 | u16 | 3 |
| 0xa | u8 cell_w, u8 cell_h | 20,20 / 24,24 |
| 0xc | u8 advance_y, u8 | 18,20 / 22,24 |
| 0xe | u8, u8 | 11,2 / 13,2 (baseline-related *(guess)*) |
| 0x10 | u16 line height *(guess)*, i16 descent | 21,−6 / 26,−7 |
| 0x14 | u32 end of range table | 0x1a0 |
| 0x18 | u32 glyph_count | 43101 |
| 0x1c | u32 range table offset | 0x40 |
| 0x24 | u32 glyph records offset | 0x1c00 |
| 0x28 | u32 glyph record size | 0x40 / 0x4c |
| 0x2c | u32 tail section offset (size 0x1c00) | 0x2a1740 / 0x31fb9c |
| 0x30 | 8-byte hash (the same two words appear in the `.xtfp` header) | |
| 0x38 | u32 advance table offset, u32 count | 0x1a40, 95 |

Range table entries (16 bytes): `u32 first_codepoint, u32 count, u32 first_glyph_index, u16 0, u16
count` — (0x20, 95, 0), (0xa0, 292, 95), (0x1c5, 346, 387), … Glyph records at 0x1c00: **4 metric
bytes** (`06 12 02 01` for space in the 20-px font: advance, height, x/y offsets *(guess at the order)*)
followed by the cell bitmap, rows padded to bytes (3 bytes × 20 rows = 60, +4 = 64; 3 × 24 + 4 = 76),
MSB first, 1 = ink. Records are in glyph-index order, so lookup = range table → index → offset. The
`.xtfp` (`XFP3`) cache: header `XFP3, u16 3, u16 3, u16 0x80, u16 0x20, u16 7|12 (base|hot), u16 0|1,
u32 1|6, u32 xtf_size, 8-byte xtf hash, u32, u32 2, u16 1, u16 2, u8 cell_w, u8 cell_h, u16 record
size, u16 1, 32-byte SHA-256 of the `.xtf` (= the `asset_id`), 32-byte set hash …` — a resident hot
set of pre-selected glyphs.

**Is adding a font data-only? Yes by design, with a converter to write.** The path is Settings →
System Font → a `.xtfont` ZIP in `XTCache/system_font_downloads/` (or the installed directory under
`XTData/system_fonts/<id>/` + `selection.config`). A converted TTF needs: two `.xtf` files rendered at
cell 20 and 24 (FreeType/Pillow can rasterise 1-bpp cells; the header/range/record layout above is
complete enough to write, the 4 metric bytes need one confirmation against a known glyph), a
`manifest.json` with correct SHA-256s and sizes (`PsaFontSha256`/`IFontSha256` verify them), and
either a `.hot.xtfp` (format only partly known) or a test of whether the app accepts a manifest without
`hot` — `required_features` suggests the hot cache is optional per package *(guess)*. The emulator is
the test bench: put the package on the card image, boot, open System Font, screenshot. Risk is nil
(the card only).

## 5. Images and wallpapers

- **Sleep ("Get Started") and power-off wallpapers** are the two embedded `.xic` files (§1), selected
  by region: NVS `hw_calib/region` 2 = overseas → 0x3c4579e0; the pair is registered in a DROM table
  at 0x3c3b78c8 (`{begin, end, name}` × 2, names `overseas` 0x3c388bc9 / `domestic` 0x3c388bd2,
  referenced from code at 0x42002ff0–0x42003014). The decoded overseas image (scratch
  `wall_overseas.png`) is the device's "X4 Pro / Get Started / Tap to Go Home" screen. **Data-only
  swap:** encode a 480×800 image as a 2-plane `.xic` of exactly 96,024 bytes (`tools/xic2png.py
  --encode` writes 1-plane files today; the 2-plane grey layout is the documented guess in
  `docs/xic.md`) and overwrite bytes at file 0xd79e0 (overseas) / 0xc02c4 (domestic), re-checksum.
- **Even cheaper, no image edit at all:** the app has an Images page and a Wallpapers page with
  "Set as Lockscreen Wallpaper" / "Set as Power-off Wallpaper" / "Set as both wallpapers" actions,
  persisted in NVS `wallpMode`, `lockscrWallp`, `shutWallp` (code at 0x421c8360–0x421c8410); it scans
  `/sdcard/wallpapers` and `/sdcard/picture` and decodes bmp/jpg/png/gif/webp/xtg/xic. So a custom sleep
  screen is: put a 480×800 image in `wallpapers/` on the card, select it on the device. To verify in the
  emulator: add the file to the card image, boot, Images → the file → Set as Lockscreen, sleep.
- **Splash:** `/sdcard/XTCache/app_splash` (string at DROM 0x3c3d9e6c) is a per-app splash cache
  for Lua apps (`splash` manifest key), not the boot splash. No boot-logo bitmap was identified; the
  boot shows Home directly after the preflight.
- **Icons:** the ~278 KB 1-bpp region at 0x3c3fc020 holds the UI's icons and dither tiles. Its index
  (a `{ptr, w, h}` table) was not found by pointer scanning from any segment, so the images are reached
  through code-relative offsets or a struct not recognised; replacing an icon is a same-size data edit
  once its offset is known (a Ghidra pass, §9, or a visual search in the rendered strips).
- **Covers / page caches:** `/sdcard/XTCache/image`, `/sdcard/XTCache/reading/books`,
  `home-startup-archive.xhsa` (`XHSA`, 400 bytes, a Home startup plan: header `XHSA, u16 1, u16 0x40,
  u32 400, u32 7, 1, 1, 32-byte hash, …`, referenced at 0x42003194) — caches, regenerated.

## 6. Strings and i18n

UI labels are **not** referenced by pointer from code. They live in string packs with this layout
(found by the header pattern, both in DROM):

```
u16 group_count; u16 lang_count = 4; u8 lang_ids[4] = {0,1,2,3};  // zh-CN, en, zh-TW, ja
u16 blob_size; u16 offsets[group_count][4]; char blob[blob_size];   // offset 0 = "" (missing)
```

| Pack header VA (file) | Groups | Blob | Content |
|---|---|---|---|
| 0x3c3f7a08 (0x77a08) | 18 | 0x3c3f7aa2, 689 B | count formats (`%d books`, `%d/%d`) |
| 0x3c3f7d54 (0x77d54) | 234 | 0x3c3f84ae, 13,528 B | every menu/settings/panel label |

Group index = label id: Bookshelf 0, Read 6, Extensions 8, Lua Apps 9, All Files 10, USB Mode 11,
Settings 13, Bluetooth 50, Developer 64, Memory 65, Boost 66, Full Refresh 67, Light 68, Screen
Capture 70, Sleep Lock 71, Language 91, Cloud Sync 137, Upgrade 168, About Device 169, System Font 192
(`survey/i18n_tables.txt` lists all). A second, larger pack for toasts and dialogs sits at
~0x3c491540 (its u16 offsets, ending near 0x3c4916f0) with the blob from ~0x3c4916fa ("Region set
successfully") to ~0x3c4ac000 (same four languages, e.g. "No usable SD card was detected…"); its
header differs (offsets reach 0xf0a5) and was not parsed. Languages: zh-CN, en, zh-TW, ja (NVS `language`; `fbLangDone`).

**Changing a label is a data patch.** Same length or shorter: overwrite in the blob (NUL-pad).
Longer: the blob is followed by ~1 KB of padding before the bitmaps at 0x3c3fc020 (blob end 0x3c3fb986)
— append the new text there and point the group's u16 at it (offsets are relative to the blob start and
must stay < 65,536; the header's `blob_size` should grow accordingly *(guess: unknown whether the
reader bounds-checks against it)*). Then re-checksum. No code change. The pack is read through a
pointer that is neither in DROM nor in the two DRAM segments (no reference to the header, the offset
array or the blob exists in any segment), so the accessor computes it — irrelevant for editing, but it
means "add a fifth language" is code work.

## 7. Layout constants: where the numbers are

Checked on the two visible layouts asked for.

- **Nav menu (five rows).** `NavMenuLayerView`'s constructor (0x4216b5c0–0x4216b930) builds 8
  `ListItemWidget`s of 188 bytes each in the view object and sets flags; the immediates in it (88, 104,
  172, 188, 204, 0xb00, 0xc00) are **object offsets and sizes, not pixels**. No pixel constant (row
  height, x/y, spacing) is an immediate in the view; the geometry is produced by the widget layout
  code (`ListWidget` 0x4215f2d8–0x42160460, `ListItemWidget` 0x4215f4c8–0x42160430) from the display
  data and font metrics, where the candidate `movi` values 72, 82, 92, 120, 140, 152 are again object
  offsets on inspection.
- **Settings list rows.** `SettingsPageView` (0x4219ed40–0x421a0400) shows the same pattern; its
  candidate immediates 60, 76, 196, 202, 216 were not confirmed as pixels.

Conclusion: the stock computes layout from the font's cell/advance (`.xtf` header fields `advance_y`
18/22, the `manifest.json` `cell_height`) and a small set of theme constants held in structs, not from
literal pixel immediates in the views. Two consequences: (1) **changing the card font's `advance_y`
and cell size changes the rhythm of every list** — a data-only lever on spacing; (2) a pixel-level
layout tweak needs the constant's home found with cross-references (§9) and is then a 2–3 byte `movi`
edit or a 2-byte struct field edit. Difficulty: medium for one constant once a decompiler is up; the
verification loop is cheap (patch → `stockdev.refresh_image` → boot → `x4emu screenshot --diff`).

## 8. Risks and the safe workflow

- **Boot-preflight** (`I (%lu) %s: source=%u validation=%u gate=%u configured=%ums effective=%ums
  hold=%ums decision=%u reason=%u auth=%u`, tag `boot-preflight`): the app's own cold-boot gate — power
  held, `auth` = startup-password state — not an image check. The image checks are the bootloader's
  (checksum + SHA-256, always) and OTA state.
- **OTA / otadata.** `otadata` entry 0 = `seq=1 state=2 (ESP_OTA_IMG_VALID)`; the `esp_ota_ops`
  rollback strings and `ota_trial`/`ota_restart_rollback` names show the app uses rollback
  (`esp_ota_mark_app_valid_cancel_rollback`). Writing a modified app into **app0 with otadata
  untouched** boots it unconditionally (state VALID: no pending-verify, no automatic rollback) — a
  crashing patch is a boot loop until the slot is rewritten. The safer device route, when the time
  comes, is **app1 + a new otadata entry** (`seq=2, state=NEW`): the bootloader boots it as
  PENDING_VERIFY and rolls back to app0 if it does not confirm itself — the stock in app0 stays the
  safety net. `tools/device.py` has no such command yet; it must print its plan and re-read like the
  others. Anti-rollback (`secure_version`) is 0 and eFuses are never touched.
- **Bricking surface:** anything at 0x0–0x10000 (bootloader, partition table, otadata) except a
  byte-exact restore; the SHA-256 not recomputed (bootloader refuses → deep sleep loop on the device,
  `docs/xic.md`); a DRAM segment grown past its RAM (§1: only extend IROM); NVS writes the stock makes on
  first settings change (tests boot copies).
- **Workflow:** (1) emulator only, from a scratch copy of the dump with `net_en 0`; (2) every change
  through a `stockdev.py`-style tool that verifies the bytes it expects, writes, recomputes checksum +
  SHA-256, re-parses (`esptool image-info` as the second opinion); (3) `x4emu screenshot --diff` against
  `tests/golden/stock-*.png` and `tests/data/stock-home-panel.png` to prove that only the intended
  pixels moved; (4) the developer-patched `.xic` capture path for pixel-exact records; (5) device only
  through a guarded tool, after a double backup, into the *other* slot with rollback armed.

## 9. Tooling

`tools/appdis.py` (raw objdump windows) plus the Python scans used here got: segments, strings, class
list, vtables, the i18n packs, the wallpapers, the fonts. It could **not** cheaply answer "which
function draws the Settings row and where does its y come from": that needs cross-references from
literal pools to functions, function boundaries, and ideally a decompiler.

On this Mac nothing is installed: no Java, no Ghidra, no rizin/radare2, no capstone (checked `PATH`,
`/Applications`, `brew list`, `/usr/libexec/java_home`). What it would take (not done, per the brief):

- **Ghidra** (`brew install --cask temurin@21 ghidra`, ~1.2 GB): recent Ghidra (11.x) ships an
  **Xtensa** processor module with decompiler support (added in the 11.1 line as far as I recall —
  check the release notes; the older community `ghidra-xtensa` module is the fallback). Load the three main segments as raw
  binary blocks at their VAs (a 20-line Python/`headless` script from the table in §1, or the
  community `esp32-image` loader), import `images/rom/esp32s3_rev0_rom.nm` as ROM labels, then the
  typeinfo→vtable pass done here becomes a script that names every virtual method. Expected gain:
  large — xrefs to strings and vtables in minutes, decompiled view constructors/paint methods, the
  layout constants of §7 and the icon table of §5 within an hour each.
- **rizin/radare2** (`brew install rizin`): Xtensa disassembly + ESIL, no real decompiler for Xtensa;
  lighter, scriptable; useful for xref lists (`aar`) but the windowed-call analysis is weaker.
- **capstone 5+ in the venv** (`pip install capstone`): makes the literal/xref scripts here robust
  (no false-positive "pointers" inside instruction bytes). Smallest step, good return.

Recommendation: Ghidra + JDK when the owner agrees to the install; capstone in `.venv` regardless.

## 10. First enhancements, ranked by effort (with the evidence pointer)

**Data-only, no image edit (card only, zero risk):**
1. **Custom sleep / power-off wallpaper** — drop a 480×800 image into `/sdcard/wallpapers/`, set it
   from Images → "Set as Lockscreen Wallpaper" (NVS `lockscrWallp`, `shutWallp`, `wallpMode`; §5).
   Verify in the emulator (Images page, sleep = short power press).
2. **A different system font** — a `.xtfont` package (§4): write the `.xtf` converter (format above),
   test on the card image, install via System Font. Changes every label's look and, through
   `advance_y`, the list rhythm (§7). Medium effort, all in Python; needs one confirmation of the 4
   metric bytes and of whether `hot` is required.
3. **A Lua app / lock screen** — *if* the script host accepts unwrapped `XTApps/<app>/{manifest.json,
   index.lua}`: the "Lua Apps" menu entry and `set_as_lockscreen_app` exist (§2). One emulator
   experiment decides it; if it works, "a new screen" costs a Lua file.

**Data-only, in-image edits (re-checksum with `stockdev.refresh_image`, emulator first):**
4. **Relabel anything** — the 234-group pack at 0x3c3f7d54 (§6): rename "USB Mode" to "File Transfer",
   shorten Japanese labels, etc. Bytes only.
5. **Replace the built-in "Get Started" screen** — 96,000 payload bytes at file 0xd79e0 (§5), for the
   case where the card is absent or `wallpMode` is built-in. Needs the 2-plane encoder confirmed on the
   panel model (grey model on).
6. **Icons** — same-size bitmap swap once the icon table is located (Ghidra, §9).

**Small code patches (1–6 bytes in place):**
7. **Show the hidden menu entries** (Extensions / Lua Apps / Statistics / Subscriptions labels exist,
   8 widgets are allocated, 5 shown) — find the gate as `stockdev.py` found the developer stub
   (a predicate or a count immediate near 0x4216b76d), flip it, screenshot.
8. **A layout constant** (row height, margin) — after §9 locates it: a `movi` immediate or a struct
   field; verify with `screenshot --diff`.
9. **Refresh policy** (e.g. full refresh cadence) — the `defer_auto_full`/`enDispMode` path in `Display`.

**Larger (new code in the free 2.6 MB via an extended IROM segment + a vtable hook, §1):**
10. **A new native screen or a restyled Home** — hook a `PageViewBase` vtable entry of `HomePageView`
    (0x3c3f0adc) to new paint code that calls `EPD_GFX`; needs the drawing API's method signatures
    from Ghidra first. Weeks, not days; the Lua route (3) should be tried before this.

### What this map still lacks
The icon table and image-blit call signatures (§5), the second string pack's header (§6), the exact
`.xtf` metric-byte order and the `.hot.xtfp` body (§4), the accessor of the i18n packs, whether the
Lua host takes unsigned apps, the `Display` method names (which of the 64 entries is "flush partial"),
and the layout constants themselves (§7). All are Ghidra-hours, not unknowns.

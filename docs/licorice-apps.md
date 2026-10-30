# Mini apps on XTOS Licorice: the package format, the launch, the Mini Apps entry

XTOS Licorice (261013, and 26102901, the device's since 2026-10-29; `docs/stock-licorice.md`) replaced the
7.x "put a folder in `/sdcard/XTApps`" scheme with an **installer**: you drop a `.xtapp` package in
All Files, tap it, and confirm *Install this package?*. The old folder form is ignored, and `tools/xtapp.py
install` (the classic form) produces *Install failed*. This page is the reverse-engineering of the
new format: what the installer accepts, what the launch decrypts and runs, where the user finds the
Mini Apps page, how it is built, and what is proven versus inferred.

All addresses are the v261013 image (`images/device/stock-app1-v261013.bin`), read with
`tools/ghidra_stock.py --version v261013`. The strings table is `images/ghidra/stock-v261013/`.

**26102901 takes the same packages.** Its key is the same (id `36036010c7bd05af`) at a new address, DROM
`0x3c3a4918`; `tools/xtapp.py` keeps one address per build (`LICORICE_SCALAR_VA`, chosen by
`stockver.release_tag`) and refuses a build it does not know. The DEK1 mark sits at RAM `0x3fca26d4`. The
UI calls the page **"Ink App"** ("Install Ink App", "Leave this Ink App?"); the About → "Model" unlock is
unchanged; `g` lost its three `ttf_` functions. `tests/test_licorice_apps.py` runs on both builds. The
function addresses on this page are 261013's and have not been re-located.
Several functions on the launch path were never made functions by Ghidra's analysis (the open at
0x4210944c, the Lua searcher at 0x420c7eb8, the drawer dispatch at 0x420e78e4); they were read as
raw disassembly (`tools/appdis.py`), where the shipped Xtensa SLEIGH also stops at the ESP32-S3's
`saltu` (objdump prints `excw`).

## Headline

**A mini app this repo builds installs and runs on Licorice.** `tools/xtapp.py pack --format
licorice` (and `install --format licorice`) wraps a content key for the device, packs the app's Lua
files into an encrypted code section and lays out the container; in the emulator the package goes
from All Files to the **"Installed"** toast, the drawer's **Mini Apps** lists it, a tap launches it,
`apps/probe` draws its API dump (Lua 5.5, `g`'s twenty methods), a tap reaches its `on_input`, and
the Home pad's **"Leave app"** returns to the list. `tests/test_licorice_apps.py` reproduces all of it;
`tests/test_xtapp.py` checks the format fast, without the emulator, against a re-implementation of
the firmware's decrypt.

**The encryption is mandatory but not device-bound.** Every package carries a wrapped content key
(DEK); there is no unsigned/plaintext install path. The key the device unwraps with, however, is a
**fixed constant in the firmware image**, identical on every unit — not a per-device secret. So any
firmware reader can wrap a DEK the device will accept, and with it encrypt code the device will run.
The "DRM" keeps a casual user from hand-editing a package; it is forgeable from the image alone.

**The Mini Apps page is hidden.** The side drawer has a "Mini Apps" row that shows only once NVS
`fw/lua_nav` is non-zero, and the firmware sets that when **Settings → About → "Model" is tapped ten
times** in quick succession — an Android-style easter egg.

## The container (`app.xtapp`)

Same `XTAP` magic as the classic form, a different header. `FUN_42108380` reads a 0x80-byte header
(all fields little-endian) into a 0x60-byte container struct (+0x10 = header byte 0):

| offset | size | field | value / rule |
|---|---|---|---|
| 0x00 | 4 | magic | `"XTAP"` (compared against `0x50415458`) |
| 0x04 | 2 | version | `1` |
| 0x06 | 2 | header size | `0x80` |
| 0x08 | 4 | total size | the whole file's length — **must be > 0x7f and ≤ the file** (`FUN_42108380`'s tail gate); every section must end inside it |
| 0x0c | 4 | feature mask | which sections are encrypted and HMAC'd: **0x2 code, 0x4 assets, 0x8 data, 0x10 fonts**; a clear bit means that section is stored in the clear and not checked |
| 0x10 | 4+4 | manifest | offset `0x80`, length `1..0x2000` |
| 0x18 | 4+4 | **code** | the XTCO section below (offset, length) |
| 0x20 | 4+4 | assets | an XTAB section (images and the like, `ctx.assets`) |
| 0x28 | 4+4 | (unused) | copied into the struct, read by nothing found |
| 0x30 | 4+4 | icons | never encrypted: `FUN_4210a134` reads `app_icon_l` / `app_icon_s` out of this XTAB section for the Mini Apps grid without the DEK |
| 0x38 | 4+4 | data | an XTDT section (`ctx.data`) |
| 0x40 | 4+4 | fonts | the app's TTF fonts (`ctx.fonts`) |
| 0x80 | len | manifest | plain UTF-8 JSON |

A length-0 section is absent. Every present section except 0x28 must start above 0x7f and end within
the total size (`FUN_4224d1a8`, called five times by the installer). `tools/xtapp.py` builds the header,
the manifest and the code section; the others stay 0.

The installer (`FUN_42108f68`, reached from `FUN_42109210`, the *Install this package?* action):
opens the container (`FUN_42108380`); bounds-checks the section descriptors (`FUN_4224d1a8`); parses
and validates the manifest (`FUN_42108b20` → `FUN_42108648`, `FUN_42108590`); **unwraps the DEK
(`FUN_4210a908`) — mandatory, the install fails without it**; verifies each enabled section's HMAC
(`FUN_42107f3c`, bits 0x2/0x4/0x8/0x10, the manifest's `hmac_*`); refuses a package whose
`required_features` has bit 0 set but which has no fonts section (inferred); and copies the file
**verbatim** to `/sdcard/XTApps/<app_id>/app.xtapp` (verified byte for byte). Nothing is decrypted
at install time: the code stays encrypted at rest and is decrypted in RAM at every launch.

## The manifest

Plain JSON at 0x80. `FUN_42108648` extracts into a 0x11c-byte struct, `FUN_42108590` validates:

- **`app_id`** — the `/sdcard/XTApps/<app_id>` directory name. `FUN_42108590` walks it and rejects a
  non-ASCII-letter byte (`(c & 0xdf) + 0xbf > 0x19`), so it must be **all letters**: `probe` installs,
  `gull-rock` (hyphen) does not. *(Inferred from the decompile, which ends in an undecoded tail, and
  confirmed only for the all-letter case; `tools/xtapp.py` rejects a non-letter app_id.)*
- **`display_name`** — the toast and the Mini Apps card text ("API Probe").
- **`version`** — `FUN_42108590` parses `"%d.%d"` and accepts `1.0`, `0.8` or `0.7` (absent ⇒ 0.8).
- **`entry`** — the script the Lua host runs, default `index.lua`, a 32-byte field (≤ 31 bytes);
  looked up by name in the code section.
- **`permissions`** — `FUN_42108648` folds them to a bitmask and sets a reject bit on an unknown one:
  `sys.battery` 1, `sys.status` 2, `net.http` 4, `net.https` 0x20, `storage.app` 8,
  `storage.user_file` 0x10, `bluetooth` 0x40, `device.status` 0x80, `device.keep_awake` 0x100.
- The **DEK-wrap fields** (below), and per section an **`iv_*`** (16 bytes, hex) and **`hmac_*`**
  (32 bytes, hex): `iv_code` / `hmac_code`, `iv_assets` / `hmac_assets`, `iv_data` / `hmac_data`,
  `iv_fonts` / `hmac_fonts` (`FUN_42108b20` passes `FUN_42108648` a 0x34c-byte struct: alg +0x000,
  key id +0x030, ephemeral +0x048, pack +0x0d4, `iv_code` +0x10c, `hmac_code` +0x154, then assets,
  data, fonts at +0x19c..+0x304, 0x48 bytes each).

Other keys the extractor reads: `display.orientation`, `lockscreen` (`enabled`, `entry`,
`interval_sec`; on 26102901 it is accepted with an entry `lockscreen.lua` packed beside `index.lua`, and
`ctx.system.set_as_lockscreen_app()` switches the app to it: `docs/stock-licorice.md`, "The Ink App lock
screen"), `splash`, `ttf_reserve_kb`, `required_features`, `optional_features`,
`preload_assets` (asset names hashed FNV-1a at open), `app_icon_s/l`. A `dek_wrap_alg` of
`xteink:store-seed-binding:v1` (the app store's form) is refused as `not_supported` by this build.

## The DEK wrap (the crypto gate)

The manifest carries a content key (DEK) wrapped for the device. `FUN_4210a908` unwraps it — at
install and again at every launch; the four fields are **hex** strings (`FUN_4224d294` is a strict hex
decoder: exactly 2n hex digits then the NUL):

| field | bytes | meaning |
|---|---|---|
| `dek_wrap_alg` | | must equal `"P256-ECDH-HKDF-SHA256-AESKW"` |
| `dek_wrap_key_id` | | must equal `"36036010c7bd05af"` |
| `dek_wrap_ephemeral_pub` | 65 | an uncompressed P-256 point `04‖X‖Y` (the sender's ephemeral public key) |
| `wrapped_dek_pack` | 24 | the AES-KW-wrapped 16-byte DEK |

The scheme (fully reversed from `FUN_4210a908`):

1. **ECDH.** The device scalar `d` is a **fixed 32-byte constant at DROM `0x3c32c674`** (26102901:
   `0x3c3a4918`; loaded by `FUN_421d7118`, read big-endian — mbedtls convention, confirmed by a live
   install). The key id is the first 8 bytes of `sha256(Q)`, `Q` uncompressed, which lets
   `licorice_device_pubkey` check that the scalar it read is the key. It computes
   `shared = (d · E).x`, `E` the ephemeral public key from the manifest. To *build* a package you need
   only `Q = d · G` (the device public key); `tools/xtapp.py` derives `Q` from the firmware image and
   wraps with `shared = (e · Q).x`, `e` a fresh ephemeral — the device recovers the same `shared`.
2. **KEK = HKDF-SHA256** (`FUN_4210a5e0` = HMAC-SHA256: `FUN_4210a4a4`/`FUN_421933cc`/`FUN_4210a394`):
   - PRK = `HMAC(key = "xteink.xtapp.wrap.36036010c7bd05af", msg = shared)`
   - OKM = `HMAC(key = PRK, msg = "xteink.xtapp.appdek.wrap.v2" ‖ 0x01)`  (the `0x01` is HKDF-Expand's T(1))
   - KEK = OKM[0:16]  (AES-128)
3. **DEK = AES-KW-unwrap(KEK, wrapped_dek_pack)** — RFC 3394 (`FUN_4210a754`, AES-ECB block
   `FUN_4210ae98`), with the standard default IV `a6a6a6a6a6a6a6a6` at DROM `0x3c32c66c`. 24 bytes in,
   16-byte DEK out.

A successful unwrap is cached in RAM at `0x3fc9e0f8` (26102901: `0x3fca26d4`; and RTC-fast `0x5000005c`) tagged `"DEK1"` +
the app_id — the oracle `tests/test_licorice_apps.py` reads to prove the wrap verified.

## The code section and the launch

### The open (`FUN_4210944c`)

Tapping a card on the Mini Apps page starts the Lua host app (`user.lua`, below), whose task calls
`FUN_4210944c(path, &err)` at `0x420c6452`. It allocates a 0x106c-byte app context and:

1. re-opens the container (`FUN_42108380`) and the manifest/wrap struct (`FUN_42108b20`);
2. reads the sections into RAM whole (`FUN_42107e14`): code **up to 512 KB (0x80000)** → ctx+0x11c /
   +0x120, assets up to 1 MB (0x100000) → +0x16c / +0x170, data → +0x2c4, fonts → +0x2cc (always); a
   larger code or assets section is **streamed** from the card instead (flag ctx+0x124 / +0x174, the
   file path at ctx+0x175, a copy of the header at ctx+0x238, the IV hex at ctx+0x12c / +0x290);
3. unwraps the DEK again (`FUN_4210a908`, into a stack buffer);
4. in-RAM sections: **`FUN_42107fcc(DEK, buf, len, iv_hex, hmac_hex, feature_mask, bit)`** for code
   (bit 0x2), assets (0x4), data (0x8), fonts (0x10): if the length is 0 or the bit is clear it
   accepts the bytes as they are; otherwise it hex-decodes `hmac_*`, compares it with
   **HMAC-SHA256(DEK, the stored bytes)** (`FUN_4210a5e0`) and then **decrypts in place**
   (`FUN_4210a738` → `FUN_4210ad78`, offset 0). Encrypt-then-MAC: the tag is over the ciphertext;
5. streamed sections: the same HMAC over the file region (`FUN_42107f3c`), then only the 16-byte
   XTCO header and the table are decrypted (CTR at offset 0); each file is decrypted when it is read;
6. parses the tables (`FUN_42108c44` for in-RAM sections, inline for streamed ones), hashes
   `preload_assets`, and wipes the DEK (`FUN_4224d240`). Any failure frees the context
   (`FUN_421093c4`) and returns the error string (default `io_error`).

### The cipher: AES-128-CTR, key = the DEK (`FUN_4210ad78`)

`FUN_4210ad78(key, iv, buf, len, offset64)` is `mbedtls_aes_crypt_ctr` with a seek:
`mbedtls_aes_setkey_enc(key, 128)` (`FUN_421e6b64`), the 16-byte counter = the IV plus
`offset >> 4` added **big-endian over all 128 bits** (`FUN_4224d258`), `offset & 15` keystream bytes
skipped (one ECB block `FUN_421e6c10`, counter + 1), then `FUN_421e7158` = `mbedtls_aes_crypt_ctr(ctx,
len, &nc_off, counter, stream, buf, buf)` — in place. So a whole section is **one CTR stream with the
DEK as the key and `iv_*` as the initial counter block**, and any byte range can be decrypted on its own
at its own offset (what `FUN_42109d7c` does for a streamed file). There is no per-section key
derivation, no tag in the stream and no padding. The AES is ESP-IDF's hardware driver (`esp_aes`:
the setkey `FUN_421e6b64` only copies the key, `FUN_421e7158` drives the peripheral), which
Espressif's QEMU models. `tools/xtapp.py`'s `licorice_ctr()` is this; `tests/test_xtapp.py`
checks it against a block-by-block re-implementation of the decompile, every offset included.

### The plaintext: an XTCO table of Lua files

The decrypted code section (`FUN_42108c44`, the in-RAM parse; the streamed parse at 0x421098d3 has
the same rules) is a small archive:

| offset | size | field |
|---|---|---|
| 0x00 | 4 | `"XTCO"` (`0x4f435458`) |
| 0x04 | 2 | version `1` |
| 0x06 | 2 | 0 (not read) |
| 0x08 | 4 | file count, **1..32** |
| 0x0c | 4 | table end — **must equal 16 + 104 × count**, and fit the section |
| 0x10 | 104 × count | entries: name (96 bytes, NUL-padded; empty = refused), u32 offset **counted from the table end**, u32 length; `table end + offset + length` must fit the section |
| table end | | the file bytes |

The context keeps the entries at ctx+0x2d4 (0x6c bytes each: name, +0x64 offset, +0x68 length), the
count at ctx+0x1054. No compression anywhere.

### Running the entry: Lua source (or bytecode)

`FUN_420c5fb0(host, entry)` looks the manifest's `entry` up with **`FUN_42109d7c(ctx, name, &buf,
&len)`** — an in-RAM section returns a pointer into the decrypted copy (`ctx+0x11c + [ctx+0x11c+0xc]
+ offset`); a streamed one reads the file (≤ 1 MB, else `out_of_memory`) and decrypts it at its
section offset — and hands it to **`luaL_loadbufferx(L, buf, len, name, NULL)`** (`FUN_420d1118`, mode
NULL = text or binary), then calls it. A missing entry is `"entry missing"`. So the code section
holds **plain Lua 5.5 source**; precompiled bytecode would load too (not tried). `require "a.b"` is
the searcher at `0x420c7eb8`: dots become `/`, `.lua` is appended, the file comes from the same table
(`"module '%s' not found"` otherwise); `@public/ui.lua`, a component library, is built into the
firmware. The other sections are the API's: assets (`ctx.assets`, an `XTAB` table: 64-byte header,
40-byte entries name[24] / offset / length / u16 / u16 / u8, ≤ 0x800 files, read by `FUN_42109f8c`),
data (`ctx.data`, an `XTDT` table: 16-byte header, 80-byte entries name[64] / offset / length, ≤ 128
files) and fonts — read from the disassembly, not exercised (`tools/xtapp.py` builds only the code).

What the probe shows of Licorice's Lua API (page 1 and 2, `tests/golden/licorice-app-running.png`,
`-page2.png`): `g` has `arc circle clear fill_pattern image layer line measure measure_text
pattern_rect polygon polyline rect round_rect size text text_box ttf_measure ttf_text ttf_text_box`
(the luaL_Reg at DROM 0x3c320d38; 26102901 drops the three `ttf_` functions, the rest of page 1 is the
same, `tests/golden/licorice-v26102901-app-running.png`), `g:size()` → 480, 800; `ctx` has `api assets capabilities data
display fonts gc i18n input layers lock longtask perf screen state system ui watchdog` and the
functions `invalidate quit request_refresh save set_tick_rate` (0x3c320d08). The 7.x names exist
under the same spelling (`g:clear` and `g:text` drew the probe's pages; the others' arguments were
not compared with 7.x).

### When it fails: the toasts

The host reports an open or start failure with `FUN_420c4f10`, a "Mini Apps" toast, then returns to
the list: **"Cannot run yet"** for an empty error, `io_error`, `invalid_argument` or `lua queue
full`; "Not supported on this device" for `not_supported`; "Not enough memory to run" for the
out-of-memory family; "App error, closed" for anything else (a Lua error, `entry missing`). Seen in
the emulator: a manifest-only package (`FUN_42108c44` finds no code) and a package whose `iv_code`
differs in one bit (the decrypt yields no `XTCO`) both say **"Cannot run yet"**; a package with one
ciphertext byte flipped says **"Install failed"** (the installer's HMAC). The Mini Apps tap handler
`FUN_42049a04` has its own "Cannot run yet" branch, but its gate `FUN_42108580` → `FUN_4224d28c` is a
stub returning 1, and "Launch failed" if starting the host app fails.

## Where the user finds Mini Apps

- **The drawer row.** The side drawer's items are a table at DROM **`0x3c32c58c`**, 12 bytes each
  (i18n label, kind, flags): Desktop (kind 8), Bookshelf 0, All files 1, Cloud 2, **Mini Apps 3**,
  Link (Beta) 4, USB mode 5, Settings 6. The builder `FUN_420fac30` shows an item when its flag byte
  +9 is set — every visible item has it, **Mini Apps and Link do not** — or, for kind 3, when
  `FUN_421ab308("fw", "lua_nav", 0)` (an NVS u8) is non-zero. The drawer's dispatch (0x420e78e4) sends
  kind 3 to `FUN_420e5810("sys.apps")`.
- **The unlock.** `FUN_4203f650` is the callback of Settings → About's second row, "Model"
  (`dev_model`, registered at 0x4203f9d4 with `FUN_4202c518`). Each tap within **2000 ms** of the
  previous one counts (counter `0x3fc9ce00`, timestamps `0x3fc9cdf8`); the tenth sets NVS
  **`fw/lua_nav = 1`** (`FUN_421aaff0`), with no toast. From then on the drawer lists "Mini Apps"
  between Cloud and USB mode (`tests/golden/licorice-app-drawer.png`). It persists in NVS, so on the
  device it survives reboots; `tools/nvsedit.py IMAGE set-u8 fw lua_nav 1` does the same on a scratch
  image.
- **The page.** "Mini Apps" is the app `sys.apps` ("Apps" in the app registry at DRAM 0x3fc961b4:
  on_create `FUN_4204a1d0`, on_destroy `FUN_4204a17c`, on_event `FUN_4204a0d0`); it lists every
  `/sdcard/XTApps/<dir>/app.xtapp` that parses (a package copied there by hand shows too), scanned
  by a background task — **`apps_sd` is that FreeRTOS task's name** (`FUN_42049ee8` →
  `xTaskCreatePinnedToCoreWithCaps(FUN_42049958, "apps_sd", 0x3000, …)`), not a page route as noted
  earlier. A card tap (`FUN_42049a04`) starts `user.lua`, the Lua host app (registry 0x3fc96554), via
  `FUN_420c5a94` / `FUN_420c56ac`. Licorice's All files also shows the `XTApps` folder (7.x hid it).
- **Leaving.** The Home pad in a running app asks **"Leave app — Leave this mini app?"** (OK / Back);
  OK returns to Mini Apps (`tests/golden/licorice-app-leave.png`). A reboot during an app is resumed
  through RTC memory (`FUN_4202e254`, flag at 0x50000004: back into `user.lua` or `sys.apps`; not
  exercised).

## The tool

`tools/xtapp.py` keeps the classic (7.x) path byte for byte and adds `--format licorice`:

```
.venv/bin/python tools/xtapp.py pack apps/probe --format licorice -o /tmp/probe.xtapp
.venv/bin/python tools/xtapp.py install SD.img apps/probe --format licorice   # -> card root probe.xtapp
.venv/bin/python tools/xtapp.py info /tmp/probe.xtapp                         # header, sections, manifest
```

`pack` puts every `*.lua` of the app directory into the code section (relative paths with `/`, the
entry first; other files are reported and left out), encrypts it with a fresh DEK and IV, and writes
`iv_code` / `hmac_code`; `--manifest-only` leaves the code out (installs, "Cannot run yet"). The
device key is read from the stock app image (`--image` / `--key-image`, default
`images/device/stock-app1-v261013.bin`, gitignored); only the *public* key is derived, so no secret
key material is stored in the repo. Needs the `cryptography` package. Importable:
`pack_licorice`, `install_licorice`, `build_licorice_container`, `read_licorice_container`,
`licorice_sections`, `read_licorice_code` (needs the DEK, which `pack_licorice`'s report carries),
`build_xtco` / `read_xtco`, `licorice_code_files`, `licorice_ctr`, `licorice_wrap_dek` /
`licorice_unwrap_dek`, `licorice_kek`, `licorice_section_hmac`, `licorice_device_pubkey`,
`validate_manifest_licorice`.

On the device: copy the package to the card, install it from All files, tap Settings → About →
Model ten times, then open ≡ → Mini Apps. Only an all-letter `app_id` installs.

## Verified vs inferred

**Verified in the emulator** (`tests/test_licorice_apps.py`, both tests, run twice; and by hand):

- The header and the DEK wrap — *Install this package?*, the **"Installed"** toast, the `"DEK1"` cache
  mark at `0x3fc9e0f8`, and the package copied verbatim to `/sdcard/XTApps/probe/app.xtapp`, now with
  an encrypted code section whose HMAC the installer checked.
- The unlock: ten taps on About → Model, each under 2 s of guest time apart, write NVS
  `fw/lua_nav = 1` (read back from the flash image) and make "Mini Apps" appear in the drawer;
  `fw/lua_nav = 1` set with `nvsedit` does the same. In one of three full `make test` runs a burst
  fell short (Mini Apps missing, gaps all under 2 s). A tap that lands while the panel repaints is
  read and dropped, and the status bar repaints at every minute, at a place relative to the burst
  that follows the host's load. So `test_licorice_mini_apps_unlock` fires its one burst right after a
  minute repaint, with no retry, and the launch test sets `fw/lua_nav = 1` with `nvsedit` before the
  boot instead of tapping (two full `make test` runs green since).
- The launch: Mini Apps lists "API Probe"; a tap runs `apps/probe`'s `index.lua` out of the
  AES-CTR-encrypted XTCO section (its page 1 and 2 against goldens), a tap reaches `on_input`, the
  Home pad → "Leave app" → OK returns to the list, and the console shows no reset.
- The cipher inputs, by negative controls (by hand, 2026-10-13): one flipped ciphertext byte →
  "Install failed"; one flipped `iv_code` bit (same ciphertext and HMAC) → "Cannot run yet"; a
  manifest-only package → "Cannot run yet"; the good package still launches after both failures.

**Verified on the device** (2026-10-29, Licorice 26102901): `tools/xtapp.py pack apps/probe --format
licorice` copied to the card's root installs from All files ("Installed"), the ten taps on About → Model
unhide Ink App, and API Probe runs from it. So the package format, the key wrap to the device's key
(`36036010c7bd05af`, read from the image, never from the device) and the code section's AES-CTR all hold on
the hardware, not only in the emulator.

**Verified off-device** (`tests/test_xtapp.py`): the wrap round-trips; `licorice_ctr` equals the
block-by-block re-implementation of `FUN_4210ad78` at every offset and across the 128-bit carry; the
XTCO layout and its refusals; `hmac_code` over the ciphertext; `read_licorice_code` inverts `pack`.

**Inferred from the decompile/disassembly, not exercised**: the app_id must-be-letters rule beyond
the letter case, the exact permission bits, the streamed (code > 512 KB, assets > 1 MB) paths, the
assets/data/fonts/icons section formats, Lua bytecode in the code section, `required_features`,
`preload_assets`, the RTC resume, and the `store-seed-binding` refusal.

## Open questions

1. Whether a non-letter `app_id` is truly rejected (the validator tail is undecoded SLEIGH).
2. The assets / data / fonts sections end to end (`ctx.assets`, `ctx.data`, `ctx.fonts`): their
   formats are read above, but `tools/xtapp.py` builds none, so an app with images or data files
   cannot be packed yet.
3. The app store's `xteink:store-seed-binding:v1` wrap (refused here as `not_supported`) and the
   store staging path `/sdcard/XTCache/store_staging/%s.xtapp`.
4. What header pair 0x28 is for.

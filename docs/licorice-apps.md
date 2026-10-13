# Mini apps on XTOS Licorice: the installer's package format

XTOS Licorice 261013 (the device's current stock firmware, `docs/stock-licorice.md`) replaced the
7.x "put a folder in `/sdcard/XTApps`" scheme with an **installer**: you drop a `.xtapp` package in
All Files, tap it, and confirm *Install this package?*. The old folder is ignored, and `tools/xtapp.py
install` (the classic form) produces *Install failed*. This page is the reverse-engineering of the
new format: what the installer accepts, how it is built, and what is proven versus inferred.

All addresses are the v261013 image (`images/device/stock-app1-v261013.bin`), read with
`tools/ghidra_stock.py --version v261013`. The strings table is `images/ghidra/stock-v261013/`.

## Headline

**A package this repo builds installs through Licorice's own UI.** `tools/xtapp.py pack --format
licorice` (and `install --format licorice`) wraps a content key for the device and lays out the
container the installer parses; the emulator takes it from All Files to the **"Installed"** toast,
the firmware's DEK-unwrap cache fills with the success mark, and the package lands at
`/sdcard/XTApps/<app_id>/app.xtapp`. `tests/test_licorice_apps.py` reproduces that end to end;
`tests/test_xtapp.py` checks the format fast, no emulator.

**The encryption is mandatory but not device-bound.** Every package carries a wrapped content key
(DEK); there is no unsigned/plaintext install path. The key the device unwraps with, however, is a
**fixed constant in the firmware image**, identical on every unit — not a per-device secret. So any
firmware reader can wrap a DEK the device will accept. This is a notable property: the "DRM" keeps a
casual user from hand-editing a package, but it is forgeable from the image alone.

**Not yet done: running the app.** The installer only *verifies and copies* the package; a mini app's
code stays encrypted inside `app.xtapp` and is decrypted at launch. The packages this tool builds are
manifest-only (no code section), so they install and are listed but "Cannot run yet". The launch-time
decrypt and the Mini Apps page's live entry are the open items at the end.

## The container (`app.xtapp`)

Same `XTAP` magic as the classic form, a different header. `FUN_42108380` reads a 0x80-byte header
(all fields little-endian), confirmed by a successful install:

| offset | size | field | value / rule |
|---|---|---|---|
| 0x00 | 4 | magic | `"XTAP"` (compared against `0x50415458`) |
| 0x04 | 2 | version | `1` |
| 0x06 | 2 | header size | `0x80` |
| 0x08 | 4 | total size | the whole file's length — **must be > 0x7f and ≤ the file** (`FUN_42108380`'s tail gate; leaving it 0 is what first produced *Install failed* before the crypto was ever reached) |
| 0x0c | 4 | feature mask | which encrypted sections to verify; **0 = none** |
| 0x10 | 4 | manifest offset | `0x80` |
| 0x14 | 4 | manifest length | `1..0x2000`, and must fit the file |
| 0x18.. | | section table | (offset, length) pairs for code / assets / data / fonts; a length-0 section is skipped (`FUN_4224d1a8`). Zero for a manifest-only package |
| 0x80 | len | manifest | plain UTF-8 JSON |

The installer (`FUN_42108f68`, reached from `FUN_42109210`, the *Install this package?* action) then:
opens the container (`FUN_42108380`); bounds-checks the five section descriptors (`FUN_4224d1a8`);
parses and validates the manifest (`FUN_42108b20` → `FUN_42108648`, `FUN_42108590`); **unwraps the
DEK (`FUN_4210a908`) — this is mandatory and fails the install if it fails**; verifies each enabled
section's HMAC (`FUN_42107f3c`); and copies the package to `/sdcard/XTApps/<app_id>/app.xtapp`.

## The manifest

Plain JSON at 0x80. Required and validated (`FUN_42108648` extracts, `FUN_42108590` validates):

- **`app_id`** — the `/sdcard/XTApps/<app_id>` directory name. `FUN_42108590` walks it and rejects a
  non-ASCII-letter byte (`(c & 0xdf) + 0xbf > 0x19`), so it must be **all letters**: `probe` installs,
  `gull-rock` (hyphen) does not. *(Inferred from the decompile, which ends in an undecoded tail, and
  confirmed only for the all-letter case; `tools/xtapp.py` rejects a non-letter app_id with this
  reason.)*
- **`display_name`** — the toast and list text ("API Probe" above).
- **`version`** — `FUN_42108590` parses `"%d.%d"` and accepts `1.0`, `0.8` or `0.7` (absent ⇒ 0.8).
- **`permissions`** — `FUN_42108648` folds them to a bitmask and sets a reject bit on an unknown one:
  `sys.battery` 1, `sys.status` 2, `net.http` 4, `net.https` 0x20, `storage.app` 8,
  `storage.user_file` 0x10, `bluetooth` 0x40, `device.status` 0x80, `device.keep_awake` 0x100. Wider
  than 7.x (which took only `sys.battery` / `sys.status`).
- The **DEK-wrap fields**, below — the installer's crypto gate.

Other keys the extractor reads: `entry` (default `index.lua`), `display.orientation`, `lockscreen`
(`enabled`, `interval_sec`), `ttf_reserve_kb`, `required_features`, `optional_features`,
`app_icon_s/l` (the icons `FUN_4210a134` pulls for the list, without decrypting).

## The DEK wrap (the crypto gate)

The manifest carries a content-key (DEK) wrapped for the device. `FUN_4210a908` unwraps it; the four
fields are **hex** strings (`FUN_4224d294` is a hex decoder, not base64):

| field | bytes | meaning |
|---|---|---|
| `dek_wrap_alg` | | must equal `"P256-ECDH-HKDF-SHA256-AESKW"` |
| `dek_wrap_key_id` | | must equal `"36036010c7bd05af"` |
| `dek_wrap_ephemeral_pub` | 65 | an uncompressed P-256 point `04‖X‖Y` (the sender's ephemeral public key) |
| `wrapped_dek_pack` | 24 | the AES-KW-wrapped 16-byte DEK |

The scheme (fully reversed from `FUN_4210a908`):

1. **ECDH.** The device scalar `d` is a **fixed 32-byte constant at DROM `0x3c32c674`** (loaded by
   `FUN_421d7118`, read big-endian — mbedtls convention, confirmed by a live install). It computes
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

A successful unwrap is cached in RAM at `0x3fc9e0f8` (and RTC-fast `0x5000005c`) tagged `"DEK1"` +
the app_id — the oracle `tests/test_licorice_apps.py` reads to prove the wrap verified, and the one
that stayed zero while the header was wrong.

## Section integrity

For each section whose feature-mask bit is set, `FUN_42107f3c` compares the manifest's
`hmac_code` / `hmac_assets` / `hmac_data` / `hmac_fonts` (hex, 32 bytes) against
`HMAC-SHA256(DEK, section bytes)` (`FUN_4210a630`). A manifest-only package (feature mask 0) skips
all of these, which is why it installs with no sections. `tools/xtapp.py`'s
`licorice_section_hmac()` computes the tag for when sections are added.

## Encrypted at rest; the launch path (open)

The installer does **not** decrypt the sections to disk — it copies `app.xtapp` verbatim into
`/sdcard/XTApps/<app_id>/` (verified: the installed file is byte-for-byte our package). The app's
code therefore stays encrypted inside `app.xtapp` and must be decrypted **at launch**. Not yet
reversed:

- The per-section AES decryption (the `iv_code` / `iv_assets` / … manifest fields feed it; the mode
  is one of `FUN_42109d7c` / `FUN_42109f8c` / `FUN_4210a738`, which drive the AES block
  `FUN_4210ad78` — CTR or CBC, unconfirmed), and the "code" section's own layout (whether it is the
  raw `index.lua`, Lua bytecode, or an archive).
- The **Mini Apps page**: it is registered as the route `"apps_sd"` (`0x3c274722`) → `FUN_42049958`,
  built by `FUN_420498c4` (which enumerates `/sdcard/XTApps`, reads icons via `FUN_4210a134`, and
  parses each installed manifest via `FUN_42108ef8`). Its strings are "Mini Apps" / "No mini apps
  yet" / "Cannot run yet" / "Launch failed" / "Uninstall". The emulator install lists the app at
  `/sdcard/XTApps/probe/app.xtapp`, but the live UI entry into that page was not located in this
  session (the side drawer's six items — Desktop, Bookshelf, All files, Cloud, USB mode, Settings —
  do not include it, and the Desktop is a widgets page).

Because the package is manifest-only, a launched app would hit "Cannot run yet"; building a runnable
one needs the two items above. Runtime (installed) folder paths, for reference: `FUN_420cd8d4` reads
`/sdcard/XTApps/<id>/files/<name>` (the per-app data files), `FUN_420cd594` the `state.xtsv` save.

## The tool

`tools/xtapp.py` keeps the classic (7.x) path byte-for-byte and adds `--format licorice`:

```
.venv/bin/python tools/xtapp.py pack apps/probe --format licorice -o /tmp/probe.xtapp
.venv/bin/python tools/xtapp.py install SD.img apps/probe --format licorice   # -> card root probe.xtapp
.venv/bin/python tools/xtapp.py info /tmp/probe.xtapp                         # auto-detects the form
```

The device key is read from the stock app image (`--image`, default
`images/device/stock-app1-v261013.bin`, gitignored); only the *public* key is derived, so no secret
key material is stored in the repo. Needs the `cryptography` package (P-256 / HKDF / AES-KW).
Importable: `pack_licorice`, `build_licorice_container`, `read_licorice_container`,
`licorice_wrap_dek` / `licorice_unwrap_dek`, `licorice_kek`, `licorice_section_hmac`,
`licorice_device_pubkey`, `validate_manifest_licorice`.

## Verified vs inferred

**Verified in the emulator** (`tests/test_licorice_apps.py`, twice):

- The header layout above — a package with it reaches the *Install this package?* dialog and, on OK,
  the **"Installed"** toast.
- The DEK wrap — the `"DEK1"` cache mark at `0x3fc9e0f8` appears, i.e. `FUN_4210a908` accepted our
  ECDH/HKDF/AES-KW package, with `d` read **big-endian** from `0x3c32c674`.
- The installed package at `/sdcard/XTApps/probe/app.xtapp`.
- The wrap is self-consistent off-device (`tests/test_xtapp.py`: `licorice_unwrap_dek` recovers what
  `licorice_wrap_dek` wrapped).

**Inferred from the decompile, not yet exercised**: the app_id must-be-letters rule (confirmed only
for the letter case), the exact permission bit values, the section cipher mode and code-section
format, and the Mini Apps page's live nav entry.

## Open questions

1. The launch-time section decrypt (mode + `iv_*`) and the code-section format — needed to make an
   installed app actually run.
2. How the Mini Apps page (`apps_sd` / `FUN_42049958`) is reached in the live UI.
3. Whether a non-letter `app_id` is truly rejected (the validator tail is undecoded SLEIGH).

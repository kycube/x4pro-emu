# Writing a screen for the stock as a Lua app

Session 8 (2026-09-07). The stock `xteink_app` 7.2.4 carries a complete, unreachable **Lua 5.5
scripting host**: a page that lists apps from the card, a script host that loads `index.lua` from an
app directory, and a drawing/services API bound into the interpreter. Nothing in the shipped UI
enters that page. Three bytes in the app image put a **Lua Apps** row into the nav menu, and a
0x80-byte container file on the card makes an app directory visible — after which "a new screen on
the stock firmware" is a Lua file you can edit and re-copy in seconds, without a compiler, without a
firmware build, and without touching a single native code path.

Everything below marked *confirmed* was seen running in the emulator (`tests/test_lua_apps.py`,
which reproduces all of it from scratch). What is not confirmed is listed at the end, honestly.

## The three pieces

| piece | what it is | tool |
|---|---|---|
| the patch | 3 bytes in the app image: the nav menu gains a sixth row "Lua Apps" → page 0x09 | `tools/stockpatch.py apply IMAGE lua-apps-row` |
| the container | `/sdcard/XTApps/<dir>/app.xtapp`: 0x80-byte header + the manifest JSON | `tools/xtapp.py pack APP_DIR` |
| the script | `/sdcard/XTApps/<dir>/index.lua`, the file that actually runs | `tools/xtapp.py new NAME DIR` |

## 1. The patch

The nav-menu builder `FUN_4213d834` walks menu slots 0..7. It **skips slot 3** in the loop
(`if (i == 3) i = 4`) and appends that slot at the end only when the byte at `presenter+172` is
non-zero — which nothing in the firmware ever writes (the compile-time stub at 0x4233abe8 that would
write it is itself gated on `presenter+140`). One instruction removes the condition:

```
app VA 0x4213d873   app file 0x2ed873   (+0x10000 in a 16 MB flash image)
    82 02 ac   l8ui a8, a2, 172   ->   82 a0 01   movi a8, 1
```

The row appears with its own icon and the label **"Lua Apps"** (already in the i18n pack, group 9)
and enters page id 0x09, `AppsPagePresenter`, whose title renders as "Extensions". The router
registers all 65 pages unconditionally, so nothing else has to change.

```
.venv/bin/python tools/stockpatch.py apply SCRATCH/flash.bin lua-apps-row
.venv/bin/python tools/stockpatch.py list SCRATCH/flash.bin
```

`stockpatch.py` verifies the exact bytes at the site before writing, refuses any image that is not
`xteink_app` 7.2.4, recomputes the ESP checksum byte and the appended SHA-256 the second-stage
bootloader verifies, and re-parses the result; `revert` puts the image back byte for byte. It knows
two other patches — `developer-menu` (Screen Capture, `docs/xic.md`) and `hidden-menu-rows` (the
Preload List / Statistics rows) — and they combine freely. **Never patch a file under `images/`, and
never the device**: a flash image made from the dump still holds the owner's WiFi credentials
(CLAUDE.md rule 6).

Related, and *not* needed: repointing an existing row's page id (`0x3c3dc8b6`, the Cloud Sync slot,
`0d` → `09`) reaches the same page without touching the builder; `08` there reaches the script-host
page directly, which shows "No app selected".

## 2. The container: `app.xtapp`

`FUN_421c37e8` enumerates `/sdcard/XTApps`, builds `%s/%.190s/app.xtapp` for every directory and
**drops the directory when that file does not open**. That is why a plain `manifest.json` +
`index.lua` directory never shows up: not a permission problem, not a signature problem — a missing
file. `FUN_420664d8` reads it:

| offset | size | value |
|---|---|---|
| 0x00 | 4 | `"XTAP"` |
| 0x04 | 2 | version — must be 1 |
| 0x06 | 2 | header size — must be 0x80 (and the header read must return exactly 0x80 bytes) |
| 0x0c | 4 | flags (read, not checked) |
| 0x10 | 4 | manifest offset — must be 0x80 |
| 0x14 | 4 | manifest length — 1..0x2000, and offset+length must fit the file |
| 0x34 | 4 | **must be 0** — non-zero marks an encrypted/wrapped payload |
| 0x80 | len | the manifest, plain UTF-8 JSON |

**No signature and no key.** The wrapped form the strings advertise (`xteink.xtapp.appdek.wrap.v2`,
`dek_wrap_alg`, `hmac_code/assets/data/fonts`) is one *option*, not a requirement: an unsigned,
unencrypted app loads. The reference container is 313 bytes (`tests/data/hello-app`, byte-for-byte
reproduced by `tools/xtapp.py pack`).

The manifest: `app_id` and `display_name` are required (`display_name` is the row text on the app
list). **`permissions` is the one field the firmware validates**: `FUN_4206645c` accepts only
`sys.battery` and `sys.status` and rejects the whole app on anything else — silently, with no
console line. A `"lockscreen"` entry is the usual mistake and makes the app vanish from the list.
Other keys seen in the string table are carried through: `author`, `description`, `app_icon_s`,
`app_icon_l`, `splash`, `display`, `interval_sec`, `preload_assets`, `lockscreen`.

## 3. The directory on the card

```
/sdcard/XTApps/<app_id>/
    app.xtapp        the container above (built from manifest.json)
    manifest.json    the same manifest, as you edit it
    index.lua        what runs
    lockscreen.lua   an alternative entry the host knows about (unreachable, see below)
```

`/sdcard/XTApps` is hidden from All Files, so the card is the only way in. `tools/xtapp.py install
SD.img APP_DIR` does the whole thing (mtools, FAT32 partition at 1 MiB): packs a fresh container and
copies the directory to `/XTApps/<app_id>/`.

## 4. The API, as confirmed at runtime

Lua 5.5, one sandboxed state per app. The globals are `_G, _VERSION, assert, coroutine, error,
getmetatable, ipairs, math, next, pairs, pcall, rawequal, rawget, rawlen, rawset, require, select,
setmetatable, string, table, tonumber, tostring, type, utf8, warn, xpcall` — plus `g` and `ctx`.
There is **no `print`**, and no `io`, `os` or `debug`.

**Callbacks** — define them as globals. `on_load`, `on_enter`, `on_draw`, `on_tick` and `on_input`
were all seen firing; `on_leave` and `on_unload` are registered but were not exercised. A touch
arrives at `on_input` as a **table** (its fields are not mapped); returning `true` claims it.

**Drawing — the global `g`**, methods called with a colon. Coordinates are the **portrait** frame,
480 wide × 800 tall, origin top left, 1 bpp.

| call | effect |
|---|---|
| `g:clear(0)` | blank the whole frame to **white** — status bar and footer included |
| `g:clear()` | ... with no argument, paints the whole frame **black** |
| (no clear) | the page you were started from stays on screen and you paint over it |
| `g:rect(x, y, w, h)` | rectangle outline |
| `g:line(x1, y1, x2, y2)` | line |
| `g:circle(x, y, r)` | circle outline |
| `g:text(x, y, "…")` | text in the system font (the baseline is at `y`) |
| `g:size()` | the screen size — returns 480 first |
| `g:image(…)`, `g:layer(…)` | exist; their arguments are **not** established |

**Services — the global `ctx`**, plain function tables (call with a dot). Present and confirmed:
`ctx.invalidate`, `ctx.request_refresh`, `ctx.set_tick_rate`, `ctx.save`, `ctx.quit`;
`ctx.display.{full_refresh, defer_auto_full, flush_once}`; `ctx.log.{info, warn, error}`;
`ctx.sys.{millis, uptime_ms, epoch_sec, clock, battery, charging, power, status, radio, network,
has_key, has_bt_keyboard, has_bt_gamepad}`; `ctx.system.{set_as_lockscreen_app, quit}`; and the
tables `ctx.screen, ctx.input, ctx.state, ctx.assets, ctx.data, ctx.fonts, ctx.i18n, ctx.layers,
ctx.lock, ctx.perf, ctx.gc` (their members are not established).

> **`ctx.log` is a black hole.** `info`, `warn` and `error` all exist, all return without raising —
> and nothing they are given reaches the USB Serial/JTAG console, UART0 or the card, with developer
> mode on or off. Together with the missing `print` that means **the screen is your only output**:
> draw what you want to see. `tests/test_lua_apps.py` asserts the silence, so if a future image
> starts logging, that test fails and this paragraph is wrong.

A whole app:

```lua
local taps = 0

function on_load()  ctx.log.info('hello: loaded')  end   -- goes nowhere, but costs nothing

function on_input(ev)
  taps = taps + 1
  ctx.invalidate()          -- mark dirty; on_draw follows
  return true               -- claim the event
end

function on_draw()
  g:clear(0)                -- own the frame: white
  g:rect(24, 64, 432, 132)
  g:text(48, 116, 'Hello Lua')
  g:line(24, 236, 456, 236)
  g:circle(240, 392, 104)
  g:text(48, 596, 'taps: ' .. taps)
end
```

with

```json
{ "app_id": "hello", "display_name": "Hello Lua", "entry": "index.lua",
  "permissions": ["sys.battery", "sys.status"] }
```

That is `tests/data/hello-app`, and `tests/golden/stock-lua-hello.png` is what it paints.

## 5. What is not confirmed

- **`g:image` and `g:layer`** — the methods exist; the argument shapes, and how an asset id becomes
  a bitmap, do not. The asset side (`assets.xtab`, `XtappAssetSource`, `XtabAssetSource`,
  `preload_assets`, `app_icon_s/l`, `splash`) has not been exercised at all: apps in the list show a
  default black icon.
- **`ctx.fonts`, `ctx.i18n`, `ctx.assets`, `ctx.data`, `ctx.state`, `ctx.input`, `ctx.screen`,
  `ctx.layers`, `ctx.lock`, `ctx.perf`, `ctx.gc`** — present as tables, members unread. The i18n
  route (`lang/%s.tsv` per app) is only a string.
- **The event table passed to `on_input`** — its keys were never dumped; gestures
  (`double_tap`, `swipe_*`) exist in the API string list but were not seen.
- **`on_tick` rate, `set_tick_rate` units, `ctx.save` semantics, `lua.offscreen`, `draw_with`,
  `stroke`, `color`, `invert`** — named in the registration tables, untested.
- **The lock-screen route is blocked.** `ctx.system.set_as_lockscreen_app()` raises the stock's
  confirmation dialog and does write `user_config/{lockscrLuaApp, lockscrMode = 2, lockscrIdle = 2}`;
  the standby path then reads mode 2 (its `form` changes 5 → 1) and **constructs no presenter** —
  nothing on that path builds `ScriptHostPagePresenter`, so the sleep screen stays the stock's. The
  `lockscreen.lua` entry point the host knows about is unreachable for the same reason. Finding the
  standby-side gate is the next Ghidra job (docs/stock-firmware.md, "Probed in session 7").
- **The device.** Everything here was done in the emulator. The patched image has not been flashed
  to the real X4 Pro.

## 6. The workflow

**In the emulator** (nothing here touches the device):

```
S=/tmp/lua                                        # scratch, never under images/ or the repo
cp images/device/flash-2026-09-06-a.bin $S/flash.bin
.venv/bin/python tools/nvsedit.py $S/flash.bin set-u8 user_config net_en 0   # skip the WiFi start
.venv/bin/python tools/stockpatch.py apply $S/flash.bin lua-apps-row
.venv/bin/python tools/mksd.py $S/sd.img --size 256M --src images/device/sd-files
.venv/bin/python tools/xtapp.py new hello $S/hello        # manifest.json + index.lua template
$EDITOR $S/hello/index.lua
.venv/bin/python tools/xtapp.py install $S/sd.img $S/hello
.venv/bin/python tools/x4emu --name lua1 run --flash $S/flash.bin --sd $S/sd.img --boot-hold-power
.venv/bin/python tools/x4emu --name lua1 wait-refresh --total 2 --timeout 120
.venv/bin/python tools/x4emu --name lua1 tap  88  38 --quiet 2 --wait 20     # the hamburger
.venv/bin/python tools/x4emu --name lua1 tap 585 150 --quiet 2 --wait 20     # Lua Apps (6th row)
.venv/bin/python tools/x4emu --name lua1 tap 225 355 --quiet 2 --wait 20     # the app card
.venv/bin/python tools/x4emu --name lua1 screenshot $S/app.png
```

Iterating on the script means `xtapp.py install` onto a fresh copy of the card and a reboot: the
stock mounts the card at boot and writes to it, so edit the image while the emulator is stopped.

**On the device**, when it comes to that: the app image goes into a slot through the guarded tools
only (`tools/device.py`, backup first, never 0x0..0x10000 — CLAUDE.md, "The real device"), and the
app directory is copied onto the card like any other file. The card side is reversible on its own;
the patch is reverted with `tools/stockpatch.py revert IMAGE lua-apps-row`, which restores the
image byte for byte.

> **Every offset here is 7.2.4 only.** `stockpatch.py` reads the image's `esp_app_desc` and refuses
> anything else, so a newer stock (the desk unit took an OTA to **7.5.4** on 2026-09-07, CLAUDE.md)
> is rejected rather than corrupted. Porting means finding the three sites again in the new image —
> `tools/ghidra_stock.py` on the nav-menu builder, then `tools/appdis.py` to confirm the bytes — and
> adding a second manifest entry. The card side (`tools/xtapp.py`) is version-independent as long as
> the container format has not changed.

## What is where

| file | |
|---|---|
| `tools/stockpatch.py` | the named-patch manifest (`list` / `apply` / `revert` / `--check`) |
| `tools/xtapp.py` | `new` / `pack` / `info` / `install` for `.xtapp` app directories |
| `tests/data/hello-app/` | the reference app: the manifest whose container the stock accepted, and its `index.lua` |
| `tests/test_stockpatch.py`, `tests/test_xtapp.py` | the fast tests (no emulator) |
| `tests/test_lua_apps.py` | the emulator test: menu → app list → the script painting |
| `tests/golden/stock-menu-lua.png`, `stock-lua-apps.png`, `stock-lua-hello.png` | its three screens |
| `docs/stock-firmware.md` | the map of the app, and how these offsets were found |

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

Everything in this section was read off the screen by `apps/probe`, which enumerates the host and
draws the result (§7): there is no other output channel.

### Callbacks

Define them as globals. **Every callback is called with `ctx` as its first argument**, so the
one-argument forms in older notes were reading `ctx`, not an event:

| callback | arguments | |
|---|---|---|
| `on_load(ctx)` / `on_enter(ctx)` | `ctx` | seen firing |
| `on_draw(ctx, g)` | `ctx`, and the same `g` as the global | |
| `on_tick(ctx, n)` | `ctx`, a number | fires on its own; the number's meaning is not established |
| `on_input(ctx, ev)` | `ctx`, the event table below | return `true` to claim the event |
| `on_leave`, `on_unload` | | registered, not exercised |

**The `on_input` event table** — a plain table, no metatable, five keys:

```lua
{ type = 'touch', gesture = 'tap', x = 79, y = 240, time_ms = 33219 }
```

`x` and `y` are in the **same portrait frame you draw in**, so a hit test is a plain comparison.
Only `type='touch'`, `gesture='tap'` have been seen; the `double_tap` / `swipe_*` names in the API
string list have not.

### Drawing — the global `g`

`g` is a **plain table with exactly eight functions and no metatable**. The other names in the
registration tables (`stroke`, `color`, `invert`, `draw_with`, `offscreen`, and any of `fill`,
`fill_rect`, `pixel`, `font`, `measure`, `push`/`pop`, `clip`) are **not bound**: calling one is
"attempt to call a nil value". Coordinates are the portrait frame, 480 wide × 800 tall, origin top
left, 1 bpp.

| call | effect |
|---|---|
| `g:clear(0)` | blank the whole frame to **white** — status bar and footer included |
| `g:clear()` / `g:clear(1)` | ... paints the whole frame **black** |
| (no clear) | the page you were started from stays on screen and you paint over it |
| `g:rect(x, y, w, h)` | rectangle **outline**. A fifth argument is accepted and ignored — there is no fill |
| `g:line(x1, y1, x2, y2)` | line |
| `g:circle(x, y, r)` | circle outline |
| `g:text(x, y, "…")` | text in the system font. **`y` is the top of the ~24 px line box**, not the baseline (the baseline lands near `y + 19`), which is why a rule meant to sit under a line goes at `y + 30` |
| `g:size()` | two values, `480, 800` |
| `g:image(asset, x, y)` | argument #2 must be a number; a string asset id is accepted and draws nothing without an asset pack |
| `g:layer(t)` | wants a **table** — the layer object `ctx.layers:create(w, h)` returns |

**There is no filled shape and no second text size.** Both status apps in `apps/` work around it the
same way: `fill(x, y, w, h)` stacks `h` calls to `g:line`, and anything that has to be big — the
clock — is drawn as seven-segment digits out of those fills.

### Services — the global `ctx`

Twenty members: `assets, data, display, fonts, gc, i18n, input, invalidate, layers, lock, log, perf,
quit, request_refresh, save, screen, set_tick_rate, state, sys, system`. `invalidate`,
`request_refresh`, `set_tick_rate`, `save` and `quit` are plain functions taking no `self`.

> **The sub-tables' functions are methods.** `ctx.data.exists('a.txt')` fails with *bad argument #2
> (string expected, got no value)*: the table itself is the first argument. Call them with a colon —
> `ctx.data:exists('a.txt')`, `ctx.fonts:handle(name)`, `ctx.layers:create(w, h)`, `ctx.gc:count()`.
> `ctx.sys.*` is the exception: those are plain functions.

| sub-table | members, as read at runtime |
|---|---|
| `ctx.screen` | `width=480`, `height=800`, `short_side=480`, `long_side=800`, `is_portrait=true`, `is_landscape=false`, `orientation="portrait"` |
| `ctx.input` | `caps = {touch=true, keys=true, bluetooth=true, gyro=true}`, `has_key`, `has_bt_keyboard`, `has_bt_gamepad` |
| `ctx.fonts` | `handle`, `status` — both want a name; `ctx.fonts:handle(n)` returned **nil for every built-in name tried** (`default`, `system`, `ui`, `title`, `large`, `small`, `body`, `bold`, `regular`, `mono`, `digit`, `sans`, `serif`, `24`, `32`, `48`). Fonts look like app assets, not selectable faces |
| `ctx.assets` | `handle`, `info`, `evict`; `info()` = `{count=0, limit=2097152, …}` with no asset pack |
| `ctx.data` | `exists`, `size`, `read_text`, `open_text`; `ctx.data:exists('a.txt')` = `false`, `ctx.data:read_text('a.txt')` = `nil, "not_found"` — a per-app file API that answers |
| `ctx.i18n` | `locale="en"`, `t`; `ctx.i18n:t('ok')` returns the key itself when the app ships no `lang/en.tsv` |
| `ctx.layers` | `create`; `ctx.layers:create(64, 64)` **returns a layer table with `dispose` and `draw_with`** — the offscreen route exists |
| `ctx.lock` | `base_page=""`, `clear_background=true`, `elapsed_sec=0`, `interval_sec=30`, `reason="idle"`, `flush_once`, `set_interval` |
| `ctx.perf` | `info`; `info()` = `{asset_hit=0, asset_mis…=0, …}` |
| `ctx.gc` | `collect`, `count`; `ctx.gc:count()` ≈ 65 000–80 000 (bytes of Lua heap) |
| `ctx.display` | `full_refresh`, `defer_auto_full` |
| `ctx.log` | `info`, `warn`, `error` — see the black-hole note below |
| `ctx.system` | `set_as_lockscreen_app` |
| `ctx.state` | an **empty table** on entry |

**`ctx.sys` — twelve plain functions.** `has_key` / `has_bt_keyboard` / `has_bt_gamepad` are on
`ctx.input`, not here.

| call | returns |
|---|---|
| `ctx.sys.battery()` | a number, 0..100 |
| `ctx.sys.charging()` | boolean |
| `ctx.sys.millis()`, `ctx.sys.uptime_ms()` | milliseconds since boot |
| `ctx.sys.epoch_sec()`, `ctx.sys.local_sec()` | seconds since 1970; `local_sec` is `epoch_sec` minus the device's zone offset |
| `ctx.sys.clock()` | **a userdata with `hour, minute, second, year, month, day, weekday`** (local time; `weekday` 0 = Sunday). This is how you get a date without arithmetic |
| `ctx.sys.power()` | a userdata with `percent`, `charging` |
| `ctx.sys.network()` | a userdata with `state` (`"unknown"` with the radio off) |
| `ctx.sys.radio()` | a userdata with `mode` (`"idle"`) |
| `ctx.sys.status()`, `ctx.sys.bluetooth()` | userdata; no field name tried answered |

The userdata objects' metatables are **protected** (`getmetatable` returns `false`), so they cannot
be enumerated — only named fields answer, and the list above is what a wide name probe found.

> **`ctx.log` is a black hole.** `info`, `warn` and `error` all exist, all return without raising —
> and nothing they are given reaches the USB Serial/JTAG console, UART0 or the card, with developer
> mode on or off. Together with the missing `print` that means **the screen is your only output**:
> draw what you want to see. `tests/test_lua_apps.py` asserts the silence, so if a future image
> starts logging, that test fails and this paragraph is wrong.

**The Back pad does not leave an app.** Inside a running script the capacitive Home pad raises the
stock's own dialog — `Exit app "<app_id>"?`, *Continue | Exit* — and only *Exit* returns to the app
list (`tests/test_lua_status.py::back_to_list`).

A whole app:

```lua
local taps = 0

function on_load()  ctx.log.info('hello: loaded')  end   -- goes nowhere, but costs nothing

function on_input(ctx, ev)
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

## 4b. Measuring text (there is no `g:textwidth`)

`g` cannot measure a string, so right-aligned and centred text has to be placed from a table of
advance widths. Guessing does not work: `apps/status-panels` first shipped with an estimate that was
**15 px short** for `Tue 17 Mar`, and the date ran past the right margin — which is exactly what the
owner saw.

The measurement needs no host support, only arithmetic. The ink width of *n* copies of a character
is `(n-1) * advance + ink_width(c)`, so two cells per character are enough:

```
advance(c) = ( ink_width("cccc") - ink_width("cc") ) / 2
```

`apps/probe-metrics/` draws that: page 1 is a 6 x 32 grid with both cells for all 95 printable ASCII
characters, page 2 handles the space (no ink of its own, so it is bracketed — `"|  |"` against
`"|    |"`), the characters too wide for a grid cell (`W`) or too narrow to separate (`i`, `j`), and
six validation strings. Screenshot each page, un-rotate it (`Image.rotate(-90, expand=True)`), and
read the ink extents per cell.

**The table, as measured on the stock's own rendering** (2026-09-07; it agrees with the advances in
the device's `system_small.xtf`, which is a useful cross-check — `tools/xtfont.py` can print those):

| advance | characters |
|---|---|
| 4 | `'` |
| 5 | `.` `:` `I` `i` `l` \| |
| 6 | space `,` `;` `` ` `` `j` |
| 7 | `(` `)` `[` `]` `{` `}` |
| 8 | `"` `\` `f` `r` `t` |
| 9 | `*` `-` `/` `1` `^` |
| 10 | `?` `J` `_` `s` `z` |
| 11 | `7` `a` `c` `k` `v` `x` `y` `~` |
| 12 | `!` `$` `2` `3` `4` `5` `9` `E` `F` `L` `b` `d` `e` `g` `h` `n` `o` `p` `q` `u` |
| 13 | `+` `0` `6` `8` `<` `=` `>` `P` `R` `S` `T` `Z` |
| 14 | `A` `B` `C` `K` `V` `X` `Y` |
| 15 | `#` `&` `D` `G` `H` `N` `U` |
| 16 | `O` `Q` |
| 17 | `%` `@` `w` |
| 18 | `M` `m` |
| 20 | `W` |

Validated against six real strings: the sum of advances lands **1–2 px above** the measured ink
width every time, which is the side bearings and exactly what should happen. `apps/status-panels`
carries this table as `ADV_BY_WIDTH` and a `textw()` over it; copy both into any app that aligns
text. The one caveat: this is the face the host draws Lua text with today. If a future firmware or a
different installed system font changes it, re-run `apps/probe-metrics` — that is what it is for.

## 5. What is not confirmed

Shorter than it was — `apps/probe` (§7) answered the API shape, the `on_input` event and the
`ctx.sys` getters. What is left:

- **Assets.** `g:image(asset, x, y)` takes the arguments but there is no asset pack to feed it:
  `assets.xtab`, `XtabAssetSource`, `preload_assets`, `app_icon_s/l` and `splash` have still not been
  exercised, `ctx.assets:handle(name)` answers nil, and apps in the list show a default black icon.
- **Layers.** `ctx.layers:create(w, h)` returns a real object with `dispose` and `draw_with`, and
  `g:layer(t)` wants exactly such a table — but nothing has been *composited* through them yet, and
  `draw_with`'s argument (a function? a layer?) is unread.
- **Fonts.** `ctx.fonts:handle(name)` is nil for every built-in name, so a second text size is
  probably an app-supplied font pack; how a handle would then reach `g:text` (a fourth argument is
  accepted and ignored) is unknown.
- **`ctx.save` and `ctx.state`.** `state` is an empty table on entry and `save` was not called.
- **`set_tick_rate` units**, and what the number `on_tick` receives means.
- **`ctx.lock`** (`set_interval`, `flush_once`, `interval_sec = 30`, `reason = "idle"`) belongs to
  the lock-screen route, which is still blocked: `ctx.system.set_as_lockscreen_app()` raises the
  stock's confirmation dialog and does write `user_config/{lockscrLuaApp, lockscrMode = 2,
  lockscrIdle = 2}`; the standby path then reads mode 2 (its `form` changes 5 → 1) and **constructs
  no presenter** — nothing on that path builds `ScriptHostPagePresenter`, so the sleep screen stays
  the stock's, and the `lockscreen.lua` entry point is unreachable for the same reason. Finding the
  standby-side gate is the next Ghidra job (docs/stock-firmware.md, "Probed in session 7").
- **`on_leave` / `on_unload`** are registered and were not seen firing.
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

## 7. The apps in `apps/`

Four app directories live in the repo, each one `manifest.json` + `index.lua`, ready for
`tools/xtapp.py install`. `app_id` and the directory name are kept the same, because `install` uses
`app_id` for the card directory and a mismatch is confusing on the device.

| directory | `app_id` | what it is |
|---|---|---|
| `apps/probe/` | `probe` | the API dump §4 was written from: it enumerates `g`, every `ctx` sub-table and every `ctx.sys` getter, calls the ones that cannot change state, catches the error messages (a bad call's *"bad argument #2 to 'handle'"* is the only documentation the host has), and paints the lot one page per tap. The last two pages are the live `on_input` event and a set of drawing experiments. Nine pages; tap anywhere to advance |
| `apps/status-typographic/` | `status-typographic` | **Status, variant A.** Airy: the weekday alone at the top, a hand-drawn seven-segment `HH:MM` filling the upper third, the date spelled out under it, a battery percentage with a gauge across the page, then four quiet label/value rows (uptime, network, radio, Lua heap). White space does the work; one hairline per section |
| `apps/status-panels/` | `status-panels` | **Status, variant B, the owner's pick.** The same facts as framed cards: a solid header band, a clock card, a battery card with a wide gauge, a 2 × 2 grid of stat cards and a footer band. Denser, more "instrument panel". Its geometry is a three-number scale — page margin 24, card padding 18, gap between cards 16 — and every right-aligned string is placed with the measured advance table (§4b) |
| `apps/probe-metrics/` | `probe-metrics` | the sheet §4b is measured from: two cells per printable character, plus the space, the awkward characters and six validation strings. Tap to change page. Re-run it if a firmware update or a new system font changes the face |

Both status apps are readable on purpose — the layout is a list of `g:` calls with the coordinates
in plain sight, so retuning one is editing numbers. Both carry the same two workarounds, commented
at the top of each file: `fill()` stacks `g:line` because the host has no filled shape, and the big
clock is seven-segment digits made of those fills because there is only one text size.

**Installing one on the card** (the app image must already carry the `lua-apps-row` patch, §1):

```
.venv/bin/python tools/xtapp.py install /path/to/sd.img apps/status-typographic
```

That writes `/XTApps/status-typographic/{app.xtapp, manifest.json, index.lua}` and nothing else. On
the device the same three files are simply copied into `/XTApps/<app_id>/` on the card — `/sdcard/XTApps` is
hidden from All Files, so the card reader is the way in. Two apps installed side by side show as two
cards on the Lua Apps page; the app list's card coordinates in the emulator are `tap 225 355` (left)
and `tap 225 125` (right).

`tests/test_lua_status.py` boots the patched stock with the RTC pinned
(`-global driver=x4pro.pcf8563,property=base-epoch,value=…`) and checks both screens against
`tests/golden/stock-lua-status-{list,typographic,panels}.png`. The date is compared pixel for pixel;
the `HH:MM` digits, the uptime and the Lua-heap figure move between runs and are the only masked
bands, and each masked band is separately asserted to contain ink.

## What is where

| file | |
|---|---|
| `tools/stockpatch.py` | the named-patch manifest (`list` / `apply` / `revert` / `--check`) |
| `tools/xtapp.py` | `new` / `pack` / `info` / `install` for `.xtapp` app directories |
| `apps/probe/` | the API probe §4 was written from |
| `apps/status-typographic/`, `apps/status-panels/` | the two status screens (§7) |
| `tests/data/hello-app/` | the reference app: the manifest whose container the stock accepted, and its `index.lua` |
| `tests/test_stockpatch.py`, `tests/test_xtapp.py` | the fast tests (no emulator) |
| `tests/test_lua_apps.py` | the emulator test: menu → app list → the script painting |
| `tests/golden/stock-menu-lua.png`, `stock-lua-apps.png`, `stock-lua-hello.png` | its three screens |
| `tests/test_lua_status.py` | the emulator test for the two status screens |
| `tests/golden/stock-lua-status-{list,typographic,panels}.png` | its three screens |
| `docs/stock-firmware.md` | the map of the app, and how these offsets were found |

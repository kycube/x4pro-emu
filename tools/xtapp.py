#!/usr/bin/env python3
r"""Build, inspect and install `.xtapp` Lua app containers for the stock `xteink_app` 7.2.4.

  xtapp.py new NAME OUT_DIR [--display-name T] [--author A]   scaffold manifest.json + index.lua
  xtapp.py pack APP_DIR [-o OUT]                              write APP_DIR/app.xtapp
  xtapp.py info FILE.xtapp                                    header + manifest of a container
  xtapp.py install SD.img APP_DIR [--as DIR]                  copy the app onto a card image

A "Lua app" is a directory on the card, `/sdcard/XTApps/<dir>/`, holding `manifest.json`,
`index.lua` (the script that runs) and `app.xtapp` (this container). The stock lists it under the
nav menu's **Lua Apps** row -- a row that only exists once the app image carries the `lua-apps-row`
patch (`tools/stockpatch.py`); the page behind it is 0x09, `AppsPagePresenter`. The whole workflow
is `docs/lua-apps.md`.

THE CONTAINER (`app.xtapp`), as the firmware reads it
    `FUN_421c37e8` enumerates `/sdcard/XTApps`, builds `%s/%.190s/app.xtapp` per directory and
    **drops any directory whose container fails to open**, which is why a plain
    `manifest.json` + `index.lua` directory never appears in the list. `FUN_420664d8` opens it:

        offset  size  value
        0x00     4    "XTAP"                       magic
        0x04     2    1                            version   (must be 1)
        0x06     2    0x80                         header size (must be 0x80, and the header read
                                                   must return exactly 0x80 bytes)
        0x0c     4    flags                        read, not checked here (0)
        0x10     4    manifest offset              must be 0x80
        0x14     4    manifest length              1..0x2000, and offset+length must fit the file
        0x30     4    (only looked at when 0x34 is non-zero)
        0x34     4    0                            **must be 0**: non-zero = encrypted/wrapped
        rest          zero
        0x80   len    the manifest, as plain UTF-8 JSON

    There is no signature and no DEK: an unwrapped, unsigned app loads (the wrapped
    `xteink.xtapp.appdek.wrap.v2` form exists in the strings but is not required). The JSON is
    written **compact** (`{"a":1}`, no spaces), which is what the reference container holds; nothing
    in the firmware depends on that, but it keeps `pack` reproducible byte for byte.

THE MANIFEST
    `app_id` and `display_name` are required (`display_name` is the row text on the Lua Apps page).
    `permissions` is the one field the firmware validates: `FUN_4206645c` accepts **only**
    `sys.battery` and `sys.status` and rejects the whole app on anything else -- a `"lockscreen"`
    entry is what made the first attempt vanish from the list. Other keys seen in the string table
    (`author`, `description`, `app_icon_s`, `app_icon_l`, `splash`, `display`, `interval_sec`,
    `preload_assets`, `lockscreen`) are carried through untouched; none is required and only
    `app_id`, `display_name`, `author`, `display` and `permissions` are known to be read.
    `entry` names the script; the host actually builds `<dir>/index.lua` (or `lockscreen.lua` on the
    lock-screen route, which does not work: docs/lua-apps.md).

EXAMPLE
    .venv/bin/python tools/xtapp.py new clock /tmp/apps/clock
    $EDITOR /tmp/apps/clock/index.lua
    .venv/bin/python tools/xtapp.py pack /tmp/apps/clock
    .venv/bin/python tools/xtapp.py install /tmp/sd.img /tmp/apps/clock   # -> /XTApps/clock/
    .venv/bin/python tools/stockpatch.py apply /tmp/flash.bin lua-apps-row

`install` uses mtools on the FAT32 partition at 1 MiB (`tools/mksd.py`'s layout, the device's own):
it creates `/XTApps/<app_id>/`, packs a fresh `app.xtapp` (into a temporary file -- APP_DIR itself
is never written to) and copies every file of the directory, recursively, into it. Nothing else on
the card is touched.

`pack()`, `read_container()`, `validate_manifest()`, `install()` and `scaffold()` are importable;
they raise `XtappError` with a message that says which rule was broken.
"""
import argparse, collections, json, os, shutil, struct, subprocess, sys, tempfile

MAGIC = b'XTAP'
VERSION = 1
HEADER_SIZE = 0x80
MANIFEST_OFF = 0x80
MANIFEST_MAX = 0x2000                # FUN_420664d8: 0x1fff < len-1 is rejected, so 1..0x2000
PART_OFFSET = 1048576                # tools/mksd.py puts the FAT32 partition at 1 MiB
APPS_DIR = 'XTApps'
REQUIRED = ('app_id', 'display_name')
PERMISSIONS = ('sys.battery', 'sys.status')     # FUN_4206645c accepts nothing else
ENTRY_NAMES = ('index.lua', 'lockscreen.lua')   # what the script host opens (0x4212a92a)
MAX_DIR = 190                        # the `%s/%.190s/app.xtapp` format truncates past this


class XtappError(ValueError):
    """A manifest the firmware would reject, or a container that is not the 0x80-byte XTAP form."""


# ---------------------------------------------------------------- manifest + container
def load_manifest(path):
    """Read a manifest.json, keeping its key order (so `pack` is reproducible)."""
    try:
        raw = open(path, 'rb').read()
    except OSError as e:
        raise XtappError(f'{e}') from None
    try:
        obj = json.loads(raw.decode('utf-8'), object_pairs_hook=collections.OrderedDict)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise XtappError(f'{path}: not valid UTF-8 JSON ({e})') from None
    if not isinstance(obj, dict):
        raise XtappError(f'{path}: the manifest must be a JSON object, not a {type(obj).__name__}')
    return obj


def validate_manifest(obj, where='manifest.json'):
    """Raise XtappError on anything the stock's loader would reject. Returns the manifest."""
    for key in REQUIRED:
        if key not in obj:
            raise XtappError(f'{where}: no "{key}" -- {" and ".join(REQUIRED)} are required '
                             f'("display_name" is the row text on the Lua Apps page)')
        if not isinstance(obj[key], str) or not obj[key].strip():
            raise XtappError(f'{where}: "{key}" must be a non-empty string, got {obj[key]!r}')
    app_id = obj['app_id']
    if '/' in app_id or '\\' in app_id or app_id in ('.', '..'):
        raise XtappError(f'{where}: "app_id" {app_id!r} is a directory name under /sdcard/XTApps: '
                         f'no slashes')
    if len(app_id.encode('utf-8')) > MAX_DIR:
        raise XtappError(f'{where}: "app_id" is {len(app_id.encode("utf-8"))} bytes; the firmware '
                         f'builds "%s/%.{MAX_DIR}s/app.xtapp" and would truncate it')
    perms = obj.get('permissions', [])
    if not isinstance(perms, list):
        raise XtappError(f'{where}: "permissions" must be a list, got {type(perms).__name__}')
    for p in perms:
        if p not in PERMISSIONS:
            raise XtappError(
                f'{where}: permission {p!r} is not allowed. The stock (FUN_4206645c) accepts only '
                f'{" and ".join(PERMISSIONS)} and rejects the whole app -- it disappears from the '
                f'Lua Apps list with no message on the console. Drop it (a "lockscreen" entry is '
                f'the usual mistake; the lock-screen route does not work anyway, docs/lua-apps.md)')
    entry = obj.get('entry')
    if entry is not None and (not isinstance(entry, str) or not entry):
        raise XtappError(f'{where}: "entry" must be a string naming the script')
    return obj


def manifest_bytes(obj):
    """The manifest as the container carries it: compact UTF-8 JSON, key order preserved."""
    blob = json.dumps(obj, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    if not 1 <= len(blob) <= MANIFEST_MAX:
        raise XtappError(f'the manifest is {len(blob)} bytes; the firmware accepts 1..{MANIFEST_MAX}')
    return blob


def build_container(obj, flags=0):
    """The 0x80-byte header + the compact manifest JSON."""
    blob = manifest_bytes(obj)
    head = bytearray(HEADER_SIZE)
    head[0:4] = MAGIC
    struct.pack_into('<HH', head, 4, VERSION, HEADER_SIZE)
    struct.pack_into('<I', head, 0x0c, flags)
    struct.pack_into('<II', head, 0x10, MANIFEST_OFF, len(blob))
    # 0x30 and 0x34 stay zero: 0x34 != 0 means an encrypted/wrapped payload and the loader bails
    return bytes(head) + blob


def read_container(data, where='app.xtapp'):
    """(header dict, manifest object) of a container, checked the way FUN_420664d8 checks it."""
    data = bytes(data)
    if len(data) < HEADER_SIZE:
        raise XtappError(f'{where}: {len(data)} bytes, shorter than the {HEADER_SIZE}-byte header')
    if data[:4] != MAGIC:
        raise XtappError(f'{where}: magic is {data[:4]!r}, expected {MAGIC!r}')
    version, header_size = struct.unpack_from('<HH', data, 4)
    flags, = struct.unpack_from('<I', data, 0x0c)
    off, length = struct.unpack_from('<II', data, 0x10)
    word30, word34 = struct.unpack_from('<II', data, 0x30)
    head = {'magic': MAGIC.decode(), 'version': version, 'header_size': header_size, 'flags': flags,
            'manifest_offset': off, 'manifest_length': length, 'word_0x30': word30,
            'word_0x34': word34, 'file_bytes': len(data)}
    if version != VERSION or header_size != HEADER_SIZE:
        raise XtappError(f'{where}: version {version} / header size {header_size:#x}, expected '
                         f'{VERSION} / {HEADER_SIZE:#x}')
    if off != MANIFEST_OFF:
        raise XtappError(f'{where}: manifest offset {off:#x}, expected {MANIFEST_OFF:#x}')
    if not 1 <= length <= MANIFEST_MAX:
        raise XtappError(f'{where}: manifest length {length}, expected 1..{MANIFEST_MAX}')
    if off + length > len(data):
        raise XtappError(f'{where}: manifest {off:#x}+{length} runs past the {len(data)}-byte file')
    if word34 != 0:
        raise XtappError(f'{where}: the word at 0x34 is {word34:#x}, not 0 -- that marks an '
                         f'encrypted/wrapped payload and the loader refuses it')
    try:
        obj = json.loads(data[off:off + length].decode('utf-8'),
                         object_pairs_hook=collections.OrderedDict)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise XtappError(f'{where}: the manifest is not valid UTF-8 JSON ({e})') from None
    return head, obj


# ---------------------------------------------------------------- the commands
def pack(app_dir, out=None):
    """Write `<app_dir>/app.xtapp` (or `out`) from `<app_dir>/manifest.json`. Returns a report."""
    mpath = os.path.join(app_dir, 'manifest.json')
    if not os.path.exists(mpath):
        raise XtappError(f'{mpath} not found: a Lua app directory holds manifest.json, index.lua '
                         f'and the app.xtapp built from them (tools/xtapp.py new NAME DIR)')
    obj = validate_manifest(load_manifest(mpath), mpath)
    data = build_container(obj)
    dst = out or os.path.join(app_dir, 'app.xtapp')
    with open(dst, 'wb') as f:
        f.write(data)
    entry = obj.get('entry', 'index.lua')
    warnings = []
    if not any(os.path.exists(os.path.join(app_dir, n)) for n in (entry,) + ENTRY_NAMES):
        warnings.append(f'no {entry} in {app_dir}: the app will list but run nothing')
    if os.path.basename(os.path.abspath(app_dir)) != obj['app_id']:
        warnings.append(f'the directory is {os.path.basename(os.path.abspath(app_dir))!r} but '
                        f'app_id is {obj["app_id"]!r}; `install` uses app_id for the card directory')
    return {'out': dst, 'bytes': len(data), 'manifest_bytes': len(data) - HEADER_SIZE,
            'app_id': obj['app_id'], 'display_name': obj['display_name'],
            'permissions': list(obj.get('permissions', [])), 'warnings': warnings}


def _mtools(img, *args, check=True):
    return subprocess.run([args[0], '-i', f'{img}@@{PART_OFFSET}', *args[1:]],
                          capture_output=True, text=True, check=check)


def install(img, app_dir, as_dir=None):
    """Copy `app_dir` into `/XTApps/<app_id>/` on a card image, with an `app.xtapp` packed fresh from
    its manifest. `app_dir` is only read: the container is built in a temporary directory, so an app
    kept under version control does not gain a generated file."""
    if not os.path.isdir(app_dir):
        raise XtappError(f'{app_dir} is not a directory')
    if not os.path.exists(img):
        raise XtappError(f'{img} not found (tools/mksd.py builds a card image)')
    if not shutil.which('mcopy'):
        raise XtappError('mcopy not found (brew install mtools / apt install mtools)')
    with tempfile.TemporaryDirectory() as tmp:                  # the container is built outside
        rep = pack(app_dir, os.path.join(tmp, 'app.xtapp'))     # app_dir: nothing is written there
        name = as_dir or rep['app_id']
        _mtools(img, 'mmd', f'::/{APPS_DIR}', check=False)      # already there: mmd fails, fine
        _mtools(img, 'mmd', f'::/{APPS_DIR}/{name}', check=False)
        files = sorted(n for n in os.listdir(app_dir) if not n.startswith('.')
                       and n != 'app.xtapp' and os.path.isfile(os.path.join(app_dir, n)))
        _mtools(img, 'mcopy', '-o', rep['out'], *[os.path.join(app_dir, n) for n in files],
                f'::/{APPS_DIR}/{name}/')
    files = sorted(files + ['app.xtapp'])
    for sub in sorted(n for n in os.listdir(app_dir) if os.path.isdir(os.path.join(app_dir, n))
                      and not n.startswith('.')):
        _mtools(img, 'mcopy', '-o', '-s', os.path.join(app_dir, sub), f'::/{APPS_DIR}/{name}/')
        files.append(sub + '/')
    listing = _mtools(img, 'mdir', '-b', f'::/{APPS_DIR}/{name}', check=False).stdout
    rep.update({'image': img, 'card_dir': f'/{APPS_DIR}/{name}', 'files': files,
                'listing': [ln.strip() for ln in listing.splitlines() if ln.strip()]})
    if not any(ln.lower().endswith('app.xtapp') for ln in rep['listing']):
        raise XtappError(f'{img}: app.xtapp is not in /{APPS_DIR}/{name} after the copy '
                         f'(mtools said: {listing!r})')
    return rep


TEMPLATE = '''\
-- {display_name} -- a Lua screen for the stock xteink_app 7.2.4.
--
-- Lives on the card as /sdcard/XTApps/{app_id}/index.lua, next to manifest.json and the app.xtapp
-- container that `tools/xtapp.py pack` builds. It shows up under the nav menu's "Lua Apps" row,
-- which exists only in an app image patched with `tools/stockpatch.py apply IMAGE lua-apps-row`.
--
-- EVERYTHING BELOW MARKED "confirmed" WAS SEEN RUNNING IN THE EMULATOR (docs/lua-apps.md).
--
-- Lua 5.5, one sandboxed state per app. The globals it has: _G, _VERSION, assert, coroutine, error,
-- getmetatable, ipairs, math, next, pairs, pcall, rawequal, rawget, rawlen, rawset, require, select,
-- setmetatable, string, table, tonumber, tostring, type, utf8, warn, xpcall -- and `g` and `ctx`.
-- There is NO `print` output: nothing you print reaches the console. Use ctx.log.info.
--
-- CALLBACKS (define them as globals; all confirmed to fire)
--   on_load()        once, after the script is loaded
--   on_enter()       when the page becomes visible
--   on_draw()        paint here, using `g`; called whenever the page is drawn
--   on_tick()        periodic (ctx.set_tick_rate(ms) asks for a rate)
--   on_input(ev)     a touch; `ev` is a table (fields not fully mapped -- print them with ctx.log)
--   on_leave(), on_unload()   the other end of the lifecycle (registered; not exercised)
--
-- DRAWING -- the global `g` (confirmed: methods called with a colon)
--   g:clear(0)                blank the WHOLE frame to white -- status bar and footer included
--   g:clear()                 ... with no argument it paints the frame BLACK
--   (no clear at all leaves the page you were started from on screen, and you paint over it)
--   g:rect(x, y, w, h)        rectangle outline  g:line(x1, y1, x2, y2)
--   g:circle(x, y, r)         circle outline     g:text(x, y, "string")
--   g:image(...)              image blit         g:layer(...)   layer select
--   g:size()                  the screen size (returns 480 first: the portrait width)
--   Coordinates are the portrait frame, 480 wide x 800 tall, origin top left, 1 bpp.
--   g:image / g:layer exist but their arguments are NOT established.
--
-- SERVICES -- the global `ctx` (confirmed present)
--   ctx.invalidate()          mark the page dirty -> on_draw runs again
--   ctx.request_refresh()     ask the display pipeline for a refresh
--   ctx.set_tick_rate(ms)     how often on_tick fires
--   ctx.save()                persist the app's data
--   ctx.quit()                leave the app
--   ctx.display.full_refresh() / .defer_auto_full() / .flush_once()
--   ctx.log.info(msg) / .warn(msg) / .error(msg) -- all three exist and all three are SILENT in
--                            7.2.4: they return without error and reach neither console nor card.
--                            Draw on screen if you need to see something.
--   ctx.sys.millis(), ctx.sys.battery(), ... (uptime_ms, epoch_sec, clock, charging, power,
--                            status, radio, network, has_key, has_bt_keyboard, has_bt_gamepad)
--   ctx.system.set_as_lockscreen_app(), ctx.system.quit()
--   ctx.screen, ctx.input, ctx.state, ctx.assets, ctx.data, ctx.fonts, ctx.i18n, ctx.layers,
--   ctx.lock, ctx.perf, ctx.gc -- present as tables; their members are NOT established.
--   Call them as ctx.log.info("x") (a dot, not a colon) -- ctx.* are plain function tables.

local taps = 0

function on_load()
  ctx.log.info('{app_id}: on_load')
end

function on_enter()
  ctx.log.info('{app_id}: on_enter')
end

function on_input(ev)
  taps = taps + 1
  ctx.invalidate()          -- ask for a repaint; on_draw follows
  return true               -- claim the event
end

function on_draw()
  g:clear(0)                -- white; g:clear() with no argument paints the frame black
  g:rect(20, 60, 440, 120)
  g:text(40, 110, '{display_name}')
  g:text(40, 150, 'taps: ' .. taps)
  g:line(20, 200, 460, 200)
  g:circle(240, 320, 90)
end
'''

MANIFEST_TEMPLATE = collections.OrderedDict((
    ('app_id', None), ('display_name', None), ('version', '1.0'), ('author', None),
    ('entry', 'index.lua'), ('permissions', list(PERMISSIONS))))


def scaffold(name, out_dir, display_name=None, author='x4pro-emu'):
    """Write a minimal manifest.json + an index.lua template into `out_dir`. Returns a report."""
    obj = collections.OrderedDict(MANIFEST_TEMPLATE)
    obj['app_id'] = name
    obj['display_name'] = display_name or name.replace('-', ' ').replace('_', ' ').title()
    obj['author'] = author
    validate_manifest(obj, 'the scaffold')
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for fname, text in (('manifest.json', json.dumps(obj, indent=2, ensure_ascii=False) + '\n'),
                        ('index.lua', TEMPLATE.format(app_id=obj['app_id'],
                                                      display_name=obj['display_name']))):
        path = os.path.join(out_dir, fname)
        if os.path.exists(path):
            raise XtappError(f'{path} exists; scaffold into an empty directory')
        with open(path, 'w') as f:
            f.write(text)
        written.append(path)
    rep = pack(out_dir)
    rep['written'] = written + [rep['out']]
    return rep


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('new', help='scaffold a Lua app directory')
    p.add_argument('name', help='the app_id (also the directory name on the card)')
    p.add_argument('out_dir')
    p.add_argument('--display-name', help='the row text on the Lua Apps page (default: NAME, titled)')
    p.add_argument('--author', default='x4pro-emu')

    p = sub.add_parser('pack', help='build APP_DIR/app.xtapp from APP_DIR/manifest.json')
    p.add_argument('app_dir')
    p.add_argument('-o', '--out', help='write the container here instead of APP_DIR/app.xtapp')

    p = sub.add_parser('info', help='print the header and manifest of an .xtapp')
    p.add_argument('file')
    p.add_argument('--json', action='store_true', help='one JSON object instead of the report')

    p = sub.add_parser('install', help='copy an app directory onto a card image')
    p.add_argument('image', help='an SD card image (tools/mksd.py: FAT32 partition at 1 MiB)')
    p.add_argument('app_dir')
    p.add_argument('--as', dest='as_dir', help='card directory name (default: the manifest app_id)')

    a = ap.parse_args(argv)
    try:
        if a.cmd == 'new':
            rep = scaffold(a.name, a.out_dir, a.display_name, a.author)
            print(f'{a.out_dir}: {rep["display_name"]} ({rep["app_id"]})')
            for w in rep['written']:
                print(f'  wrote {w}')
            print(f'  next: edit index.lua, then `xtapp.py pack {a.out_dir}` and '
                  f'`xtapp.py install SD.img {a.out_dir}` (docs/lua-apps.md)')
        elif a.cmd == 'pack':
            rep = pack(a.app_dir, a.out)
            print(f'{rep["out"]}: {rep["bytes"]} bytes = {HEADER_SIZE:#x} header + '
                  f'{rep["manifest_bytes"]} manifest\n'
                  f'  app_id {rep["app_id"]!r}, display_name {rep["display_name"]!r}, '
                  f'permissions {rep["permissions"] or "none"}')
            for w in rep['warnings']:
                print(f'  warning: {w}')
        elif a.cmd == 'info':
            head, obj = read_container(open(a.file, 'rb').read(), a.file)
            if a.json:
                print(json.dumps({'header': head, 'manifest': obj}, indent=2, ensure_ascii=False))
            else:
                print(f'{a.file}: {head["file_bytes"]} bytes')
                for k in ('magic', 'version', 'header_size', 'flags', 'manifest_offset',
                          'manifest_length', 'word_0x30', 'word_0x34'):
                    v = head[k]
                    print(f'  {k:<16} {v if isinstance(v, str) else f"{v:#x} ({v})"}')
                print('  manifest:')
                for line in json.dumps(obj, indent=2, ensure_ascii=False).splitlines():
                    print(f'    {line}')
                try:
                    validate_manifest(obj, a.file)
                except XtappError as e:
                    print(f'  warning: {e}')
        else:
            rep = install(a.image, a.app_dir, a.as_dir)
            print(f'{a.image}: {rep["card_dir"]}/ <- {a.app_dir}\n'
                  f'  {rep["display_name"]!r} ({rep["app_id"]}), '
                  f'{", ".join(rep["files"])}\n'
                  f'  on the card: {", ".join(rep["listing"])}')
            for w in rep['warnings']:
                print(f'  warning: {w}')
    except XtappError as e:
        print(f'{e}', file=sys.stderr)
        return 2
    except OSError as e:
        print(f'{e}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())

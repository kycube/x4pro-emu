#!/usr/bin/env python3
r"""Named byte patches on a stock `xteink_app` 7.2.4 image, verified before and after.

  stockpatch.py list IMAGE                  every patch and its state (applied / original / unknown)
  stockpatch.py apply IMAGE PATCH...        write the patched bytes, re-checksum, verify
  stockpatch.py revert IMAGE PATCH...       write the original bytes back, re-checksum, verify
  stockpatch.py apply IMAGE all             every patch in the manifest (`all` works for revert too)
  ... [-o OUT] [--check]                    write elsewhere / report only, change nothing

`IMAGE list` is accepted as well as `list IMAGE` (the verb may come first or second).

IMAGE is a bare app image (`images/device/stock-app0-7.2.4.bin`, 5,503,680 bytes, ESP magic 0xE9 at
0) or a whole 16 MB flash image whose app0 slot starts at 0x10000 (`tools/mkflash.py --raw` of the
device dump). `stockdev.app_base` tells the two apart and every offset below is an **app-image**
offset, so a flash image simply adds 0x10000. The ESP image arithmetic -- the XOR checksum byte
after the last segment and the appended SHA-256 the second-stage bootloader verifies -- is
`stockdev.refresh_image` / `verify_image`, imported, not copied; `tools/stockdev.py` keeps its own
CLI and is the `developer-menu` patch below in one-command form.

THE MANIFEST (`PATCHES`; each entry is name, description, app_offset, original, patched)

    | name              | app offset | app VA     | bytes                    |
    |-------------------|------------|------------|--------------------------|
    | `developer-menu`  | 0x4eabe0   | 0x4233abe0 | `..0c 02 1d f0` -> `0c 12 1d f0` |
    | `hidden-menu-rows`| 0x4f516a   | 0x4234516a | `b6 29 07` -> `b6 29 ff` |
    | `lua-apps-row`    | 0x2ed873   | 0x4213d873 | `82 02 ac` -> `82 a0 01` |

    **developer-menu** -- the developer-mode predicate at VA 0x4233abe0 is a compile-time stub
    (`entry a1,32 / movi.n a2,0 / retw.n`) and nothing in 7.2.4 makes it return anything else, so the
    light panel never shows Memory / Developer / **Screen Capture**. `movi.n a2,0` (0x02) becomes
    `movi.n a2,1` (0x12); the whole 7-byte stub is checked so the byte is not written into another
    firmware's instruction stream. Same bytes as `tools/stockdev.py` (docs/xic.md).

    **hidden-menu-rows** -- "gate A": the nav-menu builder walks menu slots 0..7 through the
    predicate at 0x42345158, whose `bltui a9,2,+7` hides slots 1 and 2. `b6 29 07` -> `b6 29 ff`
    (branch target moved past the return) unhides them: the menu grows to seven rows, **Read,
    Preload List, Statistics, All Files, USB Mode, Cloud Sync, Settings**, both new pages functional
    (docs/stock-firmware.md, "Probed in session 7").

    **lua-apps-row** -- the same builder (FUN_4213d834) skips slot 3 in its loop and appends it only
    when `presenter+172` is non-zero, which nothing ever writes. `l8ui a8,a2,172` (`82 02 ac`) ->
    `movi a8,1` (`82 a0 01`) makes the load a constant 1: the menu gains a sixth row **"Lua Apps"**
    with its own icon, entering page 0x09 (AppsPagePresenter), which lists the card's
    `/sdcard/XTApps/<dir>` apps and runs their `index.lua` (docs/lua-apps.md, `tools/xtapp.py`).

    The three are independent and combine freely (`apply IMAGE developer-menu lua-apps-row`).

WHAT `apply` / `revert` GUARANTEE
    The bytes at every named patch site are read first and must be **exactly** the original or
    exactly the patched sequence; anything else is `unknown` and the run is refused (exit 2) with
    both sequences printed, because another firmware version puts different code at that offset and
    writing there would corrupt an instruction at random. A patch already in the wanted state is a
    no-op ("already applied"). After the edit the checksum byte and the SHA-256 are recomputed and
    the image is re-parsed the way the bootloader parses it; a footer that does not verify is never
    written. `esptool image-info` is the independent check on a bare app image:

        .venv/bin/python -m esptool --chip esp32s3 image-info OUT.bin
            -> "Checksum: ... (valid)" and "Validation hash: ... (valid)"

    The image's `esp_app_desc` is read too: a project name / version other than `xteink_app` 7.2.4
    is refused before any offset is touched.

EXIT STATUS
    0  every named patch is in the wanted state (or `list --check`: all applied)
    1  `--check` only: at least one named patch is not in the wanted state (nothing was written)
    2  refused: not a stock 7.2.4 app image, unknown bytes at a patch site, or an unknown name

SAFETY
    A flash image made from the device dump still carries the owner's WiFi credentials in its NVS
    (CLAUDE.md rule 6): patch a **scratch copy**, never a file under `images/` and never the device.

        cp images/device/flash-2026-09-06-a.bin $S/flash.bin
        .venv/bin/python tools/nvsedit.py $S/flash.bin set-u8 user_config net_en 0
        .venv/bin/python tools/stockpatch.py apply $S/flash.bin lua-apps-row
        .venv/bin/python tools/x4emu --name lp1 run --flash $S/flash.bin --sd $S/sd.img \
            --boot-hold-power

`PATCHES`, `state()`, `states()`, `apply()`, `revert()` and `app_version()` are importable; `apply`
and `revert` return `(new_bytes, report)` and raise `StockPatchError` on anything unexpected.
"""
import argparse, os, sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stockdev import (FLASH_SIZE, APP0_OFF, StockDevError, app_base,           # noqa: E402,F401
                      image_layout, refresh_image, verify_image)

APP_BYTES = 5503680                     # images/device/stock-app0-7.2.4.bin (for messages only)
PROJECT, VERSION = 'xteink_app', '7.2.4'
DESC_OFF = 0x20                         # esp_app_desc: 24-byte image header + 8-byte segment header
DESC_MAGIC = 0xABCD5432
APPLIED, ORIGINAL, UNKNOWN = 'applied', 'original', 'unknown'


class StockPatchError(ValueError):
    """The image is not a stock 7.2.4 app, or a patch site does not hold the bytes it must hold."""


@dataclass(frozen=True)
class Patch:
    """One named byte patch. `app_offset` is an offset into the *app image*; a 16 MB flash image
    adds `app_base` (0x10000). `original` and `patched` are equal-length byte strings and include
    enough context (a whole instruction, or the stub around it) to identify the site."""
    name: str
    description: str
    app_offset: int
    original: bytes
    patched: bytes
    va: int = 0                          # the app virtual address of app_offset (documentation)
    detail: str = ''                     # what the changed instruction does
    docs: str = ''

    def __post_init__(self):
        assert len(self.original) == len(self.patched) and self.original != self.patched, self.name

    @property
    def size(self):
        return len(self.original)

    def window(self, data, base=0):
        off = base + self.app_offset
        return bytes(data[off:off + self.size])

    def state(self, data, base=0):
        """'applied', 'original' or 'unknown' for the bytes at this site."""
        got = self.window(data, base)
        return {self.patched: APPLIED, self.original: ORIGINAL}.get(got, UNKNOWN)


PATCHES = {p.name: p for p in (
    Patch(name='developer-menu',
          description='developer mode: Memory / Developer / Screen Capture in the light panel',
          app_offset=0x4eabe0, va=0x4233abe0,
          original=bytes((0x36, 0x41, 0x00, 0x0c, 0x02, 0x1d, 0xf0)),
          patched=bytes((0x36, 0x41, 0x00, 0x0c, 0x12, 0x1d, 0xf0)),
          detail='movi.n a2,0 -> movi.n a2,1 in the developer-mode predicate stub (byte +4)',
          docs='docs/xic.md; the same bytes as tools/stockdev.py'),
    Patch(name='hidden-menu-rows',
          description='nav menu: unhide the Preload List and Statistics rows (gate A)',
          app_offset=0x4f516a, va=0x4234516a,
          original=bytes((0xb6, 0x29, 0x07)), patched=bytes((0xb6, 0x29, 0xff)),
          detail='bltui a9,2,+7 -> bltui a9,2,+255: menu slots 1 and 2 stop being skipped',
          docs='docs/stock-firmware.md, "Probed in session 7"'),
    Patch(name='lua-apps-row',
          description='nav menu: a sixth row "Lua Apps" that opens the Lua app list (page 0x09)',
          app_offset=0x2ed873, va=0x4213d873,
          original=bytes((0x82, 0x02, 0xac)), patched=bytes((0x82, 0xa0, 0x01)),
          detail='l8ui a8,a2,172 -> movi a8,1 in the nav-menu builder: slot 3 is always appended',
          docs='docs/lua-apps.md'),
)}


# ---------------------------------------------------------------- reading an image
def app_version(data, base=0):
    """(project_name, version) from the image's `esp_app_desc`, or (None, None) when the descriptor
    magic is not there."""
    off = base + DESC_OFF
    if len(data) < off + 0x60 or int.from_bytes(bytes(data[off:off + 4]), 'little') != DESC_MAGIC:
        return None, None
    def s(at):
        raw = bytes(data[off + at:off + at + 32])
        return raw.split(b'\0', 1)[0].decode('utf-8', 'replace')
    return s(0x30), s(0x10)


def check_image(data, base=None):
    """Parse `data` as a stock 7.2.4 app or flash image; returns (base, layout). Raises
    StockPatchError when it is another firmware -- before any offset is written."""
    try:
        base = app_base(data) if base is None else base
        lay = image_layout(data, base)
    except StockDevError as e:
        raise StockPatchError(str(e)) from None
    project, version = app_version(data, base)
    if (project, version) != (PROJECT, VERSION):
        raise StockPatchError(
            f'the image at {base:#x} is {project or "?"} {version or "?"}, not {PROJECT} {VERSION}: '
            f'every offset in this tool was found in stock 7.2.4 ({APP_BYTES} bytes) and means '
            f'nothing in another build -- find the sites again (tools/appdis.py, tools/ghidra_stock.py) '
            f'before patching anything')
    end = lay['end'] - base
    for p in PATCHES.values():
        if p.app_offset + p.size > end:
            raise StockPatchError(f'the image ends at app offset {end:#x}, before the '
                                  f'{p.name} site at {p.app_offset:#x}')
    return base, lay


def resolve(names):
    """['all'] or a list of patch names -> the Patch objects, in manifest order."""
    if not names:
        raise StockPatchError('no patch named (try `all`, or ' + ', '.join(PATCHES) + ')')
    if list(names) == ['all']:
        return list(PATCHES.values())
    for n in names:
        if n not in PATCHES:
            raise StockPatchError(f'unknown patch {n!r}: known names are ' + ', '.join(PATCHES) +
                                  ' (or `all`)')
    return [p for n, p in PATCHES.items() if n in set(names)]      # always in manifest order


def state(data, name, base=None):
    """'applied' / 'original' / 'unknown' for one named patch."""
    base, _ = check_image(data, base)
    return PATCHES[name].state(data, base)


def states(data, base=None):
    """{name: {'state', 'offset', 'bytes', ...}} for every patch in the manifest."""
    base, _ = check_image(data, base)
    out = {}
    for p in PATCHES.values():
        out[p.name] = {'state': p.state(data, base), 'offset': base + p.app_offset,
                       'app_offset': p.app_offset, 'va': p.va, 'bytes': p.window(data, base).hex(' '),
                       'original': p.original.hex(' '), 'patched': p.patched.hex(' '),
                       'description': p.description}
    return out


# ---------------------------------------------------------------- writing an image
def _write(data, names, want, verb):
    """The shared body of apply/revert. `want` is the byte string attribute to end up with."""
    data = bytearray(data)
    base, _ = check_image(data)
    patches = resolve(names)
    changes = []
    for p in patches:
        st = p.state(data, base)
        if st == UNKNOWN:
            raise StockPatchError(
                f'{p.name}: the bytes at {base + p.app_offset:#x} (app {p.app_offset:#x}, VA '
                f'{p.va:#x}) are {p.window(data, base).hex(" ")}, expected {p.original.hex(" ")} '
                f'(original) or {p.patched.hex(" ")} (patched) -- refusing to {verb} it')
        new = getattr(p, want)
        off = base + p.app_offset
        was = p.window(data, base)
        data[off:off + p.size] = new
        changes.append({'name': p.name, 'offset': off, 'app_offset': p.app_offset, 'va': p.va,
                        'was': st, 'now': p.state(data, base), 'changed': was != new,
                        'from': was.hex(' '), 'to': new.hex(' '), 'detail': p.detail})
    lay = refresh_image(data, base)
    ok_c, ok_h = verify_image(data, base)
    if not (ok_c and ok_h):                                          # never seen; a bug if it fires
        raise StockPatchError(f'the re-checksummed image does not verify (checksum_ok={ok_c}, '
                              f'hash_ok={ok_h}) -- refusing to write it')
    return bytes(data), {
        'base': base, 'changes': changes,
        'checksum_offset': lay['checksum_off'], 'checksum': data[lay['checksum_off']],
        'hash_offset': lay['hash_off'],
        'sha256': bytes(data[lay['hash_off']:lay['hash_off'] + 32]).hex()
        if lay['hash_off'] is not None else None,
        'checksum_ok': ok_c, 'hash_ok': ok_h}


def apply(data, names):
    """Apply the named patches (or `['all']`) to the bytes of a stock 7.2.4 app or flash image.
    Returns (new bytes, report); raises StockPatchError rather than writing over unknown bytes."""
    return _write(data, names, 'patched', 'apply')


def revert(data, names):
    """The inverse of `apply`: put the original bytes back."""
    return _write(data, names, 'original', 'revert')


# ---------------------------------------------------------------- CLI
def _kind(data, base):
    return ('16 MB flash image, app0 at 0x10000' if base else 'bare app image') + f', {len(data)} bytes'


def _do_list(a, data):
    base, _ = check_image(data)
    ok_c, ok_h = verify_image(data, base)
    rows = states(data, base)
    print(f'{a.image}: {_kind(data, base)}\n'
          f'  checksum {"valid" if ok_c else "INVALID"}, '
          f'{"hash " + ("valid" if ok_h else "INVALID") if image_layout(data, base)["hash_off"] else "no appended hash"}')
    width = max(len(n) for n in rows)
    for name, r in rows.items():
        print(f'  {name:<{width}}  {r["state"]:<8} at {r["offset"]:#09x} '
              f'(app {r["app_offset"]:#x}, VA {r["va"]:#x}): {r["bytes"]}')
        print(f'  {"":<{width}}  {r["description"]}')
        if r['state'] == UNKNOWN:
            print(f'  {"":<{width}}  expected {r["original"]} (original) or {r["patched"]} (patched)')
    values = [r['state'] for r in rows.values()]
    if not a.check:
        return 0
    return 2 if UNKNOWN in values else (0 if all(v == APPLIED for v in values) else 1)


def _do_write(a, data, verb):
    base, _ = check_image(data)
    patches = resolve(a.patches)
    want = 'patched' if verb == 'apply' else 'original'
    target = APPLIED if verb == 'apply' else ORIGINAL
    print(f'{a.image}: {_kind(data, base)}')
    if a.check:
        rc = 0
        for p in patches:
            st = p.state(data, base)
            print(f'  {p.name}: {st}' + ('' if st == target else f' -> would {verb}'))
            rc = 2 if st == UNKNOWN else max(rc, 0 if st == target else 1)
        print('  --check: nothing written')
        return rc

    out, rep = _write(data, a.patches, want, verb)
    for c in rep['changes']:
        print(f'  {c["name"]}: {c["from"]} -> {c["to"]} at {c["offset"]:#x} '
              f'(app {c["app_offset"]:#x}, VA {c["va"]:#x})'
              + ('' if c['changed'] else f'   [already {target}]'))
        print(f'      {c["detail"]}')
    print(f'  checksum {rep["checksum"]:#04x} at {rep["checksum_offset"]:#x} '
          f'({"valid" if rep["checksum_ok"] else "INVALID"})')
    if rep['hash_offset'] is not None:
        print(f'  sha-256 {rep["sha256"]} at {rep["hash_offset"]:#x} '
              f'({"valid" if rep["hash_ok"] else "INVALID"})')
    dst = a.out or a.image
    with open(dst, 'wb') as f:
        f.write(out)
    print(f'  wrote {dst} ({len(out)} bytes)')
    if rep['base'] == APP0_OFF or len(out) == FLASH_SIZE:
        print("  note: a flash image made from the device dump still holds the owner's WiFi "
              'credentials (CLAUDE.md rule 6) -- keep it in scratch')
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # `stockpatch.py IMAGE list` is accepted as well as `stockpatch.py list IMAGE`
    if len(argv) >= 2 and argv[1] in ('list', 'apply', 'revert') and argv[0] not in ('list', 'apply', 'revert'):
        argv[0], argv[1] = argv[1], argv[0]

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    names = ', '.join(PATCHES) + ', all'
    for verb, helptext in (('list', 'print every patch and its state'),
                           ('apply', 'write the patched bytes'),
                           ('revert', 'write the original bytes back')):
        p = sub.add_parser(verb, help=helptext, description=__doc__,
                           formatter_class=argparse.RawDescriptionHelpFormatter)
        p.add_argument('image', help='a stock 7.2.4 app image, or a 16 MB flash image (app0 at 0x10000)')
        if verb != 'list':
            p.add_argument('patches', nargs='+', metavar='PATCH', help=names)
            p.add_argument('-o', '--out', help='write here instead of editing IMAGE in place')
        p.add_argument('--check', action='store_true',
                       help='report only, write nothing (exit 0 = already in the wanted state, 1 = not)')
    a = ap.parse_args(argv)

    try:
        data = bytearray(open(a.image, 'rb').read())
        return _do_list(a, data) if a.cmd == 'list' else _do_write(a, data, a.cmd)
    except (StockPatchError, StockDevError) as e:
        print(f'{a.image}: {e}', file=sys.stderr)
        return 2
    except OSError as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

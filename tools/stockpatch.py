#!/usr/bin/env python3
r"""Named byte patches on a stock `xteink_app` image, verified before and after.

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
`stockdev.reseal` (`refresh_image` + `verify_image`), imported, not copied; `tools/stockdev.py` keeps its own
CLI and is the `developer-menu` patch below in one-command form.

THE MANIFEST IS PER VERSION (tools/stockver.py)
    A patch site belongs to one build, not to this tool: the manifest lives in
    `tools/stockver.d/<version>.json` (`patches`), one file per stock version, and the image picks
    its own by what its `esp_app_desc` says (project + version, with `app_elf_sha256` as the
    identity of the build). `stockver.identify` does the picking and the hash check;
    `tools/stockver.py versions` lists what is known and its module docstring is the schema. A
    version with no file is refused, and so is a patch name that version's file does not carry --
    "not located in this version" is a safe answer, a guessed offset is not. `PATCHES` here is the
    manifest of the baseline version (7.2.4) for scripts that want the table without an image;
    `patches_for(data)` is the manifest of a given image.

    Stock 7.2.4 (`tools/stockver.d/7.2.4.json`), as one version among several:

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

    The image's `esp_app_desc` is read first: a project name / version no `tools/stockver.d/` file
    describes, or an `app_elf_sha256` that is not the one that file names, is refused before any
    offset is touched (our patches change an image but never its ELF hash).

EXIT STATUS
    0  every named patch is in the wanted state (or `list --check`: all applied)
    1  `--check` only: at least one named patch is not in the wanted state (nothing was written)
    2  refused: an unknown firmware version, unknown bytes at a patch site, or an unknown name

SAFETY
    A flash image made from the device dump still carries the owner's WiFi credentials in its NVS
    (CLAUDE.md rule 6): patch a **scratch copy**, never a file under `images/` and never the device.

        cp images/device/flash-2026-09-06-a.bin $S/flash.bin
        .venv/bin/python tools/nvsedit.py $S/flash.bin set-u8 user_config net_en 0
        .venv/bin/python tools/stockpatch.py apply $S/flash.bin lua-apps-row
        .venv/bin/python tools/x4emu --name lp1 run --flash $S/flash.bin --sd $S/sd.img \
            --boot-hold-power

`PATCHES`, `patches_for()`, `state()`, `states()`, `apply()`, `revert()` and `app_version()` are
importable; `apply` and `revert` return `(new_bytes, report)` and raise `StockPatchError` on
anything unexpected.
"""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stockver                                                                # noqa: E402
from stockdev import (FLASH_SIZE, APP0_OFF, CREDENTIALS_NOTE, StockDevError,   # noqa: E402
                      footer_lines, footer_status, image_layout, reseal)

APPLIED, ORIGINAL, UNKNOWN = 'applied', 'original', 'unknown'


class StockPatchError(ValueError):
    """The image is not a stock app of a known version, or a patch site does not hold the bytes the
    version file says it must hold."""


def _baseline_patches():
    """The manifest of the baseline version, for scripts that want the table without an image.
    Empty (never a guess) when tools/stockver.d/ has no file for it."""
    ref = stockver.baseline()
    return dict(ref.patches) if ref else {}


PATCHES = _baseline_patches()           # tools/stockver.d/7.2.4.json, the reference manifest


def patches_for(data, base=None):
    """The patch manifest of *this* image's version: {name: Patch}, in file order."""
    return dict(version_of(data, base)[1].patches)


# ---------------------------------------------------------------- reading an image
def app_version(data, base=0):
    """(project_name, version) from the image's `esp_app_desc`, or (None, None) when the descriptor
    magic is not there."""
    desc = stockver.app_desc(data, base)
    return (desc['project'], desc['version']) if desc else (None, None)


def version_of(data, base=None):
    """(base, stockver.Version) for an image, with this tool's wording on a refusal."""
    return stockver.identify_as(
        data, base, StockPatchError,
        lambda ref: f' -- not {ref.label} ({ref.app_bytes} bytes) and not any other version in '
                    f'tools/stockver.d/: every offset in this tool was found per version and means '
                    f'nothing in another build, so find the sites again (tools/appdis.py, '
                    f'tools/ghidra_stock.py) before patching anything')


def check_image(data, base=None):
    """Parse `data` as a stock app or flash image of a known version; returns (base, layout,
    version). Raises StockPatchError when it is another firmware -- before any offset is written."""
    try:
        base, ver = version_of(data, base)
        lay = image_layout(data, base)
    except StockDevError as e:
        raise StockPatchError(str(e)) from None
    end = lay['end'] - base
    for p in ver.patches.values():
        if p.app_offset + p.size > end:
            raise StockPatchError(f'the image ends at app offset {end:#x}, before the '
                                  f'{p.name} site at {p.app_offset:#x}')
    return base, lay, ver


def resolve(names, patches=None):
    """['all'] or a list of patch names -> the Patch objects, in manifest order. `patches` is the
    manifest of the image at hand (the baseline manifest when it is left out)."""
    patches = PATCHES if patches is None else patches
    if not patches:
        raise StockPatchError('this version has no patch sites: ' + stockver.where_to_add())
    if not names:
        raise StockPatchError('no patch named (try `all`, or ' + ', '.join(patches) + ')')
    if list(names) == ['all']:
        return list(patches.values())
    for n in names:
        if n not in patches:
            raise StockPatchError(f'unknown patch {n!r}: known names are ' + ', '.join(patches) +
                                  ' (or `all`)')
    return [p for n, p in patches.items() if n in set(names)]      # always in manifest order


def state(data, name, base=None):
    """'applied' / 'original' / 'unknown' for one named patch."""
    base, _, ver = check_image(data, base)
    return ver.need_patch(name).state(data, base)


def states(data, base=None):
    """{name: {'state', 'offset', 'bytes', ...}} for every patch in the manifest."""
    base, _, ver = check_image(data, base)
    out = {}
    for p in ver.patches.values():
        out[p.name] = {'state': p.state(data, base), 'offset': base + p.app_offset,
                       'app_offset': p.app_offset, 'va': p.va, 'bytes': p.window(data, base).hex(' '),
                       'original': p.original.hex(' '), 'patched': p.patched.hex(' '),
                       'description': p.description}
    return out


# ---------------------------------------------------------------- writing an image
def _write(data, names, want, verb):
    """The shared body of apply/revert. `want` is the byte string attribute to end up with."""
    data = bytearray(data)
    base, _, ver = check_image(data)
    patches = resolve(names, ver.patches)
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
    footer = reseal(data, base, StockPatchError)
    return bytes(data), {'base': base, 'version': ver.version, 'project': ver.project,
                         'changes': changes, **footer}


def apply(data, names):
    """Apply the named patches (or `['all']`) to the bytes of a stock app or flash image of a
    known version. Returns (new bytes, report); raises StockPatchError rather than writing over
    unknown bytes."""
    return _write(data, names, 'patched', 'apply')


def revert(data, names):
    """The inverse of `apply`: put the original bytes back."""
    return _write(data, names, 'original', 'revert')


# ---------------------------------------------------------------- CLI
def _do_list(a, data):
    base, _, ver = check_image(data)
    rows = states(data, base)
    if not rows:
        print(f'{a.image}: {stockver.image_kind(data, base, ver)}\n'
              f'  no patch sites are located in {ver.label} ({ver.file})')
        return 2 if a.check else 0
    print(f'{a.image}: {stockver.image_kind(data, base, ver)}\n  {footer_status(data, base)}')
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
    base, _, ver = check_image(data)
    patches = resolve(a.patches, ver.patches)
    want = 'patched' if verb == 'apply' else 'original'
    target = APPLIED if verb == 'apply' else ORIGINAL
    print(f'{a.image}: {stockver.image_kind(data, base, ver)}')
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
    print('\n'.join(footer_lines(rep)))
    dst = a.out or a.image
    with open(dst, 'wb') as f:
        f.write(out)
    print(f'  wrote {dst} ({len(out)} bytes)')
    if rep['base'] == APP0_OFF or len(out) == FLASH_SIZE:
        print(CREDENTIALS_NOTE)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # `stockpatch.py IMAGE list` is accepted as well as `stockpatch.py list IMAGE`
    if len(argv) >= 2 and argv[1] in ('list', 'apply', 'revert') and argv[0] not in ('list', 'apply', 'revert'):
        argv[0], argv[1] = argv[1], argv[0]

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    names = ', '.join(PATCHES) + ', all'      # the baseline names; the image's own manifest decides
    for verb, helptext in (('list', 'print every patch and its state'),
                           ('apply', 'write the patched bytes'),
                           ('revert', 'write the original bytes back')):
        p = sub.add_parser(verb, help=helptext, description=__doc__,
                           formatter_class=argparse.RawDescriptionHelpFormatter)
        p.add_argument('image', help='a stock app image of a known version '
                                      '(tools/stockver.py versions), or a 16 MB flash image')
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

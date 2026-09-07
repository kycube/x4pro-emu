#!/usr/bin/env python3
r"""Enable developer mode in a stock `xteink_app` image (one byte + a re-checksum).

  stockdev.py IMAGE                  patch IMAGE in place, recompute checksum + SHA-256, verify
  stockdev.py IMAGE -o OUT.bin       write the patched image to OUT.bin, leave IMAGE alone
  stockdev.py IMAGE --check          report only: patched / unpatched / unknown, change nothing

IMAGE is either a bare app image (`images/device/stock-app0-7.2.4.bin`, 5,503,680 bytes, ESP magic
0xE9 at 0) or a whole 16 MB flash image whose app0 slot starts at 0x10000 (`tools/mkflash.py --raw`
of the device dump); which one it is is detected from the size and from where the 0xE9 sits. Exit
status: 0 = developer mode is on (patched now or already), 1 = `--check` on an unpatched image,
2 = refused (an unknown firmware version, or the stub does not match).

WHICH VERSION (tools/stockver.py)
    The offset of the stub is **not** a property of this tool: it belongs to one build, and every
    number below lives in `tools/stockver.d/<version>.json` (`dev_stub`). The image says what it is
    in its `esp_app_desc` (project + version, and `app_elf_sha256` as the identity of the build);
    `stockver.identify` picks the file, checks the ELF hash and hands over the stub. A version with
    no version file -- or one whose file has no `dev_stub` because the site has not been found in it
    yet -- is refused, never guessed. 7.2.4 is one version among several:

        | version | app offset | app VA     | stub                    | byte +4  |
        |---------|------------|------------|-------------------------|----------|
        | 7.2.4   | 0x4eabe0   | 0x4233abe0 | `36 41 00 0c 02 1d f0`  | 02 -> 12 |

    `tools/stockver.py versions` lists what is known; its module docstring is the schema.

WHY A PATCH (docs/xic.md, "How developer mode is enabled")
    The pull-down light panel gains its Memory / Developer / **Screen Capture** buttons only when the
    app's developer-mode predicate returns non-zero, and in 7.2.4 that predicate is a compile-time
    **stub that unconditionally returns 0**:

        app VA 0x4233abe0:  entry a1,32          36 41 00      file offset 0x4eabe0
               0x4233abe4:  movi.n a2, 0         0c 02          <- the byte this tool changes
               0x4233abe6:  retw.n               1d f0

    There is no NVS key, no card file and no gesture that flips it (the "tap Firmware Version" reveal
    on About Device unlocks Diagnostics Test, a different mechanism). So the only way to a `.xic`
    screen capture in the emulator is to make the stub return 1: `movi.n a2,0` (0x0c 0x02) becomes
    `movi.n a2,1` (0x0c 0x12) -- one byte at file offset 0x4eabe4, 0x10000 + 0x4eabe4 in a flash
    image. The patched app boots normally (boot-preflight `decision=0`, hash accepted) and reaches
    Home; `tests/test_stock_capture.py` drives it all the way to a capture.

WHAT ELSE HAS TO BE FIXED UP (ESP-IDF image format)
    The app image is header(24) + N x [segment header(8) + payload] + padding + checksum byte
    [+ SHA-256]. Header byte 1 is the segment count, byte 23 is the "hash appended" flag. The
    checksum is the XOR of every segment payload byte seeded with 0xEF and sits in the last byte of
    the 16-byte-aligned block that follows the last segment (file offset 0x53fa9f in 7.2.4); when
    byte 23 is 1, a SHA-256 of everything before it is appended (0x53faa0..0x53fabf), which is what
    the second-stage bootloader verifies. Both are recomputed here and re-parsed afterwards, so the
    tool refuses to leave an image the bootloader would reject. `esptool` is the independent check:

        .venv/bin/python -m esptool --chip esp32s3 image-info OUT.bin
            -> "Checksum: 0x99 (valid)" and "Validation hash: ... (valid)"

    (that only works on a bare app image; for a flash image, slice 0x10000 out first, or just trust
    the tool's own re-parse, which is the same arithmetic).

SAFETY
    A patched image made from the device dump **still carries the owner's WiFi credentials** in its
    NVS (a flash image) and is still the owner's app (either form): CLAUDE.md rule 6 -- keep it in a
    scratch directory, never under `images/` or in the repository, and never on the real device. This
    is an emulator-only convenience for producing an oracle; the device stays untouched.

    .venv/bin/python tools/nvsedit.py SCRATCH/flash.bin set-u8 user_config net_en 0
    .venv/bin/python tools/stockdev.py SCRATCH/flash.bin        # 0x10000 + 0x4eabe4
    .venv/bin/python tools/x4emu --name xc run --flash SCRATCH/flash.bin --sd SCRATCH/sd.img \
        --boot-hold-power

`stub_state(data, base)`, `patch(data)` and `refresh_image(data, base)` are importable; `patch`
returns `(new_bytes, report_dict)` and raises `StockDevError` on anything that is not a stock stub
of a known version.
"""
import argparse, hashlib, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stockver                                                              # noqa: E402

FLASH_SIZE = 0x1000000
APP0_OFF = 0x10000              # the stock partition table's app0 slot (tools/mkflash.py)
ESP_MAGIC = 0xE9
CHECKSUM_SEED = 0xEF
HASH_BYTES = 32
# the developer stub itself (offset, bytes, the immediate) is per version: tools/stockver.d/*.json


class StockDevError(ValueError):
    """The image is not a stock app of a known version (bad ESP header, an unknown firmware, or the
    developer stub is not where this version's file says it is)."""


# ---------------------------------------------------------------- ESP image arithmetic
def image_layout(data, base=0):
    """{'segments': [(offset, length)], 'checksum_off', 'hash_off'|None, 'end'} for the ESP-IDF
    application image starting at `base`. Offsets are absolute in `data`."""
    if len(data) < base + 24 or data[base] != ESP_MAGIC:
        raise StockDevError(f'no ESP image magic {ESP_MAGIC:#04x} at {base:#x}')
    count, hash_appended = data[base + 1], data[base + 23]
    off, segments = base + 24, []
    for i in range(count):
        if off + 8 > len(data):
            raise StockDevError(f'segment {i} header runs past the end of the image')
        _load, length = struct.unpack_from('<II', data, off)
        off += 8
        if off + length > len(data):
            raise StockDevError(f'segment {i} ({length} bytes at {off:#x}) runs past the end')
        segments.append((off, length))
        off += length
    checksum_off = off + (16 - off % 16 - 1) % 16       # last byte of the 16-byte-aligned block
    hash_off = checksum_off + 1 if hash_appended == 1 else None
    end = (hash_off + HASH_BYTES) if hash_off is not None else checksum_off + 1
    if end > len(data):
        raise StockDevError(f'image footer at {checksum_off:#x} runs past the end of the file')
    return {'segments': segments, 'checksum_off': checksum_off, 'hash_off': hash_off, 'end': end,
            'base': base}


def checksum(data, layout):
    """XOR of every segment payload byte, seeded 0xEF -- the byte the bootloader compares."""
    c = CHECKSUM_SEED
    for off, length in layout['segments']:
        for b in data[off:off + length]:
            c ^= b
    return c


def refresh_image(data, base=0):
    """Rewrite the checksum byte and the appended SHA-256 of the image at `base`. `data` is a
    bytearray, edited in place; returns the layout."""
    lay = image_layout(data, base)
    data[lay['checksum_off']] = checksum(data, lay)
    if lay['hash_off'] is not None:
        data[lay['hash_off']:lay['hash_off'] + HASH_BYTES] = \
            hashlib.sha256(bytes(data[base:lay['hash_off']])).digest()
    return lay


def verify_image(data, base=0):
    """(checksum_ok, hash_ok) by re-parsing the image the way the second-stage bootloader does."""
    lay = image_layout(data, base)
    ok_c = data[lay['checksum_off']] == checksum(data, lay)
    ok_h = True
    if lay['hash_off'] is not None:
        want = hashlib.sha256(bytes(data[base:lay['hash_off']])).digest()
        ok_h = bytes(data[lay['hash_off']:lay['hash_off'] + HASH_BYTES]) == want
    return ok_c, ok_h


# ---------------------------------------------------------------- the patch itself
def app_base(data):
    """0 for a bare app image, 0x10000 for a 16 MB flash image. Raises StockDevError otherwise."""
    if len(data) >= APP0_OFF + 24 and (len(data) == FLASH_SIZE or data[0] != ESP_MAGIC) \
            and data[APP0_OFF] == ESP_MAGIC:
        return APP0_OFF
    if data[:1] == bytes((ESP_MAGIC,)):
        return 0
    raise StockDevError(f'{len(data)} bytes with no ESP image magic {ESP_MAGIC:#04x} at 0 or at '
                        f'{APP0_OFF:#x}: not a stock app image and not a 16 MB flash image')


def version_of(data, base=None):
    """(base, stockver.Version) for an image, with this tool's wording on a refusal.

    Every offset this tool uses comes from the version file `stockver.identify` picks, so an
    unknown build is refused here, before any offset is read."""
    try:
        return stockver.identify(data, base)
    except stockver.UnknownVersion as e:
        ref = stockver.baseline()
        tail = (f' -- it is not stock {ref.label} ({ref.app_bytes} bytes) either, and another '
                f'version has the developer-mode predicate at a different offset: find it again '
                f'with tools/appdis.py / tools/ghidra_stock.py before patching anything'
                if ref else '')
        raise StockDevError(f'{e}{tail}') from None
    except stockver.StockVerError as e:
        raise StockDevError(str(e)) from None


def stub_state(data, base=0, ver=None):
    """'patched', 'unpatched' or 'unknown' for the developer-mode stub of the image at `base`.
    The stub is this version's (`ver`, else read from the image's `esp_app_desc`)."""
    stub = (ver or version_of(data, base)[1]).need_dev_stub()
    off = base + stub.app_offset
    got = bytes(data[off:off + stub.size])
    for value, name in ((stub.unpatched, 'unpatched'), (stub.patched, 'patched')):
        if got == stub.variant(value):
            return name
    return 'unknown'


def patch(data):
    """Enable developer mode in the bytes of a stock app or flash image of a known version.

    Returns (patched bytes, report). The report carries base, version, stub_offset, movi_offset,
    `was` and `now` (the stub state before/after), checksum_offset, checksum, hash_offset, sha256
    and the (checksum_ok, hash_ok) of the re-parse. Raises StockDevError when the image is not a
    version tools/stockver.d/ describes, or when the stub is not the one that version's file
    names -- blindly writing the patched byte there would corrupt whatever instruction lives at
    that offset."""
    data = bytearray(data)
    base, ver = version_of(data)
    stub = ver.need_dev_stub()
    lay = image_layout(data, base)
    if base + stub.app_offset + stub.size > lay['end']:
        raise StockDevError(f'the image at {base:#x} ends at {lay["end"]:#x}, before the developer '
                            f'stub at {base + stub.app_offset:#x}: not a stock {ver.label} app')
    was = stub_state(data, base, ver)
    if was == 'unknown':
        got = bytes(data[base + stub.app_offset:base + stub.app_offset + stub.size])
        raise StockDevError(
            f'the developer-mode stub is not at {base + stub.app_offset:#x}: found {got.hex(" ")}, '
            f'expected {stub.stub.hex(" ")} (or the patched '
            f'{stub.variant(stub.patched).hex(" ")}). The image calls itself {ver.label} but does '
            f'not hold the bytes {ver.file} names for it; this is not stock {ver.label} '
            f'({ver.app_bytes} bytes) as it was found -- find the predicate again with '
            f'tools/appdis.py before patching anything')
    data[base + stub.movi_offset] = stub.patched
    lay = refresh_image(data, base)
    ok_c, ok_h = verify_image(data, base)
    if not (ok_c and ok_h):                                          # never seen; a bug if it fires
        raise StockDevError(f'the re-checksummed image does not verify (checksum_ok={ok_c}, '
                            f'hash_ok={ok_h}) -- refusing to write it')
    return bytes(data), {
        'base': base, 'version': ver.version, 'project': ver.project, 'va': stub.va,
        'stub_offset': base + stub.app_offset, 'movi_offset': base + stub.movi_offset,
        'was': was, 'now': stub_state(data, base, ver), 'checksum_offset': lay['checksum_off'],
        'checksum': data[lay['checksum_off']], 'hash_offset': lay['hash_off'],
        'sha256': bytes(data[lay['hash_off']:lay['hash_off'] + HASH_BYTES]).hex()
        if lay['hash_off'] is not None else None,
        'checksum_ok': ok_c, 'hash_ok': ok_h}


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('image', help='a stock app image of a known version (tools/stockver.py '
                                  'versions), or a 16 MB flash image (app0 at 0x10000)')
    ap.add_argument('-o', '--out', help='write here instead of editing IMAGE in place')
    ap.add_argument('--check', action='store_true',
                    help='report patched (exit 0) / unpatched (exit 1) / unknown (exit 2), write nothing')
    a = ap.parse_args(argv)

    data = bytearray(open(a.image, 'rb').read())
    kind = f'{len(data)} bytes'
    try:
        base, ver = version_of(data)
        stub = ver.need_dev_stub()
        kind = ('16 MB flash image, app0 at 0x10000' if base else 'bare app image') + \
               f', {len(data)} bytes, {ver.label}'
        if a.check:
            state = stub_state(data, base, ver)
            ok_c, ok_h = verify_image(data, base)
            print(f'{a.image}: {kind}\n  developer mode: {state}'
                  f'  (stub at {base + stub.app_offset:#x}, byte {base + stub.movi_offset:#x} = '
                  f'{data[base + stub.movi_offset]:#04x})\n'
                  f'  checksum {"valid" if ok_c else "INVALID"}, '
                  f'{"hash " + ("valid" if ok_h else "INVALID") if image_layout(data, base)["hash_off"] else "no appended hash"}')
            return {'patched': 0, 'unpatched': 1}.get(state, 2)
        out, rep = patch(data)
    except StockDevError as e:
        print(f'{a.image}: {e}', file=sys.stderr)
        return 2

    dst = a.out or a.image
    with open(dst, 'wb') as f:
        f.write(out)
    print(f'{a.image}: {kind}\n'
          f'  {rep["movi_offset"]:#x}: movi.n a2,{"0 -> 1" if rep["was"] == "unpatched" else "1 (already patched)"}'
          f'   (developer stub at {rep["stub_offset"]:#x}, app VA {rep["va"]:#x})\n'
          f'  checksum {rep["checksum"]:#04x} at {rep["checksum_offset"]:#x} '
          f'({"valid" if rep["checksum_ok"] else "INVALID"})\n'
          f'  sha-256 {rep["sha256"]} at {rep["hash_offset"]:#x} '
          f'({"valid" if rep["hash_ok"] else "INVALID"})\n'
          f'  wrote {dst} ({len(out)} bytes): developer mode is {rep["now"]}')
    if rep['base'] == APP0_OFF or os.path.getsize(dst) == FLASH_SIZE:
        print('  note: a flash image made from the device dump still holds the owner\'s WiFi '
              'credentials (CLAUDE.md rule 6) -- keep it in scratch')
    return 0


if __name__ == '__main__':
    sys.exit(main())

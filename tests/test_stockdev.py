"""tools/stockdev.py on a scratch copy of the stock app -- no emulator, no QEMU, ~2 s.

Developer mode (and with it Screen Capture, the only way to a `.xic` screenshot) is a compile-time
stub in `xteink_app` 7.2.4: the predicate at app VA 0x4233abe0 is `entry / movi.n a2,0 / retw.n` and
nothing in the firmware ever makes it return anything else (docs/xic.md). `tools/stockdev.py` turns
that `movi.n a2,0` into `movi.n a2,1` -- one byte at file offset 0x4eabe4 -- and then has to repair
the two things the second-stage bootloader checks: the XOR checksum byte after the last segment and
the SHA-256 appended behind it. Which bytes that changes, that esptool calls the footer valid, and
that the same patch lands at +0x10000 in a flash image is tests/test_stockpatch.py's (its
`developer-menu` is this patch, byte for byte). This file covers the tool's own surface:

  * `--check` says unpatched before and patched after, with the exit codes the manual promises,
  * a second run is a no-op,
  * an image that is not stock 7.2.4 is refused instead of being corrupted at a guessed offset,
  * the importable `patch()` reports what it wrote and matches the CLI.

Needs images/device/stock-app0-7.2.4.bin (gitignored, extracted from the device dump); skips without
it, as the stock tests do on CI. Everything happens on a copy in pytest's tmp_path; nothing is
written under images/.
"""
import os, shutil, subprocess
import pytest
from conftest import ROOT, PY

import stockdev                                                                # noqa: E402

TOOL = os.path.join(ROOT, 'tools', 'stockdev.py')
APP = os.path.join(ROOT, 'images', 'device', 'stock-app0-7.2.4.bin')
CROSSPOINT = os.path.join(ROOT, 'firmware', '.pio', 'build', 'x4pro', 'firmware.bin')
APP_BYTES = 5503680
CHECKSUM_OFF = 0x53fa9f          # last byte of the 16-byte block after segment 7
HASH_OFF = 0x53faa0              # ..0x53fabf, the end of the image


def run(*args, expect=0):
    """`tools/stockdev.py ...`; returns stdout+stderr, asserting the exit code."""
    r = subprocess.run([PY, TOOL, *map(str, args)], capture_output=True, text=True)
    assert r.returncode == expect, \
        f'stockdev {args} exited {r.returncode}, wanted {expect}:\n{r.stdout}\n{r.stderr}'
    return r.stdout + r.stderr


def image_info(path):
    """esptool's own view of the image footer (the independent check on our arithmetic; also
    test_stockpatch.py's and test_stockstrings.py's)."""
    r = subprocess.run([PY, '-m', 'esptool', '--chip', 'esp32s3', 'image-info', str(path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, f'esptool image-info failed:\n{r.stdout}\n{r.stderr}'
    return r.stdout


def esptool_valid(path):
    """(checksum valid, validation hash valid) as esptool reports them. Bare app images only."""
    out = image_info(path)
    return tuple(f in out and '(valid)' in out.split(f)[1].split('\n')[0]
                 for f in ('Checksum:', 'Validation hash:'))


@pytest.fixture
def app(tmp_path):
    """A scratch copy of the stock app image."""
    if not os.path.exists(APP):
        pytest.skip('images/device/stock-app0-7.2.4.bin not available')
    dst = tmp_path / 'app.bin'
    shutil.copyfile(APP, dst)
    return dst


def test_check_reports_unpatched_then_patched_and_changes_nothing(app):
    """`--check` never writes, and its exit code is the answer: 0 patched, 1 unpatched."""
    before = open(app, 'rb').read()
    out = run(app, '--check', expect=1)
    assert 'developer mode: unpatched' in out and '0x4eabe4 = 0x02' in out, out
    assert 'checksum valid' in out and 'hash valid' in out, out
    assert open(app, 'rb').read() == before, '--check wrote to the image'

    run(app)
    out = run(app, '--check')
    assert 'developer mode: patched' in out and '0x4eabe4 = 0x12' in out, out
    assert 'bare app image' in out, out


def test_patching_twice_is_idempotent(app, tmp_path):
    """The second run says "already patched" and leaves the file byte for byte the same."""
    run(app)
    once = open(app, 'rb').read()
    out = run(app)
    assert 'already patched' in out, out
    assert open(app, 'rb').read() == once


def test_a_foreign_image_is_refused(app, tmp_path):
    """Another firmware puts the predicate somewhere else, so writing 0x12 at 0x4eabe4 would corrupt
    a random instruction: the tool refuses (exit 2) instead, for both `--check` and a patch run."""
    # a stock app whose stub bytes were altered: the offset is right, the code is not
    fake = tmp_path / 'fake.bin'
    blob = bytearray(open(app, 'rb').read())
    blob[0x4eabe0] = 0x37                                    # not `entry a1,32` any more
    fake.write_bytes(blob)
    for args in ((fake,), (fake, '--check')):
        out = run(*args, expect=2)
        assert 'developer' in out and ('unknown' in out or 'not stock' in out.lower()), out
    assert open(fake, 'rb').read() == bytes(blob), 'a refused image must not be written'

    # CrossPoint's own build: a valid ESP32-S3 app image that is simply not xteink_app 7.2.4
    if os.path.exists(CROSSPOINT):
        out = run(CROSSPOINT, expect=2)
        assert 'not stock xteink_app 7.2.4' in out, out
        assert 'appdis.py' in out, 'the message should say how to find the predicate again'

    # not an ESP image at all
    junk = tmp_path / 'junk.bin'
    junk.write_bytes(b'not an esp image' * 64)
    assert 'no ESP image magic' in run(junk, expect=2)


def test_the_importable_api_matches_the_cli(app):
    """`patch()` returns the offsets and the footer it wrote; `stub_state` names the three cases."""
    blob = open(app, 'rb').read()
    assert stockdev.app_base(bytearray(blob)) == 0
    assert stockdev.stub_state(blob) == 'unpatched'

    out, rep = stockdev.patch(blob)
    assert (rep['base'], rep['stub_offset'], rep['movi_offset']) == (0, 0x4eabe0, 0x4eabe4)
    assert (rep['was'], rep['now']) == ('unpatched', 'patched')
    assert (rep['checksum_offset'], rep['hash_offset']) == (CHECKSUM_OFF, HASH_OFF)
    assert rep['checksum_ok'] and rep['hash_ok']
    assert out[HASH_OFF:HASH_OFF + 32].hex() == rep['sha256']
    assert stockdev.stub_state(out) == 'patched'

    run(app)                                                 # the CLI produces the same bytes
    assert open(app, 'rb').read() == out

    with pytest.raises(stockdev.StockDevError, match='not at 0x4eabe0'):
        stockdev.patch(blob[:0x4eabe0] + b'\x37' + blob[0x4eabe1:])

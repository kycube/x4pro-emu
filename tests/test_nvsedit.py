"""tools/nvsedit.py: `set-str`, `erase` and `redact` on a scratch copy of the device dump.

`redact` is what makes a stock flash image shareable (docs/NEXT_PHASE.md §3.2 step 8): it erases
user_config/sta_ssid, sta_pwd and wifi_creds -- the owner's WiFi credentials, which are the reason
images/device/flash-*.bin never leaves images/ (CLAUDE.md, device rule 6) -- and sets user_config/net_en
to 0, which is also what keeps the stock off the radio. This module never prints or stores a credential
value: it locates the three entries by key before the edit and checks afterwards that their 32-byte
slots read 0xff and that no entry with those keys is left.

The last test boots the redacted image the way tests/test_stock.py boots the dump (its `stock_card`
fixture, its `boot_to_home` and `masked_diff` are imported from there) and compares Home with
tests/golden/stock-home.png outside the status bar, so the redaction is proven not to cost the stock
its NVS: an image edited this way still boots to its home screen. Skips without the dump, the device
card files (images/device/sd-files), the built QEMU or the golden.
"""
import os, shutil, struct, subprocess, sys, pytest
from conftest import x4emu, ROOT, PY, QEMU
from test_stock import DUMP, GOLDEN, boot_to_home, masked_diff, stock_card      # noqa: F401 (fixture)

sys.path.insert(0, os.path.join(ROOT, 'tools'))
import nvsedit                                                                 # noqa: E402

NVSEDIT = os.path.join(ROOT, 'tools', 'nvsedit.py')
NS = 'user_config'
CREDS = ('sta_ssid', 'sta_pwd', 'wifi_creds')


def nvs(image):
    return nvsedit.Nvs(open(image, 'rb').read(), 0x9000, 0x5000)


def run(image, *args):
    """`tools/nvsedit.py IMAGE ...`; returns stdout."""
    r = subprocess.run([PY, NVSEDIT, str(image), *map(str, args)], capture_output=True, text=True)
    assert r.returncode == 0, f'nvsedit {args} failed ({r.returncode}):\n{r.stdout}\n{r.stderr}'
    return r.stdout


def items(image, ns=NS):
    """{key: (page, entry index, header bytes, span)} for every written entry of namespace `ns`,
    read back with the module's own page walker (the last copy of a key wins, as NVS does)."""
    n = nvs(image)
    nsi = next(k for k, v in n.ns.items() if v == ns)
    out = {}
    for p, pg in enumerate(n.pages):
        for i, e in n.entries(pg):
            if e[0] == nsi:
                out[e[8:24].split(b'\0')[0].decode()] = (p, i, bytes(e), max(e[2], 1))
    return out


def slot(image, page, first, span):
    """The raw span * 32 bytes of the entries an item occupied."""
    return bytes(nvs(image).pages[page][64 + 32 * first:64 + 32 * (first + span)])


def str_value(image, key, ns=NS):
    """The string of NS/KEY, parsed out of the image: header (type, chunk, size, both CRCs) checked,
    the bytes taken from the span entries. Raises KeyError when the key is not there."""
    n = nvs(image)
    nsi = next(k for k, v in n.ns.items() if v == ns)
    for p, pg in enumerate(n.pages):
        for i, e in n.entries(pg):
            if e[0] != nsi or e[8:24].split(b'\0')[0].decode() != key:
                continue
            assert e[1] == 0x21, f'{key} is type 0x{e[1]:02x}, not str'
            assert e[3] == 0xff, f'{key} chunk index 0x{e[3]:02x}, not 0xff'
            size, reserved, dcrc = struct.unpack('<HHI', e[24:32])
            assert reserved == 0xffff, f'{key} reserved 0x{reserved:04x}'
            assert e[2] == 1 + (size + 31) // 32, f'{key} span {e[2]} for {size} bytes'
            assert struct.unpack('<I', e[4:8])[0] == nvsedit.crc32_le(bytes(e[0:4] + e[8:32])), f'{key} header crc'
            data = bytes(pg[64 + 32 * (i + 1):64 + 32 * (i + 1) + size])
            assert dcrc == nvsedit.crc32_le(data), f'{key} data crc'
            assert data[-1] == 0, f'{key} not NUL-terminated'
            assert all(b == 0xff for b in pg[64 + 32 * (i + 1) + size:64 + 32 * (i + e[2])]), f'{key} padding'
            assert all(nvsedit.entry_state(pg, j) == 2 for j in range(i, i + e[2])), f'{key} span entry states'
            return data[:-1].decode()
    raise KeyError(key)


@pytest.fixture
def scratch(tmp_path):
    """A scratch copy of the device dump (never edited in place; images/ is read-only here)."""
    if not os.path.exists(DUMP):
        pytest.skip('device dump not available')
    flash = tmp_path / 'stock.bin'
    shutil.copyfile(DUMP, flash)
    return flash


def test_redact_erases_the_credentials_and_turns_wifi_off(scratch):
    """`redact` erases the three user_config credential keys everywhere and writes net_en = 0. The
    entries they occupied read 0xff afterwards, so the values are gone from the image itself, and a
    second `redact` is a no-op for them (idempotent)."""
    before = items(scratch)
    where = {k: before[k][:2] + (before[k][3],) for k in CREDS if k in before}
    assert set(where) == set(CREDS), f'the dump should hold all of {CREDS}, has {sorted(before)}'
    assert before['net_en'][2][24] == 1, 'the dump has user_config/net_en = 1 (WiFi on)'

    out = run(scratch, 'redact')
    for key in CREDS:
        assert f'{NS}/{key}: erased 1 copy' in out, out
    assert f'{NS}/net_en = 0:' in out and 'net_en = 0' in out.splitlines()[-1], out

    # 1. `list` no longer mentions them, and net_en reads 0 (the old copy was retired)
    listing = run(scratch, 'list')
    for key in CREDS:
        assert key not in listing, f'{key} still listed after redact'
    net = [ln for ln in listing.splitlines() if f'{NS}/net_en ' in ln]
    assert len(net) == 1 and net[0].endswith('(u8) = 0'), net
    # 2. and neither does the page walker; the slots are blank
    after = items(scratch)
    assert not [k for k in CREDS if k in after], sorted(after)
    for key, (page, first, span) in where.items():
        assert slot(scratch, page, first, span) == b'\xff' * 32 * span, f'{key} bytes still in the image'
        pg = nvs(scratch).pages[page]
        assert all(nvsedit.entry_state(pg, j) == 0 for j in range(first, first + span)), f'{key} entry states'
    # 3. everything else survived (the phy calibration blobs, the panel type, the light settings)
    assert items(scratch, 'hw_calib')['screenType'][2][24] == 2, 'hw_calib/screenType lost'
    assert {k for k in before if k not in CREDS} <= set(after), 'redact dropped an unrelated key'
    assert 'phy/cal_data (blob_data)' in listing and 'phy/cal_version (u32) = 711' in listing

    # 4. idempotent: a second pass finds nothing to erase and still leaves net_en = 0
    out2 = run(scratch, 'redact')
    for key in CREDS:
        assert f'{NS}/{key}: erased 0 copies (key not present)' in out2, out2
    assert items(scratch)['net_en'][2][24] == 0


def test_set_str_writes_a_valid_entry_and_replaces_the_old_one(scratch):
    """`set-str` writes a str item (type 0x21) with its data in the span entries and both CRCs right;
    a second write of the same key retires the first. Values of hidden keys stay hidden in `list`."""
    run(scratch, 'redact')
    out = run(scratch, 'set-str', NS, 'sta_ssid', 'emulator')
    assert 'sta_ssid = <8 chars>' in out and 'emulator' not in out, 'set-str printed a hidden value'

    listing = run(scratch, 'list')
    hit = [ln for ln in listing.splitlines() if f'{NS}/sta_ssid ' in ln]
    assert len(hit) == 1 and hit[0].endswith('(str) = <8 chars hidden>'), hit    # listed again, value hidden
    assert str_value(scratch, 'sta_ssid') == 'emulator'                          # ... and right in the image

    # a plain key is shown, and a string longer than one entry spans several
    run(scratch, 'set-str', NS, 'note', 'hello world')
    run(scratch, 'set-str', NS, 'long', 'x' * 100)
    assert f'{NS}/note (str) = hello world' in run(scratch, 'list')
    assert str_value(scratch, 'note') == 'hello world'
    assert str_value(scratch, 'long') == 'x' * 100
    assert items(scratch)['long'][3] == 5                    # header + ceil(101 / 32) = 4 data entries

    # replacing: one copy in the listing, the old entries blanked, the new value readable
    page, first, _, span = items(scratch)['note']
    run(scratch, 'set-str', NS, 'note', 'second')
    assert len([ln for ln in run(scratch, 'list').splitlines() if f'{NS}/note ' in ln]) == 1
    assert str_value(scratch, 'note') == 'second'
    assert slot(scratch, page, first, span) == b'\xff' * 32 * span

    # `erase` takes it away again, and a missing key is not an error
    assert f'{NS}/note: erased 1 copy' in run(scratch, 'erase', NS, 'note')
    assert 'note' not in items(scratch)
    assert 'erased 0 copies (key not present)' in run(scratch, 'erase', NS, 'note')
    # nothing else moved: the u8 path still works on the same image
    run(scratch, 'set-u8', NS, 'net_en', '1')
    assert items(scratch)['net_en'][2][24] == 1


@pytest.fixture
def redacted(tmp_path, request, stock_card):                                     # noqa: F811
    """The redacted dump plus a copy of the device card, running in the emulator."""
    if not os.path.exists(DUMP):
        pytest.skip('device dump not available')
    if not os.path.exists(QEMU):
        pytest.skip('qemu not built')
    if not os.path.exists(GOLDEN):
        pytest.skip('tests/golden/stock-home.png not available')
    flash = tmp_path / 'stock.bin'
    shutil.copyfile(DUMP, flash)
    subprocess.run([PY, NVSEDIT, str(flash), 'redact'], check=True, stdout=subprocess.DEVNULL)
    sd = tmp_path / 'sd.img'
    shutil.copyfile(stock_card, sd)
    name = 'nvs-' + request.node.name.replace('[', '-').replace(']', '').replace('=', '-')
    x4emu(name, 'stop', check=False)
    x4emu(name, 'run', '--flash', str(flash), '--sd', str(sd))
    yield name, tmp_path
    x4emu(name, 'stop', check=False)


def test_redacted_image_boots_to_home(redacted):
    """The whole point of `redact`: the stock still finds its NVS and paints Home. Same path as
    tests/test_stock.py (preflight deep sleep, power press, refresh 2 + an idle panel -- refresh 3 is
    the status-bar clock at the next minute and is never waited for), same golden."""
    name, tmp = redacted
    st = boot_to_home(name)
    assert st['epd_unknown_cmds'] == 0 and st['spi2']['dma_errors'] == 0, st['spi2']
    shot = tmp / 'home.png'
    x4emu(name, 'screenshot', str(shot))
    assert masked_diff(shot, GOLDEN) == 0, 'the redacted image draws a different Home than tests/golden/stock-home.png'
    # WiFi is off (net_en = 0), so nothing ever brings the radio up
    log = x4emu(name, 'log').stdout
    assert 'wifi:mode : sta' not in log, 'the radio started although net_en is 0'

"""tools/device.py's flash-layout reader: no emulator, no QEMU, no serial port (~1 s).

Why this file exists. `tools/device.py` used to hard-code app0 = 0x10000 as *the* slot the
device starts. On 2026-09-07 the owner updated the device through its own Upgrade menu: the
OTA wrote stock 7.5.4 into **app1** and moved otadata to point at it, so app0 (still stock
7.2.4) is no longer started at all. A write into app0 would land in a slot the bootloader
never runs, and the tool would have called it a success. The layout is therefore parsed,
never assumed, and this file pins the parsing -- against the two dumps on disk and against
the corner cases the ESP-IDF bootloader has to resolve -- without going anywhere near the
device:

  * the partition table of both dumps (two OTA app slots, one otadata partition, no factory),
  * what otadata actually says in each: the 2026-09-06 dump boots app0, the 2026-09-07 dump
    boots app1,
  * the app descriptors: xteink_app 7.2.4 in app0 and 7.5.4 in app1 on 2026-09-07,
  * the CRC-32 gate -- a corrupted entry stops being a candidate,
  * equal sequence numbers, one invalid CRC, and a blank (0xFFFFFFFF) pair, each resolved
    the way bootloader_common_select_otadata() resolves it; those sectors are built in
    memory, the dumps are only ever read,
  * the refusal `flash-crosspoint` / `restore-stock` now print, before writing anything,
    when the slot they would write is not the slot that boots,
  * the structural guard that keeps every esptool call in the file off everything below
    0x10000 (bootloader, partition table, otadata).

The dump-backed cases need images/device/flash-2026-09-0{6,7}-a.bin (gitignored -- their NVS
holds the owner's WiFi credentials, so nothing here prints partition contents) and skip
without them, as the other stock tests do on CI. The in-memory cases always run.
"""
import os, struct, subprocess, sys
import pytest
from conftest import ROOT, PY

sys.path.insert(0, os.path.join(ROOT, 'tools'))
import device                                                                  # noqa: E402

TOOL = os.path.join(ROOT, 'tools', 'device.py')
DUMP_0906 = os.path.join(ROOT, 'images', 'device', 'flash-2026-09-06-a.bin')
DUMP_0907 = os.path.join(ROOT, 'images', 'device', 'flash-2026-09-07-a.bin')
VALID, PENDING_VERIFY = 2, 1


def dump(path):
    if not os.path.exists(path):
        pytest.skip(f'{os.path.relpath(path, ROOT)} not available')
    return device.layout_from_dump(path)


@pytest.fixture(scope='module')
def old():
    """The layout of the 7.2.4 era dump (2026-09-06)."""
    return dump(DUMP_0906)


@pytest.fixture(scope='module')
def now():
    """The layout of the dump taken after the owner's OTA update (2026-09-07)."""
    return dump(DUMP_0907)


def sector(seq, state=VALID, crc=None):
    """One esp_ota_select_entry_t as the flash holds it (correct CRC unless told otherwise)."""
    head = struct.pack('<I', seq) + b'\xff' * 20 + struct.pack('<I', state)
    return head + struct.pack('<I', device.esp_crc32(head[:4]) if crc is None else crc)


def entry(seq, state=VALID, crc=None):
    return device.parse_ota_entry(sector(seq, state, crc))


# ----------------------------------------------------------------- the two dumps

@pytest.mark.parametrize('which', ['old', 'now'])
def test_partition_table_is_read_out_of_the_dump(which, request):
    """The stock table, parsed rather than assumed: which partitions, where, how big."""
    layout = request.getfixturevalue(which)
    got = [(p['label'], p['kind'], p['offset'], p['size']) for p in layout['partitions']]
    assert got == [
        ('nvs',      'data/nvs',      0x009000, 0x005000),
        ('otadata',  'data/otadata',  0x00E000, 0x002000),
        ('app0',     'app/ota_0',     0x010000, 0x7E0000),
        ('app1',     'app/ota_1',     0x7F0000, 0x7E0000),
        ('spiffs',   'data/spiffs',   0xFD0000, 0x014000),
        ('coredump', 'data/coredump', 0xFE4000, 0x01C000),
    ]
    assert [s['label'] for s in layout['apps']] == ['app0', 'app1']   # ota_0 first
    assert layout['factory'] is None                                  # no fallback slot
    assert layout['otadata']['offset'] == 0x00E000
    # nothing in the tool hard-codes those addresses any more
    src = open(device.__file__).read()
    assert 'STOCK_APP0 =' not in src and 'STOCK_APP1 =' not in src


def test_the_2026_09_07_dump_boots_app1(now):
    """Measured, not assumed: entry 0 seq 1, entry 1 seq 2, both VALID and both CRCs good,
    so entry 1 is active and (2 - 1) % 2 selects OTA slot 1 = app1."""
    e0, e1 = now['entries']
    assert (e0['seq'], e0['state'], e0['state_name'], e0['crc_ok'], e0['valid']) == (1, VALID, 'VALID', True, True)
    assert (e1['seq'], e1['state'], e1['state_name'], e1['crc_ok'], e1['valid']) == (2, VALID, 'VALID', True, True)
    assert not e0['blank'] and not e1['blank']
    boot = now['boot']
    assert (boot['active'], boot['seq'], boot['index'], boot['label']) == (1, 2, 1, 'app1')
    assert boot['pending_verify'] is False
    assert [s['boots'] for s in now['apps']] == [False, True]


def test_the_2026_09_06_dump_boots_app0(old):
    """The 7.2.4 era: entry 0 seq 1 VALID, entry 1 never written (all 0xFF), so entry 0 is
    the only candidate and (1 - 1) % 2 selects app0."""
    e0, e1 = old['entries']
    assert (e0['seq'], e0['state_name'], e0['crc_ok'], e0['valid']) == (1, 'VALID', True, True)
    assert e1['blank'] and e1['seq'] == 0xFFFFFFFF and not e1['valid']
    boot = old['boot']
    assert (boot['active'], boot['seq'], boot['index'], boot['label']) == (0, 1, 0, 'app0')
    assert [s['boots'] for s in old['apps']] == [True, False]


def test_app_descriptors_of_the_2026_09_07_dump(now):
    """Both slots hold a stock app; the version in each is what decides whether writing a
    slot is pointless."""
    app0, app1 = now['apps']
    assert app0['desc']['project'] == 'xteink_app' and app0['desc']['version'] == '7.2.4'
    assert app1['desc']['project'] == 'xteink_app' and app1['desc']['version'] == '7.5.4'
    assert app0['desc']['idf_ver'] == app1['desc']['idf_ver'] == 'v6.0.1'
    assert app1['desc']['date'] == 'Sep  5 2026'
    assert (app0['blank'], app1['blank']) == (False, False)
    assert app0['image_size'] == 5503680      # == images/device/stock-app0-7.2.4.bin
    assert app1['image_size'] == 5445680      # == images/device/stock-app1-7.5.4.bin
    assert 'xteink_app 7.5.4' in device.describe_app(app1)


def test_the_write_commands_read_the_layout_cheaply(now):
    """The live write path asks for deep=False: the same slots, versions and boot decision,
    without walking every segment header. One serial transaction per slot instead of one
    per segment is one fewer chance for the magnetic pogo adapter to let go mid-read."""
    shallow = device.read_layout(device.dump_reader(DUMP_0907), 'shallow', deep=False)
    assert shallow['boot']['label'] == now['boot']['label'] == 'app1'
    assert [s['desc']['version'] for s in shallow['apps']] == ['7.2.4', '7.5.4']
    assert [s['image_size'] for s in shallow['apps']] == [None, None]
    assert 'bytes' not in device.describe_app(shallow['apps'][1])


def test_app1_was_still_blank_before_the_update(old):
    app0, app1 = old['apps']
    assert app0['desc']['version'] == '7.2.4' and app0['image_size'] == 5503680
    assert app1['blank'] and app1['desc'] is None and app1['image_size'] is None
    assert device.describe_app(app1) == 'blank (erased, 0xFF)'


def test_the_crc_check_rejects_a_corrupted_entry(now):
    """The active entry of the real dump, with one byte of its sequence number flipped in
    memory (the dump itself is never modified): the CRC no longer matches, so the
    bootloader would not consider it, and the other entry takes over."""
    with open(DUMP_0907, 'rb') as f:
        f.seek(now['otadata']['offset'] + device.OTA_SECTOR)
        good = f.read(device.OTA_ENTRY)
    assert device.parse_ota_entry(good)['valid']
    bad = bytes([good[0] ^ 0x10]) + good[1:]
    e = device.parse_ota_entry(bad)
    assert e['seq'] == 0x12 and not e['crc_ok'] and not e['valid']
    assert e['crc'] != e['crc_expected']
    fallen_back = device.resolve_boot_slot([device.parse_ota_entry(sector(1)), e], 2)
    assert (fallen_back['active'], fallen_back['index']) == (0, 0)


# ------------------------------------------- sector pairs the bootloader has to resolve
# Built in memory. resolve_boot_slot() mirrors bootloader_common_select_otadata().

def test_equal_sequence_numbers_go_to_entry_1():
    """Both valid and equal: the bootloader's comparison is a strict '>', so entry 0 does
    not win the tie -- entry 1 does."""
    b = device.resolve_boot_slot([entry(3), entry(3)], 2)
    assert b['active'] == 1
    assert b['seq'] == 3 and b['index'] == (3 - 1) % 2 == 0


def test_a_higher_sequence_number_wins_from_either_side():
    assert device.resolve_boot_slot([entry(9), entry(4)], 2)['active'] == 0
    assert device.resolve_boot_slot([entry(4), entry(9)], 2)['active'] == 1
    # the slot is the sequence number, not the entry: seq 9 -> (9-1) % 2 = 0
    assert device.resolve_boot_slot([entry(9), entry(4)], 2)['index'] == 0


def test_an_entry_with_an_invalid_crc_is_not_a_candidate():
    """A higher sequence number does not help an entry whose CRC does not check out."""
    b = device.resolve_boot_slot([entry(2), entry(7, crc=0xDEADBEEF)], 2)
    assert (b['active'], b['seq'], b['index']) == (0, 2, 1)
    b = device.resolve_boot_slot([entry(7, crc=0xDEADBEEF), entry(2)], 2)
    assert (b['active'], b['seq'], b['index']) == (1, 2, 1)


def test_a_blank_pair_is_a_fresh_flash():
    """Two erased sectors: no candidate at all. Without a factory partition the bootloader
    starts OTA slot 0; with one it starts the factory app (index -1)."""
    blank = [device.parse_ota_entry(b'\xff' * 32), device.parse_ota_entry(b'\xff' * 32)]
    assert all(e['blank'] and not e['valid'] for e in blank)
    b = device.resolve_boot_slot(blank, 2, has_factory=False)
    assert (b['active'], b['index']) == (None, 0) and 'fresh' in b['reason']
    b = device.resolve_boot_slot(blank, 2, has_factory=True)
    assert (b['active'], b['index']) == (None, -1) and 'factory' in b['reason']


def test_pending_verify_is_reported():
    """A slot that has not confirmed itself yet: the report has to say so, because a
    bootloader built with rollback starts the *other* slot instead."""
    b = device.resolve_boot_slot([entry(1), entry(2, state=PENDING_VERIFY)], 2)
    assert b['active'] == 1 and b['pending_verify'] and b['state_name'] == 'PENDING_VERIFY'


def test_the_crc_is_esp_idfs_crc32_le_over_the_sequence_number():
    """Pinned against the bytes both dumps carry: seq 1 -> 0x4743989a, seq 2 -> 0x55f63774
    (esp_crc32_le with an all-ones seed, over the four ota_seq bytes only)."""
    assert device.esp_crc32(struct.pack('<I', 1)) == 0x4743989A
    assert device.esp_crc32(struct.pack('<I', 2)) == 0x55F63774


# ---------------------------------------------------- the gate in front of every write

def test_flash_crosspoint_refuses_app0_while_app1_boots(now):
    """The whole point of this session: on today's device, aiming flash-crosspoint at app0
    is refused before anything is written, and the message says what to do instead."""
    target = device.slot_by_label(now, 'app0')
    msg = device.boot_slot_refusal(now, target, 'Writing firmware.bin', 'flash-crosspoint')
    assert msg and msg.startswith('REFUSING: nothing has been written.')
    assert 'The bootloader starts app1, not app0' in msg
    assert 'xteink_app 7.5.4' in msg                      # what app1 holds today
    assert '--slot app1' in msg                           # what the owner would do instead
    assert 'Upgrade menu' in msg                          # or let the device move it itself
    assert 'otadata sits below 0x10000' in msg            # and what this tool will not do
    # the slot that does boot is not refused
    assert device.boot_slot_refusal(now, device.slot_by_label(now, 'app1'),
                                    'Writing firmware.bin', 'flash-crosspoint') is None


def test_restore_stock_refuses_app0_while_app1_boots(now):
    target = device.slot_by_label(now, 'app0')
    msg = device.boot_slot_refusal(now, target, 'Restoring the stock app', 'restore-stock')
    assert msg and 'Restoring the stock app would put an app into app0' in msg
    assert 'tools/device.py restore-stock --slot app1' in msg
    assert device.boot_slot_refusal(now, device.slot_by_label(now, 'app1'),
                                    'Restoring the stock app', 'restore-stock') is None


def test_the_gate_follows_otadata_and_not_a_constant(old):
    """The same code on the pre-update layout comes to the opposite answer: there app0 is
    the slot that boots and app1 is the one that would be pointless to write."""
    assert device.boot_slot_refusal(old, device.slot_by_label(old, 'app0'), 'Writing x',
                                    'flash-crosspoint') is None
    msg = device.boot_slot_refusal(old, device.slot_by_label(old, 'app1'), 'Writing x',
                                   'flash-crosspoint')
    assert msg and 'The bootloader starts app0, not app1' in msg


def test_a_layout_without_a_boot_slot_is_refused():
    """Nothing valid in otadata and no way to say what runs: refuse rather than guess."""
    layout = {'apps': [{'label': 'app0'}], 'boot': {'label': None, 'index': None,
                                                    'reason': 'no otadata partition'}}
    msg = device.boot_slot_refusal(layout, {'label': 'app0'}, 'Writing x', 'flash-crosspoint')
    assert msg and 'does not name a slot to boot' in msg
    # and the other way round: otadata unusable, a factory partition takes over, so no OTA
    # slot runs at all and "--slot <the one that boots>" would be nonsense advice
    layout = {'apps': [{'label': 'app0'}, {'label': 'app1'}],
              'boot': {'label': 'factory', 'index': -1, 'reason': 'no valid otadata entry'}}
    msg = device.boot_slot_refusal(layout, {'label': 'app0'}, 'Writing x', 'flash-crosspoint')
    assert msg and 'does not start any OTA app slot' in msg and '--slot' not in msg


# -------------------------------------------------------- nothing writes below 0x10000

@pytest.mark.parametrize('addr', [0x0, 0x1000, 0x8000, 0xE000, 0xFFFF])
def test_no_write_below_the_first_app_slot(addr):
    """Bootloader, partition table and otadata are unreachable from this file: the guard
    fires before esptool is even spawned."""
    with pytest.raises(SystemExit) as e:
        device._refuse_forbidden_write(['write-flash', hex(addr), 'some.bin'])
    assert 'REFUSING to write flash' in str(e.value) and '0x10000' in str(e.value)


@pytest.mark.parametrize('addr', [0x10000, 0x7F0000])
def test_the_app_slots_stay_writable(addr):
    assert device._refuse_forbidden_write(['write-flash', hex(addr), 'some.bin']) is None
    assert device._refuse_forbidden_write(['read-flash', '0x0', '0x1000', 'out.bin']) is None


@pytest.mark.parametrize('sub', ['erase-flash', 'erase_flash', 'erase-region'])
def test_erasing_is_refused_outright(sub):
    with pytest.raises(SystemExit) as e:
        device._refuse_forbidden_write([sub, '0x10000', '0x1000'])
    assert 'nothing here erases flash' in str(e.value)


def test_every_esptool_command_line_passes_the_guard():
    """Structural, so a future subcommand cannot quietly route around it: the two functions
    that build an esptool command line are the only ones, and both call the guard first."""
    src = open(device.__file__).read()
    assert src.count("'-m', 'esptool'") == 2, 'a third esptool call site appeared'
    for fn in ('def esptool(', 'def esp_run('):
        body = src[src.index(fn):src.index(fn) + 400]
        assert '_refuse_forbidden_write(args)' in body, f'{fn} does not call the guard'
    assert "'-m', 'espefuse'" in src and 'burn' not in src.split('def cmd_efuse_summary')[1][:400]


# ------------------------------------------------------------------------ the CLI

def test_slots_from_dump_needs_no_device():
    """The subcommand the owner runs. Offline, read-only, and it names the boot slot in
    words rather than in addresses."""
    if not os.path.exists(DUMP_0907):
        pytest.skip('images/device/flash-2026-09-07-a.bin not available')
    r = subprocess.run([PY, TOOL, 'slots', '--from-dump', DUMP_0907],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'Boot slot: app1' in r.stdout
    assert 'BOOTS  app1' in r.stdout and 'xteink_app 7.5.4' in r.stdout
    assert 'entry 1: seq 2, state VALID, CRC ok' in r.stdout


def test_slots_says_so_plainly_when_a_file_is_not_a_dump(tmp_path):
    """Pointed at something that is not a flash image it explains itself instead of
    throwing a traceback at the owner."""
    junk = tmp_path / 'not-a-dump.bin'
    junk.write_bytes(bytes(0x20000))
    r = subprocess.run([PY, TOOL, 'slots', '--from-dump', str(junk)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 1 and 'Traceback' not in r.stderr
    assert 'cannot read the flash layout' in r.stderr


def test_help_still_works():
    for args in (['--help'], ['slots', '--help'], ['flash-crosspoint', '--help'],
                 ['restore-stock', '--help']):
        r = subprocess.run([PY, TOOL, *args], capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
        assert 'usage:' in r.stdout

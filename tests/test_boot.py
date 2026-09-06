"""End-to-end: boot CrossPoint in the emulator, reach Home, press Right, see a change."""
import json, os, subprocess
from conftest import x4emu

def test_boot_to_home_and_press_right(images, emu, tmp_path):
    x4emu(emu, 'run', '--flash', images['flash'], '--sd', images['sd'], '--fast-epd')
    x4emu(emu, 'wait-text', 'Entering activity: Home', '--timeout', 90)
    x4emu(emu, 'wait-refresh', '--total', 2, '--timeout', 60)   # boot splash + home paint
    state = json.loads(x4emu(emu, 'state').stdout)
    assert state['panel'] == 'uc8279'
    assert state['refresh_count'] >= 2
    assert state['epd_unknown_cmds'] == 0
    before = tmp_path / 'before.png'
    after = tmp_path / 'after.png'
    x4emu(emu, 'screenshot', str(before))
    assert before.stat().st_size > 1000
    x4emu(emu, 'wait-quiet', '--seconds', 2, '--timeout', 60)
    x4emu(emu, 'screenshot', str(before))
    n0 = json.loads(x4emu(emu, 'state').stdout)['refresh_count']
    x4emu(emu, 'press', 'right', '--wait', 20)
    x4emu(emu, 'wait-refresh', '--total', n0 + 1, '--timeout', 30)
    x4emu(emu, 'wait-quiet', '--seconds', 1, '--timeout', 30)
    r = x4emu(emu, 'screenshot', str(after), '--diff', str(before), check=False)
    assert r.returncode == 1, f'expected a visible change after pressing Right:\n{r.stdout}'
    assert 'differ' in r.stdout
    state = json.loads(x4emu(emu, 'state').stdout)
    assert state['refresh_count'] > n0
    assert state['buttons']['right'] is False

def test_probe_verdict_matches_device(images, emu):
    """The panel probe must answer exactly what the desk unit answers."""
    x4emu(emu, 'run', '--flash', images['flash'], '--sd', images['sd'], '--fast-epd')
    x4emu(emu, 'wait-text', 'promoted SSD1677 -> UC8279 800x480 (LUT_VER=68)', '--timeout', 60)
    log = open(os.path.join(os.path.dirname(images['flash']), '..', '..', '.x4emu', emu, 'console.log'), errors='replace').read() \
        if False else open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.x4emu', emu, 'console.log'), errors='replace').read()
    assert 'bus probe VER=00 0F 68 00 00 FLG=13 -> UltraChip' in log
    assert 'MTP[0x000..0x02F]: A5 A5 1F 20 25 25 3C 00 97 02 02 03 20 02 58 00' in log

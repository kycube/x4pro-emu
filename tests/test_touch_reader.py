"""M4: touch, I2C devices, battery. Boot, open the test book by tapping, page forward twice."""
import json
from conftest import x4emu, emu_log, emu_state, boot_crosspoint, open_test_book, wait_log


def test_i2c_devices_and_battery(images, emu):
    boot_crosspoint(emu, images)
    log = emu_log(emu)
    assert 'SDK RTC found' in log
    assert 'SDMMC card mounted' in log
    st = emu_state(emu)
    assert st['i2c0']['transactions'] > 10
    assert st['battery']['soc'] == 63
    # the resident BATINFO profile must satisfy the firmware: no upload
    assert st['battery']['batinfo_writes'] == 0, st['battery']
    # the gauge and the charger STAT line follow `x4emu battery`
    out = json.loads(x4emu(emu, 'battery', '--soc', 42, '--mv', 3700, '--charging', 'on').stdout)
    assert out == {'soc': 42, 'mv': 3700, 'charging': True}
    assert emu_state(emu)['gpio']['chg']['level'] == 1


def test_tap_open_book_and_page_forward(images, emu, tmp_path):
    boot_crosspoint(emu, images)
    open_test_book(emu, tmp_path)            # two taps: Browse Files on Home, the book's row
    p1 = tmp_path / 'page1.png'; p2 = tmp_path / 'page2.png'; p3 = tmp_path / 'page3.png'
    x4emu(emu, 'screenshot', str(p1))
    x4emu(emu, 'press', 'right', '--wait', 20)
    x4emu(emu, 'wait-quiet', '--seconds', 1.5, '--timeout', 60)
    r = x4emu(emu, 'screenshot', str(p2), '--diff', str(p1), check=False)
    assert r.returncode == 1, r.stdout
    x4emu(emu, 'press', 'right', '--wait', 20)
    x4emu(emu, 'wait-quiet', '--seconds', 1.5, '--timeout', 60)
    r = x4emu(emu, 'screenshot', str(p3), '--diff', str(p2), check=False)
    assert r.returncode == 1, r.stdout
    # Home pad returns to the home screen (its second "Entering activity: Home"; the first is the boot's)
    x4emu(emu, 'home', '--wait', 20)
    wait_log(emu, 'Entering activity: Home', count=2)

"""M4: touch, I2C devices, battery. Boot, open the test book by tapping, page forward twice."""
import json, os
from conftest import x4emu, ROOT

def console(emu):
    return open(os.path.join(ROOT, '.x4emu', emu, 'console.log'), errors='replace').read()

def test_i2c_devices_found(images, emu):
    x4emu(emu, 'run', '--flash', images['flash'], '--sd', images['sd'], '--fast-epd')
    x4emu(emu, 'wait-text', 'Entering activity: Home', '--timeout', 90)
    log = console(emu)
    assert 'SDK RTC found' in log
    assert 'SDMMC card mounted' in log
    st = json.loads(x4emu(emu, 'state').stdout)
    assert st['i2c0']['transactions'] > 10
    assert st['battery']['soc'] == 63
    # the resident BATINFO profile must satisfy the firmware: no upload
    assert st['battery']['batinfo_writes'] == 0, st['battery']

def test_tap_open_book_and_page_forward(images, emu, tmp_path):
    x4emu(emu, 'run', '--flash', images['flash'], '--sd', images['sd'], '--fast-epd')
    x4emu(emu, 'wait-text', 'Entering activity: Home', '--timeout', 90)
    x4emu(emu, 'wait-quiet', '--seconds', 2, '--timeout', 60)
    # Home menu, portrait UI on the landscape panel: "Browse Files" sits around (345, 350)
    x4emu(emu, 'tap', 345, 350, '--quiet', 2, '--wait', 20)
    x4emu(emu, 'wait-text', 'Entering activity: FileBrowser', '--timeout', 30)
    x4emu(emu, 'wait-quiet', '--seconds', 1.5, '--timeout', 60)
    # first entry of the file list: select it with the buttons (Right = down/next is not
    # needed for the first row); confirm via a tap on the first row
    x4emu(emu, 'screenshot', str(tmp_path / 'browser.png'))
    x4emu(emu, 'tap', 140, 240, '--quiet', 1.5, '--wait', 20)
    x4emu(emu, 'wait-text', 'Loaded ePub: /Test Book.epub', '--timeout', 90)
    x4emu(emu, 'wait-quiet', '--seconds', 3, '--timeout', 120)
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
    # Home pad returns to the home screen
    x4emu(emu, 'home', '--wait', 20)
    x4emu(emu, 'wait-text', 'Entering activity: Home', '--timeout', 30)

def test_battery_property(images, emu):
    x4emu(emu, 'run', '--flash', images['flash'], '--sd', images['sd'], '--fast-epd')
    x4emu(emu, 'wait-text', 'Entering activity: Home', '--timeout', 90)
    out = json.loads(x4emu(emu, 'battery', '--soc', 42, '--mv', 3700, '--charging', 'on').stdout)
    assert out == {'soc': 42, 'mv': 3700, 'charging': True}
    st = json.loads(x4emu(emu, 'state').stdout)
    assert st['gpio']['chg']['level'] == 1

"""M4/M5: the I2C devices, the battery gauge and charger line, the frontlight channels. (Touch and the
book walk: tests/test_device_screenshot.py, test_replay.py.)"""
import json
from conftest import x4emu, emu_log, emu_state, boot_crosspoint


def test_i2c_devices_battery_and_frontlight(images, emu):
    boot_crosspoint(emu, images)
    # the frontlight: both LEDC channels attached at 25 kHz, off at boot
    light = json.loads(x4emu(emu, 'light', '-v').stdout)
    gpios = {c['gpio']: c for c in light['channels']}
    assert 8 in gpios and 9 in gpios, light
    assert gpios[8]['freq_hz'] == 25000 and gpios[9]['freq_hz'] == 25000
    assert light['cool'] == 0 and light['warm'] == 0
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


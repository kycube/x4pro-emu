"""M5: frontlight PWM observable; power-button hold sleeps (VM paused, sleep screen), press wakes with DSLEEP."""
import json, os
from conftest import x4emu, ROOT

def console(emu):
    return open(os.path.join(ROOT, '.x4emu', emu, 'console.log'), errors='replace').read()

def test_frontlight_channels_attached(images, emu):
    x4emu(emu, 'run', '--flash', images['flash'], '--sd', images['sd'], '--fast-epd')
    x4emu(emu, 'wait-text', 'Entering activity: Home', '--timeout', 90)
    light = json.loads(x4emu(emu, 'light', '-v').stdout)
    gpios = {c['gpio']: c for c in light['channels']}
    assert 8 in gpios and 9 in gpios, light
    assert gpios[8]['freq_hz'] == 25000 and gpios[9]['freq_hz'] == 25000
    assert light['cool'] == 0 and light['warm'] == 0   # light off at boot

def test_deep_sleep_and_wake(images, emu, tmp_path):
    x4emu(emu, 'run', '--flash', images['flash'], '--sd', images['sd'], '--fast-epd')
    x4emu(emu, 'wait-text', 'Entering activity: Home', '--timeout', 90)
    x4emu(emu, 'wait-quiet', '--seconds', 2, '--timeout', 60)
    x4emu(emu, 'hold', 'power', '--ms', 700)
    x4emu(emu, 'wait-text', 'Entering deep sleep', '--timeout', 30)
    # the model pauses the VM once the firmware writes SLEEP_EN
    for _ in range(50):
        st = json.loads(x4emu(emu, 'state').stdout)
        if st['sleep']['sleeping']:
            break
        import time; time.sleep(0.2)
    assert st['sleep']['sleeping'] and st['sleep']['deep'], st['sleep']
    assert st['sleep']['ext1_sel'] == '0x8'          # GPIO3 = RTC IO 3
    assert 'paused' in x4emu(emu, 'status').stdout
    x4emu(emu, 'screenshot', str(tmp_path / 'sleep.png'))   # the sleep screen stays visible
    x4emu(emu, 'press', 'power')                              # auto-extended to a 1.5 s hold
    x4emu(emu, 'wait-text', 'rst:0x5 (DSLEEP)', '--timeout', 30)
    x4emu(emu, 'wait-text', 'Entering activity: Home', '--timeout', 90)
    st = json.loads(x4emu(emu, 'state').stdout)
    assert not st['sleep']['sleeping'] and st['sleep']['wakes'] == 1
    assert st['sleep']['ext1_status'] == '0x8' and st['sleep']['wakeup_cause'] == '0x2'
    assert 'running' in x4emu(emu, 'status').stdout

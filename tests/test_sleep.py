"""M5: frontlight PWM observable; power-button hold sleeps (VM paused, sleep screen), press wakes with DSLEEP;
the RTC IO pad writes around the sleep land in the stored-value overlay (S1, 3.2.9)."""
import json
from conftest import x4emu, emu_log, emu_state, boot_crosspoint, wait_for, wait_log


def test_frontlight_channels_attached(images, emu):
    boot_crosspoint(emu, images)
    light = json.loads(x4emu(emu, 'light', '-v').stdout)
    gpios = {c['gpio']: c for c in light['channels']}
    assert 8 in gpios and 9 in gpios, light
    assert gpios[8]['freq_hz'] == 25000 and gpios[9]['freq_hz'] == 25000
    assert light['cool'] == 0 and light['warm'] == 0   # light off at boot


def test_deep_sleep_and_wake(images, emu, tmp_path):
    boot_crosspoint(emu, images, quiet_s=2)
    x4emu(emu, 'hold', 'power', '--ms', 700)
    x4emu(emu, 'wait-text', 'Entering deep sleep', '--timeout', 30)
    # the model pauses the VM once the firmware writes SLEEP_EN
    st = wait_for(lambda: (s := emu_state(emu))['sleep']['sleeping'] and s, 20,
                  'the sleep model to pause the VM', every=0.2)
    assert st['sleep']['deep'], st['sleep']
    assert st['sleep']['ext1_sel'] == '0x8'          # GPIO3 = RTC IO 3
    assert 'paused' in x4emu(emu, 'status').stdout
    x4emu(emu, 'screenshot', str(tmp_path / 'sleep.png'))   # the sleep screen stays visible
    x4emu(emu, 'press', 'power')                              # auto-extended to a 1.5 s hold
    x4emu(emu, 'wait-text', 'rst:0x5 (DSLEEP)', '--timeout', 30)
    # the console keeps the first boot's lines across the modelled reset: the woken boot's Home is the second
    wait_log(emu, 'Entering activity: Home', count=2, timeout=90)
    st = emu_state(emu)
    assert not st['sleep']['sleeping'] and st['sleep']['wakes'] == 1
    assert st['sleep']['ext1_status'] == '0x8' and st['sleep']['wakeup_cause'] == '0x2'
    assert 'running' in x4emu(emu, 'status').stdout
    # the RTC IO pad configuration around the sleep (hold / pull / sleep isolation of RTC_IO_TOUCH_PAD3,
    # XTAL_32N, TOUCH_PAD0..) is stored by the x4pro.rtcio overlay, not logged (docs/NEXT_PHASE.md 3.2.9)
    assert st['rtcio']['pad_writes'] > 0, st['rtcio']
    assert 'rtcio' not in st['iolog_hot'], st['iolog_hot']
    assert 'x4pro/rtcio' not in emu_log(emu, 'qemu.log')

"""End-to-end: boot CrossPoint in the emulator, reach Home, press Right, see a change. (The panel
probe's answer, the desk unit's bytes, is checked by test_panels.py.)"""
from conftest import x4emu, emu_state, boot_crosspoint


def test_boot_to_home_and_press_right(images, emu, tmp_path):
    boot_crosspoint(emu, images)
    x4emu(emu, 'wait-refresh', '--total', 2, '--timeout', 60)   # boot splash + home paint
    state = emu_state(emu)
    assert state['panel'] == 'uc8279'                            # the default: the desk unit's panel
    assert state['epd_unknown_cmds'] == 0
    before = tmp_path / 'before.png'
    after = tmp_path / 'after.png'
    x4emu(emu, 'wait-quiet', '--seconds', 2, '--timeout', 60)
    x4emu(emu, 'screenshot', str(before))
    assert before.stat().st_size > 1000
    n0 = emu_state(emu)['refresh_count']
    x4emu(emu, 'press', 'right', '--wait', 20)                   # fails unless a refresh follows
    x4emu(emu, 'wait-quiet', '--seconds', 1, '--timeout', 30)
    r = x4emu(emu, 'screenshot', str(after), '--diff', str(before), check=False)
    assert r.returncode == 1, f'expected a visible change after pressing Right:\n{r.stdout}'
    assert 'differ' in r.stdout
    state = emu_state(emu)
    assert state['refresh_count'] > n0
    assert state['buttons']['right'] is False

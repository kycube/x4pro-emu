"""Inputs reach the firmware the way a finger delivers them (docs/x4emu.md, "Input delivery";
docs/NEXT_PHASE.md 0.h): no input the firmware reads may be lost, on an idle host or a loaded one.

The loss this guards against, in the form every full `make test` run used to show (one input per run,
a different test each time, never alone): `x4emu tap/home` reported "read by the firmware after
0.03 s" and no refresh followed. The mechanism, found with `--trace-i2c`: the GT911 model made every
input change a new frame at once, *overwriting* the frame the firmware was reading. A release that
arrived between the firmware's status read and its clear of 0x814E was wiped by that clear unseen, so
the firmware believed the finger (or the Home pad) was still down: the tap never completed, the pad
turned into a long press, and the *next* input was misread as well (it continued the stuck contact).
On a loaded host the vCPU thread is preempted between those two I2C transactions while QMP is
answered, which is what widened the window from a fraction of a millisecond to a real chance per
input. The model now latches each frame until the clear and reports a change that arrived meanwhile
as the next frame (QEMU patch "GT911 frames latch until the clear").

The deterministic form: stop the VM, press and release, continue -- the whole input lands while a
frame is unread. Before the fix the press was gone (the firmware found only a release frame); now it
sees the press and then the release, i.e. a tap. `test_inputs_land_on_a_squeezed_host` is the
statistical form: the QEMU process runs 20 ms out of every 50 (SIGSTOP/SIGCONT; guest time follows
the host clock, so the guest executes less per guest millisecond -- a loaded host made on purpose)
and every input must act, with no retry anywhere.
"""
import json, os, re, signal, subprocess, sys, threading, time
import pytest
from conftest import x4emu, ROOT

BROWSE_FILES = (345, 350)        # CrossPoint's Home menu entry, landscape panel pixels
BROWSE_FILES_GT911 = (129, 345)  # the same point in the GT911's portrait frame (x4emu.to_gt911)
INPUT = '/machine/x4pro-input'


def console(name):
    return open(os.path.join(ROOT, '.x4emu', name, 'console.log'), errors='replace').read()


def activity(name):
    """The CrossPoint activity entered last (its "Entering activity: X" console line)."""
    found = re.findall(r'Entering activity: (\w+)', console(name))
    return found[-1] if found else None


def state(name):
    return json.loads(x4emu(name, 'state').stdout)


def qmp(name, cmd, **args):
    return x4emu(name, 'qmp', json.dumps({'execute': cmd, **({'arguments': args} if args else {})}))


def boot_crosspoint(name, images):
    x4emu(name, 'run', '--flash', images['flash'], '--sd', images['sd'], '--fast-epd')
    x4emu(name, 'wait-text', 'Entering activity: Home', '--timeout', 120)
    x4emu(name, 'wait-quiet', '--seconds', 2, '--timeout', 60)


def need_latching_gt911(name):
    if 'input_seq' not in state(name)['gt911']:
        pytest.skip('this QEMU predates the latching GT911 model (no state.gt911.input_seq)')


def paused_input(name, prop, down, up):
    """Press and release `prop` while the VM is stopped, then let it run: the release arrives while
    the press frame is still unread. Returns the GT911 state seen just before `cont`."""
    qmp(name, 'stop')
    try:
        qmp(name, 'qom-set', path=INPUT, property=prop, value=down)
        qmp(name, 'qom-set', path=INPUT, property=prop, value=up)
        g = state(name)['gt911']
    finally:
        qmp(name, 'cont')
    return g


def wait_activity(name, want, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if activity(name) == want:
            return True
        time.sleep(0.2)
    return False


def test_a_release_before_the_firmware_reads_the_press_is_kept(images, emu):
    """CrossPoint, deterministic: a tap and a Home-pad press whose release lands before the firmware
    has read the press frame still act -- the press frame is kept until the clear and the release
    follows as the next frame."""
    boot_crosspoint(emu, images)
    need_latching_gt911(emu)
    rx, ry = BROWSE_FILES_GT911
    g = paused_input(emu, 'touch', json.dumps([{'x': rx, 'y': ry}]), '[]')
    assert g['frame_ready'] and g['pending'] and g['deferred'] >= 1, g
    assert wait_activity(emu, 'FileBrowser'), f'the tap was lost: still on {activity(emu)}'
    x4emu(emu, 'wait-quiet', '--seconds', 2, '--timeout', 60)
    g = state(emu)['gt911']
    assert g['read_seq'] == g['input_seq'] and not g['pending'], g      # press and release both consumed
    g = paused_input(emu, 'home', True, False)
    assert g['pending'], g
    assert wait_activity(emu, 'Home'), f'the Home pad was lost: still on {activity(emu)}'


def test_inputs_report_what_the_firmware_saw(images, emu):
    """`tap`, `home` and `press` hold until the firmware has seen the input and its release, and say
    so: a GT911 frame consumed (`read_after_s`, `release_read`), a button's level sampled twice
    (`state.gpio_in.samples`), and at least --ms of guest time (`held_ms`)."""
    boot_crosspoint(emu, images)
    need_latching_gt911(emu)
    t = json.loads(x4emu(emu, '--json', 'tap', *BROWSE_FILES, '--quiet', 2, '--wait', 30).stdout)
    assert t['read_after_s'] is not None and t['release_read'] is True and t['held_ms'] >= 120, t
    assert wait_activity(emu, 'FileBrowser')
    x4emu(emu, 'wait-quiet', '--seconds', 2, '--timeout', 60)
    p = json.loads(x4emu(emu, '--json', 'press', 'right', '--ms', 80, '--wait', 20).stdout)
    assert p['read_after_s'] is not None and p['release_read'] is True and p['held_ms'] >= 80, p
    assert state(emu)['gpio_in']['samples']['right'] >= 2
    x4emu(emu, 'wait-quiet', '--seconds', 2, '--timeout', 60)
    h = json.loads(x4emu(emu, '--json', 'home', '--quiet', 2, '--wait', 30).stdout)
    assert h['read_after_s'] is not None and h['release_read'] is True and h['held_ms'] >= 250, h
    assert wait_activity(emu, 'Home')


class Squeeze:
    """Run a process RUN_MS out of every RUN_MS + STOP_MS (SIGSTOP / SIGCONT) from a thread, and
    always leave it running."""
    def __init__(self, pid, run_ms=20, stop_ms=30):
        self.pid, self.run_s, self.stop_s = pid, run_ms / 1000, stop_ms / 1000
        self.done = threading.Event()
        self.t = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        try:
            while not self.done.wait(self.run_s):
                os.kill(self.pid, signal.SIGSTOP)
                time.sleep(self.stop_s)
                os.kill(self.pid, signal.SIGCONT)
        except ProcessLookupError:
            pass

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *exc):
        self.done.set()
        self.t.join()
        try:
            os.kill(self.pid, signal.SIGCONT)
        except ProcessLookupError:
            pass


def test_inputs_land_on_a_squeezed_host(images, emu):
    """CrossPoint with its QEMU getting 40 % of the host (see the module docstring): Browse Files and
    back with the Home pad, four times, then the book and two page turns -- every input acts the
    first time."""
    boot_crosspoint(emu, images)
    need_latching_gt911(emu)
    pid = int(open(os.path.join(ROOT, '.x4emu', emu, 'pid')).read())
    with Squeeze(pid):
        for i in range(4):
            x4emu(emu, 'tap', *BROWSE_FILES, '--quiet', 2, '--wait', 30, timeout=120)
            assert wait_activity(emu, 'FileBrowser'), f'round {i}: the tap was lost'
            x4emu(emu, 'home', '--quiet', 2, '--wait', 30, timeout=120)
            assert wait_activity(emu, 'Home'), f'round {i}: the Home pad was lost'
        x4emu(emu, 'tap', *BROWSE_FILES, '--quiet', 2, '--wait', 30, timeout=120)
        assert wait_activity(emu, 'FileBrowser')
        x4emu(emu, 'tap', 140, 240, '--quiet', 2, '--wait', 30, timeout=120)          # the book
        x4emu(emu, 'wait-text', 'Loaded ePub: /Test Book.epub', '--timeout', 120, timeout=180)
        for _ in range(2):
            x4emu(emu, 'wait-quiet', '--seconds', 2, '--timeout', 120, timeout=180)
            n = state(emu)['refresh_count']
            x4emu(emu, 'press', 'right', '--wait', 30, timeout=120)                    # a page turn
            assert state(emu)['refresh_count'] > n


# --- the stock firmware (needs the device dump and card files; skips otherwise) -------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_stock import (GOLDEN, PANEL_W, STATUS_BAR_COLS, boot_to_home, masked_diff,   # noqa: E402,F401
                        settle, stock, stock_card)


def test_stock_paused_tap_and_home_pad_are_kept(stock):
    """The stock reads the GT911 on its INT line and acts on one press frame and one release frame;
    a tap on the menu icon and the Home pad pressed and released inside one VM pause both act."""
    name, tmp = stock
    boot_to_home(name)
    settle(name, 2, 30)
    need_latching_gt911(name)
    g = paused_input(name, 'touch', json.dumps([{'x': 441, 'y': 88}]), '[]')     # the menu icon (88, 38)
    assert g['pending'], g
    settle(name, 2, 60)
    shot = tmp / 'menu.png'
    x4emu(name, 'screenshot', str(shot))
    menu = os.path.join(ROOT, 'tests', 'golden', 'stock-menu.png')
    assert masked_diff(shot, menu, (STATUS_BAR_COLS, PANEL_W - 60)) == 0, 'the paused tap did not open the menu'
    paused_input(name, 'home', True, False)
    settle(name, 2, 60)
    x4emu(name, 'screenshot', str(shot))
    assert masked_diff(shot, GOLDEN) == 0, 'the paused Home pad did not return to Home'

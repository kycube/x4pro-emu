"""S4 / D3: `x4emu record` an input script, `x4emu replay` it, and land on the same pixels.

The flow is the one `tests/test_touch_reader.py` walks by hand — boot CrossPoint, tap "Browse
Files" on the Home menu, come back with the Home pad — but recorded into a journal and replayed
onto two freshly booted instances. All three runs use `run --deterministic` (`-icount 3`: guest
time follows the instruction count, so the same instruction stream reaches Home at the same guest
millisecond), and the replay waits for the guest clock, not the host clock, before each input.

The acceptance is *identical screenshots*, not identical guest timestamps: the replay's waits are
host-paced polls of `state.uptime_us`, so an input lands a few milliseconds late. That is harmless
as long as every input is recorded at a moment when the firmware is idle — hence the `--quiet 2`
on the inputs and the `wait-quiet` between them, which is how the other tests drive CrossPoint
anyway. CrossPoint's Home screen draws no clock (its golden matches the device screenshot in 0
pixels), so nothing has to be masked out of the comparison.
"""
import json
import os
import pytest
from conftest import x4emu

BROWSE_FILES = (345, 350)      # the Home menu entry, in landscape panel pixels (test_touch_reader)
BOOT_S = 300                   # host seconds for a boot / a replay subprocess (a loaded host is slow)


def boot(name, images):
    """A deterministic CrossPoint on `name`, at Home. Nothing else: the journal's first step waits
    for its own guest time, and the recorded `--quiet 2` waits for the panel."""
    x4emu(name, 'run', '--flash', images['flash'], '--sd', images['sd'], '--fast-epd',
          '--deterministic', timeout=BOOT_S)
    x4emu(name, 'wait-text', 'Entering activity: Home', '--timeout', 180, timeout=BOOT_S)


def state(name):
    return json.loads(x4emu(name, 'state').stdout)


def journal(path):
    return [json.loads(l) for l in open(path) if l.strip()]


def diff_pixels(a, b):
    """Pixels differing anywhere on the panel (unmasked: nothing on this screen follows the clock)."""
    from PIL import Image, ImageChops
    ia, ib = Image.open(a).convert('L'), Image.open(b).convert('L')
    assert ia.size == ib.size, (ia.size, ib.size)
    d = ImageChops.difference(ia, ib)
    data = d.get_flattened_data() if hasattr(d, 'get_flattened_data') else d.getdata()
    return sum(1 for v in data if v)


@pytest.fixture
def replay_emus():
    """Two more instances for the replays (the `emu` fixture records on a third)."""
    names = ['rpa', 'rpb']
    for n in names:
        x4emu(n, 'stop', check=False)
    yield names
    for n in names:
        x4emu(n, 'stop', check=False)


def test_record_replay_same_screen(images, emu, replay_emus, tmp_path):
    flow = str(tmp_path / 'flow.jsonl')
    shots = []

    # 1. record: Home -> tap "Browse Files" -> FileBrowser -> Home pad -> Home
    boot(emu, images)
    x4emu(emu, 'wait-quiet', '--seconds', 2, '--timeout', 60)
    r = x4emu(emu, 'record', flow)
    assert flow in r.stdout, r.stdout
    x4emu(emu, 'tap', *BROWSE_FILES, '--quiet', 2, '--wait', 30, timeout=BOOT_S)
    x4emu(emu, 'wait-text', 'Entering activity: FileBrowser', '--timeout', 60)
    x4emu(emu, 'wait-quiet', '--seconds', 2, '--timeout', 60)
    x4emu(emu, 'home', '--quiet', 2, '--wait', 30, timeout=BOOT_S)
    x4emu(emu, 'wait-quiet', '--seconds', 2, '--timeout', 60)
    r = x4emu(emu, 'record', '--stop')
    assert '2 steps' in r.stdout, r.stdout
    shots.append(str(tmp_path / 'a.png'))
    x4emu(emu, 'screenshot', shots[0])

    # 2. the journal: one line per input command, in guest-time order, with the arguments a replay
    #    needs to call the same function again
    steps = journal(flow)
    assert [s['cmd'] for s in steps] == ['tap', 'home'], steps
    ts = [s['t_ms'] for s in steps]
    assert all(a < b for a, b in zip(ts, ts[1:])), ts
    assert all(t > 0 for t in ts), ts
    tap_args = steps[0]['args']
    assert tap_args['x'] == BROWSE_FILES[0] and tap_args['y'] == BROWSE_FILES[1], tap_args
    assert tap_args['ms'] == 120 and tap_args['quiet'] == 2 and tap_args['wait'] == 30, tap_args
    # non-input commands are not recorded (the wait-quiet/wait-text/screenshot above are gone)
    assert len(steps) == 2, steps

    # 3. the virtual-clock wait: guest time, not host time (this instance is idle at Home)
    before = state(emu)['uptime_us']
    r = x4emu(emu, 'wait-guest-ms', 500, '--timeout', 60)
    assert r.stdout.startswith('guest '), r.stdout
    assert state(emu)['uptime_us'] - before >= 500_000
    js = json.loads(x4emu(emu, '--json', 'wait-guest-ms', 200, '--timeout', 60).stdout)
    assert js['ok'] and js['advanced_ms'] >= 200 and js['guest_ms'] >= js['target_ms'], js

    # 4. replay the journal onto two fresh instances, human mode and --json mode.
    #    A replayed input the firmware reads while it paints draws nothing and `replay` stops with
    #    "no refresh within Ns" -- the loaded-host flake (CLAUDE.md "Lessons"; `--deterministic`
    #    stretches every paint against the host clock, and the desk Mac runs the owner's own
    #    applications). The repair is to replay the *whole* journal again from a fresh boot: the
    #    journal is untouched, so step 5's acceptance -- three runs, one screen -- is unweakened.
    for i, name in enumerate(replay_emus):
        for attempt in range(2):
            x4emu(name, 'stop', check=False)
            boot(name, images)
            try:
                if i:
                    js = json.loads(x4emu(name, '--json', 'replay', flow, timeout=BOOT_S).stdout)
                    assert js['steps'] == 2 and js['guest_ms_end'] >= ts[-1], js
                    assert [s['cmd'] for s in js['replayed']] == ['tap', 'home'], js
                else:
                    r = x4emu(name, 'replay', flow, timeout=BOOT_S)
                    assert f't={ts[0]} ms tap {BROWSE_FILES[0]} {BROWSE_FILES[1]}' in r.stdout, r.stdout
                    assert 'replayed 2 steps' in r.stdout, r.stdout
            except AssertionError as e:
                if attempt or 'no refresh within' not in str(e):
                    raise
                print(f'{name}: the replay lost an input; replaying the journal again from a fresh boot')
                continue
            break
        # the firmware took the same path: FileBrowser and back to Home
        x4emu(name, 'wait-text', 'Entering activity: FileBrowser', '--timeout', 30)
        x4emu(name, 'wait-quiet', '--seconds', 2, '--timeout', 60)
        shots.append(str(tmp_path / f'{name}.png'))
        x4emu(name, 'screenshot', shots[-1])

    # 5. the acceptance: three runs, one screen
    for other in shots[1:]:
        n = diff_pixels(shots[0], other)
        assert n == 0, f'{os.path.basename(other)} differs from the recorded run in {n} pixels'

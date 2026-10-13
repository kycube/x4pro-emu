"""S4 / D3: `x4emu record` an input script, `x4emu replay` it, and land on the same pixels.

The flow is the one `tests/test_touch_reader.py` walks by hand — boot CrossPoint, tap "Browse
Files" on the Home menu, come back with the Home pad — but recorded into a journal and replayed
onto two freshly booted instances. All three runs use `run --deterministic --icount shift=3,sleep=off`
(guest time follows the instruction count, so the same instruction stream reaches Home at the same
guest millisecond, and idle guest time passes without waiting for the host), and the replay waits for
the guest clock, not the host clock, before each input.

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
from conftest import (x4emu, instance_name, emu_state, BROWSE_FILES, boot_crosspoint, copy_images,
                      count_pixels, wait_log)

BOOT_S = 300                   # host seconds for a boot / a replay subprocess (a loaded host is slow)


# `--deterministic` is `-icount 3`; `sleep=off` on top lets the idle guest time between the inputs
# (the `--quiet 2` waits, the replay's waits for each step's t_ms) pass at once instead of in real
# time. It is also what keeps the host out of the guest clock entirely: with sleep=on an idle guest
# waits in real time, so how long the host took leaks into guest time.
ICOUNT = 'shift=3,sleep=off'


def boot(name, images):
    """A deterministic CrossPoint on `name`, at Home. Nothing else: the journal's first step waits
    for its own guest time, and the recorded `--quiet 2` waits for the panel."""
    boot_crosspoint(name, images, '--deterministic', '--icount', ICOUNT, home_s=180, timeout=BOOT_S)


def journal(path):
    return [json.loads(l) for l in open(path) if l.strip()]


def diff_pixels(a, b):
    """Pixels differing anywhere on the panel (unmasked: nothing on this screen follows the clock)."""
    from PIL import Image, ImageChops
    ia, ib = Image.open(a).convert('L'), Image.open(b).convert('L')
    assert ia.size == ib.size, (ia.size, ib.size)
    return count_pixels(ImageChops.difference(ia, ib))


@pytest.fixture
def replay_emus():
    """Two more instances for the replays (the `emu` fixture records on a third), under the run's
    X4EMU_TEST_PREFIX like every other test instance."""
    names = [instance_name('rpa'), instance_name('rpb')]
    for n in names:
        x4emu(n, 'stop', check=False)
    yield names
    for n in names:
        x4emu(n, 'stop', check=False)


def test_record_replay_same_screen(images_base, images, emu, replay_emus, tmp_path):
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
    # only the inputs: the wait-quiet/wait-text/screenshot above are not recorded
    assert [s['cmd'] for s in steps] == ['tap', 'home'], steps
    ts = [s['t_ms'] for s in steps]
    assert all(a < b for a, b in zip(ts, ts[1:])), ts
    assert all(t > 0 for t in ts), ts
    tap_args = steps[0]['args']
    assert tap_args['x'] == BROWSE_FILES[0] and tap_args['y'] == BROWSE_FILES[1], tap_args
    assert tap_args['ms'] == 120 and tap_args['quiet'] == 2 and tap_args['wait'] == 30, tap_args

    # 3. the virtual-clock wait: guest time, not host time (this instance is idle at Home)
    before = emu_state(emu)['uptime_us']
    r = x4emu(emu, 'wait-guest-ms', 500, '--timeout', 60)
    assert r.stdout.startswith('guest '), r.stdout
    assert emu_state(emu)['uptime_us'] - before >= 500_000
    js = json.loads(x4emu(emu, '--json', 'wait-guest-ms', 200, '--timeout', 60).stdout)
    assert js['ok'] and js['advanced_ms'] >= 200 and js['guest_ms'] >= js['target_ms'], js

    # 4. replay the journal onto two fresh instances, human mode and --json mode. Each replayed step
    #    is delivered once: `x4emu` holds an input in guest time until the firmware has consumed it
    #    and its release (docs/x4emu.md "Input delivery"), so the replay no longer re-runs the journal
    #    when the host is loaded. Each boots its own copy of the images the recording booted from, and
    #    no finished instance keeps running: with sleep=off an idle guest spins a host core.
    x4emu(emu, 'stop')
    for i, name in enumerate(replay_emus):
        boot(name, copy_images(images_base, tmp_path / name))
        if i:
            js = json.loads(x4emu(name, '--json', 'replay', flow, timeout=BOOT_S).stdout)
            assert js['steps'] == 2 and js['guest_ms_end'] >= ts[-1], js
            assert [s['cmd'] for s in js['replayed']] == ['tap', 'home'], js
        else:
            r = x4emu(name, 'replay', flow, timeout=BOOT_S)
            assert f't={ts[0]} ms tap {BROWSE_FILES[0]} {BROWSE_FILES[1]}' in r.stdout, r.stdout
            assert 'replayed 2 steps' in r.stdout, r.stdout
        # the firmware took the same path: FileBrowser and back to Home (the boot's Home is the first)
        x4emu(name, 'wait-text', 'Entering activity: FileBrowser', '--timeout', 30)
        wait_log(name, 'Entering activity: Home', count=2)
        x4emu(name, 'wait-quiet', '--seconds', 2, '--timeout', 60)
        shots.append(str(tmp_path / f'{name}.png'))
        x4emu(name, 'screenshot', shots[-1])
        x4emu(name, 'stop')

    # 5. the acceptance: three runs, one screen
    for other in shots[1:]:
        n = diff_pixels(shots[0], other)
        assert n == 0, f'{os.path.basename(other)} differs from the recorded run in {n} pixels'

"""wait-quiet in guest time, and the order a parallel `make test` hands tests out in.

`x4emu wait-quiet S` (and every input's `--quiet S`) returns once the panel has been idle for S
seconds of guest time, counted from the later of the last refresh and the last input, so a panel
that is already idle returns at once (docs/x4emu.md, "Panel"). The last input is the one `x4emu`
delivered (`.x4emu/NAME/last_input.json`, tied to the QEMU pid), or one that moved the board's input
counters some other way, which then counts from when wait-quiet saw it. The fast half checks the
mark's staleness rules and tests/conftest.py's schedule order; the emulator half boots CrossPoint.
"""
import json
import os
import sys

from conftest import ROOT, x4emu, instance_name, schedule_order, UNKNOWN_DURATION_S

sys.path.insert(0, ROOT)
from x4emu import commands as c                                   # noqa: E402

BROWSE_FILES_GT911 = (129, 345)   # CrossPoint's Home "Browse Files" in the GT911's portrait frame
INPUT = '/machine/x4pro-input'


class Item:
    def __init__(self, nodeid):
        self.nodeid = nodeid


def test_schedule_pairs_each_long_test_with_a_short_one():
    d = {'a': 100, 'b': 80, 'c': 60, 'd': 40, 'e': 1, 'f': 0.5, 'g': 0.1, 'h': 0.2, 'i': 5}
    order = [it.nodeid for it in schedule_order([Item(k) for k in d], d, 3)]
    # xdist's first batch is two consecutive items per worker: the three longest each share theirs
    # with one of the three shortest, then the rest follow longest first
    assert order == ['a', 'g', 'b', 'h', 'c', 'f', 'd', 'i', 'e'], order
    assert [it.nodeid for it in schedule_order([Item(k) for k in d], d, 1)] == \
        sorted(d, key=lambda k: -d[k])


def test_schedule_counts_an_unknown_test_as_long():
    d = {'a': 40, 'b': 1}
    order = [it.nodeid for it in schedule_order([Item('b'), Item('new'), Item('a')], d, 1)]
    assert UNKNOWN_DURATION_S < 40 and order == ['a', 'new', 'b'], order


def test_an_input_mark_counts_only_for_its_own_process(tmp_path, request):
    name = instance_name(request.node.name)
    path = c.last_input_file(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, 'w') as f:
            json.dump({'pid': 4242, 'uptime_us': 5_000_000, 'sig': [1, 2, 2, 2]}, f)
        assert c.read_input_mark(name, 4242, 6_000_000)['uptime_us'] == 5_000_000
        assert c.read_input_mark(name, 4243, 6_000_000) is None, 'a mark from another QEMU process'
        assert c.read_input_mark(name, 4242, 4_000_000) is None, 'a mark from the future of this boot'
        assert c.read_input_mark(name, None, 6_000_000) is None
        c.forget_input(name)
        assert c.read_input_mark(name, 4242, 6_000_000) is None
    finally:
        c.forget_input(name)
        try:
            os.rmdir(os.path.dirname(path))
        except OSError:
            pass


def test_input_signature():
    st = {'gt911': {'input_seq': 7}, 'gpio_in': {'changes': {'left': 2, 'right': 4, 'power': 3}}}
    assert c.input_signature(st) == [7, 2, 4, 3]
    assert c.input_signature({'gt911': {}}) is None           # a QEMU without the counters


def quiet(name, seconds=2):
    return json.loads(x4emu(name, '--json', 'wait-quiet', '--seconds', seconds, '--timeout', 60).stdout)


def test_wait_quiet_returns_at_once_when_idle_and_waits_after_any_input(images, emu):
    x4emu(emu, 'run', '--flash', images['flash'], '--sd', images['sd'], '--fast-epd')
    x4emu(emu, 'wait-text', 'Entering activity: Home', '--timeout', 90)
    first = quiet(emu)
    assert first['ok'] and first['idle_s'] >= 2, first
    # 1. already idle: no second wait
    again = quiet(emu)
    assert again['ok'] and again['waited_s'] < 0.5 and again['idle_s'] >= 2, again
    # 2. an input x4emu delivered: the idle time restarts at its end (Right moves the selection)
    n = json.loads(x4emu(emu, 'state').stdout)['refresh_count']
    x4emu(emu, 'press', 'right', '--wait', 20)
    after = quiet(emu)
    assert after['ok'] and after['waited_s'] >= 1.0, after
    # 3. an input that bypassed x4emu (raw QMP): seen through the GT911 input counter, so the wait
    #    counts from the call and the screen it leads to is drawn before wait-quiet returns
    for value in (json.dumps([{'x': BROWSE_FILES_GT911[0], 'y': BROWSE_FILES_GT911[1]}]), '[]'):
        x4emu(emu, 'qmp', json.dumps({'execute': 'qom-set',
                                      'arguments': {'path': INPUT, 'property': 'touch', 'value': value}}))
    raw = quiet(emu)
    assert raw['ok'] and raw['waited_s'] >= 1.0, raw
    log = open(os.path.join(ROOT, '.x4emu', emu, 'console.log'), errors='replace').read()
    assert 'Entering activity: FileBrowser' in log, 'the raw tap did not act'
    assert json.loads(x4emu(emu, 'state').stdout)['refresh_count'] > n
    # 4. a new boot never inherits the old boot's mark
    x4emu(emu, 'stop')
    assert not os.path.exists(c.last_input_file(emu))

"""S4 (developer experience): `x4emu/api.py` (in-process command execution), `x4emu shell`,
`x4emu watch`, and `tools/x4emu_mcp.py` importing the package instead of shelling out to it.

One booted CrossPoint instance (the `emu` fixture) is reused for parts (a)-(d): none of them
disturb the screen destructively — status/state/screenshot are read-only, the shell's failing `tap`
line never reaches the emulator (it fails in argparse before any QMP call), and watch's own tap
uses the same "Browse Files" coordinate `tests/test_touch_reader.py`/`test_replay.py` already use.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from x4emu import api  # noqa: E402

from conftest import PY, X4EMU, x4emu

BOOT_S = 300
BROWSE_FILES = (345, 350)


def boot(name, images):
    """A CrossPoint instance at Home, idle."""
    x4emu(name, 'run', '--flash', images['flash'], '--sd', images['sd'], '--fast-epd', timeout=BOOT_S)
    x4emu(name, 'wait-text', 'Entering activity: Home', '--timeout', 180, timeout=BOOT_S)
    x4emu(name, 'wait-quiet', '--seconds', 2, '--timeout', 60)


def same_shape(a, b, path='$'):
    """Structurally equal, tolerating live numbers: same keys, same list lengths, non-numeric
    leaves equal, numeric leaves only required to have the same type (a counter or a timestamp
    read a moment apart will differ in value, not in kind)."""
    if isinstance(a, dict):
        assert isinstance(b, dict), f'{path}: {a!r} vs {b!r}'
        assert a.keys() == b.keys(), f'{path}: keys differ {a.keys()} vs {b.keys()}'
        for k in a:
            same_shape(a[k], b[k], f'{path}.{k}')
    elif isinstance(a, list):
        assert isinstance(b, list), f'{path}: {a!r} vs {b!r}'
        assert len(a) == len(b), f'{path}: list length differs {a!r} vs {b!r}'
        for i, (x, y) in enumerate(zip(a, b)):
            same_shape(x, y, f'{path}[{i}]')
    elif isinstance(a, bool) or isinstance(b, bool):
        assert a == b, f'{path}: {a!r} vs {b!r}'
    elif isinstance(a, (int, float)):
        assert isinstance(b, (int, float)), f'{path}: {a!r} vs {b!r}'
    else:
        assert a == b, f'{path}: {a!r} vs {b!r}'


# ---------------------------------------------------------------- (a) x4emu/api.py
def test_api_run_in_process(images, emu):
    boot(emu, images)

    d = api.run(['--name', emu, 'status'])
    assert isinstance(d, dict) and d.get('running') is True and d.get('name') == emu, d

    bad = api.run(['--name', emu, 'nonsense'])
    assert isinstance(bad, dict) and 'error' in bad and bad.get('rc'), bad

    timed_out = api.run(['--name', emu, 'wait-text', 'not-on-screen-anywhere', '--timeout', '1'])
    assert timed_out.get('found') is False and 'error' in timed_out and timed_out.get('rc') == 1, timed_out

    # run_text mirrors human mode, and raises (never SystemExit) on a genuine failure
    text = api.run_text(['status'], name=emu)
    assert text.strip() == f'{emu}: pid {d["pid"]}, running, running=True', repr(text)
    with pytest.raises(RuntimeError):
        api.run_text(['tap', 'abc', 'def'], name=emu)
    # ... and the instance is unaffected by that failed call
    assert api.run(['--name', emu, 'status'])['running'] is True


# ---------------------------------------------------------------- (b) x4emu shell
def test_shell_repl(images, emu, tmp_path):
    boot(emu, images)
    shot = str(tmp_path / 'a.png')

    # the reference: the same three commands run directly through the CLI
    ref_status = x4emu(emu, 'status').stdout
    ref_state = json.loads(x4emu(emu, '--json', 'state').stdout)
    ref_shot_line = x4emu(emu, 'screenshot', shot).stdout

    script = f'status\nstate\nscreenshot {shot}\ntap notanumber notanumber\nstatus\nexit\n'
    r = subprocess.run([PY, X4EMU, '--name', emu, 'shell'], input=script,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r

    dec = json.JSONDecoder()
    out = r.stdout
    status_line, rest = out.split('\n', 1)
    assert status_line + '\n' == ref_status, (status_line, ref_status)
    state_obj, idx = dec.raw_decode(rest)
    same_shape(ref_state, state_obj)
    rest = rest[idx:]
    while rest.startswith('\n'):
        rest = rest[1:]
    shot_line, rest = rest.split('\n', 1)
    assert shot_line + '\n' == ref_shot_line, (shot_line, ref_shot_line)

    # the failing `tap` line was reported (to stderr) and the loop kept going: the final `status`
    # after it still ran and printed its own line
    assert 'invalid int value' in r.stderr, r.stderr
    assert rest.strip() == status_line, (rest, status_line)


# ---------------------------------------------------------------- (c) x4emu watch
def test_watch_live_view(images, emu, tmp_path):
    boot(emu, images)
    live = str(tmp_path / 'live.png')
    proc = subprocess.Popen([PY, X4EMU, '--name', emu, 'watch', '--port', '0', '--seconds', '40',
                             '--out', live], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        url = proc.stdout.readline().strip()
        assert url.startswith('http://127.0.0.1:'), (url, proc.stderr.read())

        state1 = json.loads(urllib.request.urlopen(url + 'state.json', timeout=10).read())
        assert 'refresh_count' in state1 and 'uptime_us' in state1, state1
        panel1 = urllib.request.urlopen(url + 'panel.png', timeout=10).read()
        assert panel1[:8] == b'\x89PNG\r\n\x1a\n', panel1[:16]

        # this is the point of the test: an ordinary x4emu command still works while `watch` is
        # holding the HTTP server (the QMP socket is single-client, and each of watch's requests
        # only holds it briefly)
        r = x4emu(emu, 'tap', *BROWSE_FILES, '--quiet', 2, '--wait', 30, timeout=BOOT_S)
        assert 'refresh' in r.stdout

        t0 = time.time()
        refresh1 = state1['refresh_count']
        state2 = state1
        while time.time() - t0 < 30 and state2['refresh_count'] == refresh1:
            time.sleep(0.3)
            state2 = json.loads(urllib.request.urlopen(url + 'state.json', timeout=10).read())
        assert state2['refresh_count'] > refresh1, (refresh1, state2)

        panel2 = urllib.request.urlopen(url + 'panel.png', timeout=10).read()
        assert panel2 != panel1

        t0 = time.time()
        while time.time() - t0 < 10 and (not os.path.exists(live) or open(live, 'rb').read() != panel2):
            time.sleep(0.2)
        assert os.path.exists(live), 'watch --out file was never written'
        assert open(live, 'rb').read() == panel2, '--out file was not updated to the latest panel.png'
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)

    # bring the instance back to a quiet Home for the next part of the suite
    x4emu(emu, 'wait-quiet', '--seconds', 2, '--timeout', 60)
    x4emu(emu, 'home', '--quiet', 2, '--wait', 20, timeout=BOOT_S)
    x4emu(emu, 'wait-quiet', '--seconds', 2, '--timeout', 60)


# ---------------------------------------------------------------- (d) tools/x4emu_mcp.py
def test_mcp_module_imports_and_calls_in_process(images, emu):
    boot(emu, images)
    import importlib.util
    spec = importlib.util.spec_from_file_location('x4emu_mcp_test', os.path.join(ROOT, 'tools', 'x4emu_mcp.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # the @server.tool() decorator returns the original function (see mcp.server.mcpserver.MCPServer.tool)
    status = mod.emu_status(emu)
    assert isinstance(status, str) and 'running' in status and emu in status, status

    state = mod.emu_state(emu)
    assert isinstance(state, dict) and 'refresh_count' in state and 'uptime_us' in state, state

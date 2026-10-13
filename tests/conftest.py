import hashlib, os, subprocess, sys, time, pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
X4EMU = os.path.join(ROOT, 'tools', 'x4emu')
BUILD = os.path.join(ROOT, 'firmware', '.pio', 'build', 'x4pro')
QEMU = os.environ.get('X4EMU_QEMU', os.path.join(ROOT, 'qemu', 'build', 'qemu-system-xtensa'))

def x4emu(name, *args, check=True, timeout=120):
    r = subprocess.run([PY, X4EMU, '--name', name, *map(str, args)], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise AssertionError(f'x4emu {args} failed ({r.returncode}):\n{r.stdout}\n{r.stderr}')
    return r

# `x4emu --name NAME` becomes .x4emu/NAME/ and part of two UNIX socket paths, and sun_path holds
# 104 bytes on macOS, 108 on Linux (CLAUDE.md, "Lessons"). pytest node names are long and the CI
# checkout (/home/runner/work/x4pro-emu/x4pro-emu) is 20 bytes longer than the desk Mac's, so
# `pytest-test_mcp_module_imports_and_calls_in_process` measured 93 bytes here and 109 on CI -- one
# over the limit, which is why that job was red from 2026-09-07 while the same suite passed on the
# Mac. `instance_name` clamps to what actually fits the checkout it is running in.
SOCK_MAX = 104          # the smaller of the two caps, so a name that fits here fits everywhere


def instance_name(node_name, node_id=None):
    """The `--name` for one test: readable, unique, and short enough for the socket paths.

    Two test files can hold the same function name (`test_the_importable_api_matches_the_cli` is in
    both test_stockdev.py and test_stockpatch.py), and a shared name means a shared `.x4emu/NAME/`
    with its console.log and sockets, so the hash covers `node_id` -- pass `request.node.nodeid`
    where a collision is possible; the readable head still comes from the function name.

    `X4EMU_TEST_PREFIX` (e.g. `a-`) goes in front of the test's part, so two pytest runs side by side --
    two agents, or two workers -- never share an instance (each test stops its own name first)."""
    name = 'pytest-' + os.environ.get('X4EMU_TEST_PREFIX', '') + \
        node_name.replace('[', '-').replace(']', '').replace('=', '-')
    room = SOCK_MAX - len(os.path.join(ROOT, '.x4emu')) - len('/console.sock') - 2   # '/' + NUL
    if len(name) > room or node_id:
        h = hashlib.sha1((node_id or node_name).encode()).hexdigest()[:8]
        name = name[:min(len(name), room - 9)] + '-' + h
    return name


def x4emu_input(name, *args, **kw):
    """An input command (`tap`, `home`, `press`, ... usually with `--wait`), delivered once.

    This used to repeat an input that drew nothing ("no refresh within Ns" although the firmware had
    read the GT911 frame): every full run lost about one input, in a different test each time. The
    cause was the GT911 model overwriting an unread frame -- a release landing between the firmware's
    status read and its clear was wiped unseen -- and button holds measured in host milliseconds; the
    model now latches frames and `x4emu` holds every input until the firmware has seen it and its
    release (docs/x4emu.md "Input delivery", tests/test_input_delivery.py). A test that still needs a
    retry must say why next to it."""
    return x4emu(name, *args, **kw)


@pytest.fixture(scope='session')
def images_base(tmp_path_factory):
    """Built once per session: the flash image from the firmware build and an MBR/FAT32 SD image
    with the test book. Tests never boot these directly (see `images`)."""
    if not os.path.exists(os.path.join(BUILD, 'firmware.bin')):
        pytest.skip('firmware not built (cd firmware && pio run -e x4pro)')
    if not os.path.exists(QEMU):
        pytest.skip('qemu not built')
    d = tmp_path_factory.mktemp('images')
    flash = d / 'flash.bin'
    sd = d / 'sd.img'
    subprocess.run([PY, os.path.join(ROOT, 'tools', 'mkflash.py'), str(flash), '--build', BUILD], check=True, stdout=subprocess.DEVNULL)
    empty = d / 'sdroot'; empty.mkdir()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from mkepub import make_epub
    make_epub(str(empty / 'Test Book.epub'))
    subprocess.run([PY, os.path.join(ROOT, 'tools', 'mksd.py'), str(sd), '--size', '64M', '--src', str(empty)], check=True, stdout=subprocess.DEVNULL)
    return {'flash': str(flash), 'sd': str(sd), 'dir': d}

@pytest.fixture
def images(images_base, tmp_path):
    """A private copy of the flash and card images for one test. CrossPoint writes to both (NVS
    settings in the flash, reading positions and recent books on the card), so a shared image let one
    test's state leak into the next: a Home screen with a "recent book" card, a reader that opened on
    page 3 (2026-09-07). Copying 16 + 64 MB takes well under a second."""
    import shutil
    flash = tmp_path / 'flash.bin'; sd = tmp_path / 'sd.img'
    shutil.copyfile(images_base['flash'], flash); shutil.copyfile(images_base['sd'], sd)
    return {'flash': str(flash), 'sd': str(sd), 'dir': images_base['dir']}

@pytest.fixture
def emu(images, request):
    name = instance_name(request.node.name, request.node.nodeid)
    x4emu(name, 'stop', check=False)
    yield name
    x4emu(name, 'stop', check=False)


# --- --golden-drift FILE: the upstream watch's CrossPoint-latest job (docs/upstream.md) ----------
# Off unless the option is given: without it the hook below returns at once and no outcome changes.
# With it the run answers "did the emulator break?" for a firmware the goldens were not made from:
#  - a test in GOLDEN_ORACLES that fails in one of its own comparisons against a device-made golden
#    (a bare assert in the test body whose message matches, not an x4emu command), while its
#    emulator still runs with no unknown panel command and no crash marker in its console, is
#    reported as xfail and recorded in FILE as golden drift;
#  - a test that passed while its emulator shows a crash marker or an unknown panel command fails;
#  - every other failure stays a failure.
# FILE gets one JSON line per drift or unhealthy test (`tools/upstream_check.py tests-summary`).
GOLDEN_ORACLES = {
    # test function -> regex over the failing assert's message (the device's own screenshots)
    'test_home_screen_matches_device_screenshot': r'% of pixels differ',
    'test_reader_pages_match_device_screenshots': r'differs from the device screenshot|base_frame_diff\(',
}


def pytest_addoption(parser):
    parser.addoption('--golden-drift', metavar='FILE', default=None,
                     help='upstream watch only: a mismatch against a device golden on a healthy emulator '
                          'becomes xfail and a JSON line in FILE (docs/upstream.md)')


def _emu_health(name):
    """(problems, running) of one instance: crash markers in its console, unknown panel commands."""
    import importlib.util, json
    spec = importlib.util.spec_from_file_location('upstream_check', os.path.join(ROOT, 'tools', 'upstream_check.py'))
    uc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(uc)
    problems = uc.crash_markers(os.path.join(ROOT, '.x4emu', name, 'console.log'))
    r = x4emu(name, 'state', check=False, timeout=30)
    try:
        st = json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        st = None
    if st and st.get('epd_unknown_cmds'):
        problems.append(f'{st["epd_unknown_cmds"]} unknown panel command(s), last {st.get("epd_last_unknown")}')
    return problems, st is not None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    path = item.config.getoption('--golden-drift')
    rep = outcome.get_result()
    if not path or rep.when != 'call' or rep.skipped or 'emu' not in getattr(item, 'funcargs', {}):
        return
    import json, re
    problems, running = _emu_health(item.funcargs['emu'])
    record = None
    if rep.passed and problems:
        rep.outcome = 'failed'
        rep.longrepr = 'golden-drift check: the emulator was unhealthy after a passing test: ' + '; '.join(problems)
        record = {'kind': 'unhealthy', 'test': item.nodeid, 'message': '; '.join(problems)}
    elif rep.failed and call.excinfo is not None:
        pattern = GOLDEN_ORACLES.get(getattr(item, 'originalname', None) or item.name)
        exc = call.excinfo
        msg = str(exc.value)
        golden = (pattern and exc.errisinstance(AssertionError)
                  and exc.traceback[-1].name == item.function.__name__ and re.search(pattern, msg))
        if golden and running and not problems:
            first = next((x.strip() for x in msg.splitlines() if re.search(pattern, x)), msg.strip()[:200])
            rep.outcome = 'skipped'
            rep.wasxfail = f'golden drift: {first}'
            record = {'kind': 'golden-drift', 'test': item.nodeid, 'message': first}
    if record:
        with open(path, 'a') as f:
            f.write(json.dumps(record) + '\n')

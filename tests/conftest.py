import hashlib, json, os, re, shutil, subprocess, sys, time
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TOOLS = os.path.join(ROOT, 'tools')
X4EMU = os.path.join(TOOLS, 'x4emu')
BUILD = os.path.join(ROOT, 'firmware', '.pio', 'build', 'x4pro')
QEMU = os.environ.get('X4EMU_QEMU', os.path.join(ROOT, 'qemu', 'build', 'qemu-system-xtensa'))

# tools/ on the import path once, for every test module (upstream_check, nvsedit, stockver, ...).
# This directory is there already: pytest puts a conftest's directory first when it has no __init__.py,
# which is what makes `from conftest import ...` and `from mkepub import ...` work.
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

from mkepub import make_epub                                     # noqa: E402  (the test book)


def x4emu(name, *args, check=True, timeout=120):
    r = subprocess.run([PY, X4EMU, '--name', name, *map(str, args)], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise AssertionError(f'x4emu {args} failed ({r.returncode}):\n{r.stdout}\n{r.stderr}')
    return r


def _x4emu_dir():
    return os.path.join(os.environ.get('X4EMU_ROOT') or ROOT, '.x4emu')    # docs/x4emu.md


def instance_dir(name):
    """`.x4emu/NAME/`: the instance's sockets, run.json and logs."""
    return os.path.join(_x4emu_dir(), name)


def emu_log(name, file='console.log'):
    """The text of one of the instance's logs: console.log (USB Serial/JTAG), uart0.log, qemu.log."""
    with open(os.path.join(instance_dir(name), file), errors='replace') as f:
        return f.read()


def emu_state(name):
    """`x4emu state` as a dict."""
    return json.loads(x4emu(name, 'state').stdout)


def wait_for(fn, timeout, what, every=0.5):
    """Call `fn` every `every` host seconds until it returns something true, and return that; after
    `timeout` host seconds raise AssertionError naming `what`."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(every)
    raise AssertionError(f'timeout waiting for {what}')


def wait_log(name, text, count=1, timeout=30, file='console.log'):
    """Wait until `text` has appeared `count` times in the instance's log. `x4emu wait-text` finds the
    first one, and the console holds the whole run, reset and deep-sleep wake included: a second
    visit to a screen ("Entering activity: Home" after the Home pad, or after a wake) needs a count."""
    wait_for(lambda: emu_log(name, file).count(text) >= count, timeout,
             f'{text!r} {count} time(s) in {file}', every=0.2)


def count_pixels(im, lo=1, hi=255):
    """How many pixels of a mode 'L' or '1' image have a value in lo..hi: the default counts the
    non-zero pixels of a difference image, (0, 127) the dark ones of a screenshot. A histogram, so no
    Python loop over 384,000 pixels and no `getdata()` (deprecated from Pillow 12 on)."""
    return sum(im.histogram()[lo:hi + 1])


# `x4emu --name NAME` becomes .x4emu/NAME/ and part of two UNIX socket paths, and sun_path holds
# 104 bytes on macOS, 108 on Linux (docs/NEXT_PHASE.md §7). pytest node names are long and the CI
# checkout (/home/runner/work/x4pro-emu/x4pro-emu) is 20 bytes longer than the desk Mac's, so
# `pytest-test_mcp_module_imports_and_calls_in_process` measured 93 bytes here and 109 on CI -- one
# over the limit, which is why that job was red from 2026-09-07 while the same suite passed on the
# Mac. `instance_name` clamps to what actually fits the checkout it is running in.
SOCK_MAX = 104          # the smaller of the two caps, so a name that fits here fits everywhere

# Under pytest-xdist (`make test` runs the suite on several workers) every worker is its own pytest
# process: X4EMU_TEST_PREFIX gains the worker number (`gw3` -> `3`, so `sp-` becomes `sp3-`, no
# prefix becomes `gw3-`), and two workers never share an instance even for the names that are not
# derived from the test node (`instance_name('rpa')`). Set here, at import, before any test runs.
_WORKER = os.environ.get('PYTEST_XDIST_WORKER')
if _WORKER:
    _base = os.environ.get('X4EMU_TEST_PREFIX', '').rstrip('-')
    os.environ['X4EMU_TEST_PREFIX'] = (_base or 'gw') + _WORKER.removeprefix('gw') + '-'


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
    room = SOCK_MAX - len(_x4emu_dir()) - len('/console.sock') - 2   # '/' + NUL
    if len(name) > room or node_id:
        h = hashlib.sha1((node_id or node_name).encode()).hexdigest()[:8]
        name = name[:min(len(name), room - 9)] + '-' + h
    return name


_clonefile = None       # libSystem's clonefile(2), looked up on first use


def copy_image(src, dst):
    """A private copy of a flash or card image for one test (the firmware writes to both). On macOS an
    APFS clone: instant and nothing written, copy-on-write keeps the source as it is. A full run
    copies about 8 GB of 16 MB flash and 64/256 MB card images, and a parallel run starts with every
    worker copying at once, while the other workers' emulators read their cards. Elsewhere, or when
    the clone fails (another volume), a plain copy. Returns `dst` as a string."""
    global _clonefile
    src, dst = str(src), str(dst)
    if sys.platform == 'darwin':
        try:
            if _clonefile is None:
                import ctypes
                _clonefile = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True).clonefile
            if os.path.lexists(dst):
                os.remove(dst)
            if _clonefile(src.encode(), dst.encode(), 0) == 0:
                os.chmod(dst, 0o644)                # a clone keeps the source's mode
                return dst
        except (OSError, AttributeError):
            pass
    shutil.copyfile(src, dst)
    return dst


def copy_images(src, d):
    """`copy_image` of an images dict's flash and card into directory `d`, as a new dict."""
    os.makedirs(d, exist_ok=True)
    return {k: copy_image(src[k], os.path.join(str(d), os.path.basename(src[k]))) for k in ('flash', 'sd')}


@pytest.fixture(scope='session')
def images_base(tmp_path_factory):
    """Built once per session (per worker): the flash image from the firmware build and an MBR/FAT32
    SD image with the test book. Tests never boot these directly (see `images`)."""
    if not os.path.exists(os.path.join(BUILD, 'firmware.bin')):
        pytest.skip('firmware not built (cd firmware && pio run -e x4pro)')
    if not os.path.exists(QEMU):
        pytest.skip('qemu not built')
    d = tmp_path_factory.mktemp('images')
    flash = d / 'flash.bin'
    sd = d / 'sd.img'
    subprocess.run([PY, os.path.join(TOOLS, 'mkflash.py'), str(flash), '--build', BUILD], check=True, stdout=subprocess.DEVNULL)
    books = d / 'sdroot'; books.mkdir()
    make_epub(str(books / 'Test Book.epub'))
    subprocess.run([PY, os.path.join(TOOLS, 'mksd.py'), str(sd), '--size', '64M', '--src', str(books)], check=True, stdout=subprocess.DEVNULL)
    return {'flash': str(flash), 'sd': str(sd)}


@pytest.fixture
def images(images_base, tmp_path):
    """A private copy (`copy_image`) of the flash and card images for one test, `tmp_path/flash.bin`
    and `tmp_path/sd.img`. CrossPoint writes to both (NVS settings in the flash, reading positions and
    recent books on the card), so a shared image let one test's state leak into the next: a Home
    screen with a "recent book" card, a reader that opened on page 3 (2026-09-07)."""
    return copy_images(images_base, tmp_path)


@pytest.fixture
def emu(images, request):
    """This test's instance name, stopped before and after the test. It asks for `images` so that a
    test using it skips when the firmware or QEMU is not built."""
    name = instance_name(request.node.name, request.node.nodeid)
    x4emu(name, 'stop', check=False)
    yield name
    x4emu(name, 'stop', check=False)


# --- CrossPoint on the test images: the walks several tests share ------------------------------
BROWSE_FILES = (345, 350)      # CrossPoint's Home menu entry "Browse Files", landscape panel pixels
BOOK_ROW = (140, 240)          # the first row of its file list: the test book, the card's only file


def boot_crosspoint(name, images, *run_args, home_s=90, quiet_s=0, timeout=120):
    """`x4emu run` CrossPoint from `images` (`--fast-epd` plus `run_args`) and wait up to `home_s`
    for its Home activity; with `quiet_s`, also until the panel has been idle that long (the Home
    paint done, ready for a screenshot or an input). `timeout`: host seconds for the run command."""
    x4emu(name, 'run', '--flash', images['flash'], '--sd', images['sd'], '--fast-epd', *run_args, timeout=timeout)
    x4emu(name, 'wait-text', 'Entering activity: Home', '--timeout', home_s, timeout=home_s + 30)
    if quiet_s:
        x4emu(name, 'wait-quiet', '--seconds', quiet_s, '--timeout', 60)


def open_test_book(name, shots):
    """From CrossPoint's Home to page 1 of the test book, painted and idle: Browse Files, then the
    book's row. `shots/browser.png` is the file list (for the artifacts when the second tap fails)."""
    x4emu(name, 'wait-quiet', '--seconds', 2, '--timeout', 60)
    x4emu(name, 'tap', *BROWSE_FILES, '--wait', 20)
    x4emu(name, 'wait-text', 'Entering activity: FileBrowser', '--timeout', 30)
    x4emu(name, 'wait-quiet', '--seconds', 1.5, '--timeout', 60)
    x4emu(name, 'screenshot', os.path.join(str(shots), 'browser.png'))
    x4emu(name, 'tap', *BOOK_ROW, '--wait', 20)
    x4emu(name, 'wait-text', 'Loaded ePub: /Test Book.epub', '--timeout', 90)
    x4emu(name, 'wait-quiet', '--seconds', 3, '--timeout', 120)


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
    parser.addoption('--record-durations', metavar='FILE', default=None,
                     help='write every test\'s duration (setup + call + teardown, seconds) to FILE as JSON '
                          '(`make test-durations` refreshes tests/durations.json this way)')


# --- the order of a parallel run: longest first ------------------------------------------------
# `make test` runs pytest-xdist with --maxschedchunk=1, and xdist hands tests out in collection
# order, so the collection is sorted by tests/durations.json (seconds per node id, measured by
# `make test-durations`): the long stock tests start at once instead of last. A test the file does
# not know yet counts as long (it may be a new stock walk), so it starts early too. Only under xdist:
# a sequential run keeps the files' own order. Every worker sorts its own collection the same way
# (xdist requires identical collections).
#
# Plain longest-first is not quite it: xdist keeps two tests queued on every worker (a worker needs
# the next item before it can finish the current one), and its first batch is two consecutive items
# per worker, so the two longest tests would run back to back on worker 0. Each of the first N (N =
# workers) is therefore paired with one of the shortest, which runs in the long one's shadow.
DURATIONS = os.path.join(ROOT, 'tests', 'durations.json')
UNKNOWN_DURATION_S = 30.0


def load_durations(path=DURATIONS):
    try:
        with open(path) as f:
            return {k: float(v) for k, v in json.load(f).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def schedule_order(items, durations, workers):
    """`items` longest first, the first `workers` of them each followed by one of the shortest."""
    est = lambda it: durations.get(it.nodeid, UNKNOWN_DURATION_S)       # noqa: E731
    ranked = sorted(items, key=lambda it: -est(it))                      # stable: ties keep file order
    n = min(workers, len(ranked) // 2) if workers > 1 else 0
    head, rest = ranked[:n], ranked[n:]
    tails, rest = rest[len(rest) - n:][::-1], rest[:len(rest) - n]
    paired = [x for pair in zip(head, tails) for x in pair]
    return paired + rest


def pytest_collection_modifyitems(session, config, items):
    if not _WORKER:
        return
    workers = int(os.environ.get('PYTEST_XDIST_WORKER_COUNT', '1'))
    items[:] = schedule_order(items, load_durations(), workers)


# --record-durations FILE: summed per node id in the process that sees every report (a serial run,
# or xdist's controller, which gets the workers' reports), merged into FILE at the end.
_durations = None


def pytest_configure(config):
    global _durations
    if config.getoption('--record-durations') and not _WORKER:
        _durations = {}


def pytest_runtest_logreport(report):
    if _durations is not None:
        _durations[report.nodeid] = _durations.get(report.nodeid, 0.0) + report.duration


def pytest_sessionfinish(session, exitstatus):
    if not _durations:
        return
    path = session.config.getoption('--record-durations')
    old = load_durations(path)
    old.update({k: round(v, 1) for k, v in _durations.items()})
    with open(path, 'w') as f:
        json.dump(dict(sorted(old.items())), f, indent=0)
        f.write('\n')


def _emu_health(name):
    """(problems, running) of one instance: crash markers in its console, unknown panel commands."""
    import upstream_check                                       # tools/, on sys.path above
    problems = upstream_check.crash_markers(os.path.join(instance_dir(name), 'console.log'))
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
    if not path:
        return
    rep = outcome.get_result()
    if rep.when != 'call' or rep.skipped or 'emu' not in getattr(item, 'funcargs', {}):
        return
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

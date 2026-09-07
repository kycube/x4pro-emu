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
    where a collision is possible; the readable head still comes from the function name."""
    name = 'pytest-' + node_name.replace('[', '-').replace(']', '').replace('=', '-')
    room = SOCK_MAX - len(os.path.join(ROOT, '.x4emu')) - len('/console.sock') - 2   # '/' + NUL
    if len(name) > room or node_id:
        h = hashlib.sha1((node_id or node_name).encode()).hexdigest()[:8]
        name = name[:min(len(name), room - 9)] + '-' + h
    return name


def x4emu_input(name, *args, tries=3, **kw):
    """An input command carrying `--wait`, repeated once when nothing repainted.

    Both firmwares poll touch and the buttons only between rendering passes that take seconds, so an
    input delivered into such a pass is read and then ignored: `--wait` reports "no refresh within Ns"
    while the firmware did take the frame (CLAUDE.md "Lessons"; the session-4 flake in three tests, and
    one input in ~40 lost on a loaded host). `tests/test_stock.py`'s `tap_repaints` and
    `tests/test_device_screenshot.py` already retry for this; this is the same guard for every other
    test. The retry is safe precisely because no refresh happened -- the firmware acted on nothing, so
    nothing advanced. Any other failure is raised at once, unretried.

    Three attempts, not two: the desk Mac runs the owner's own applications (a load average around 7
    of 14 cores while this was written), every paint takes longer under that, and two attempts were
    measurably not enough -- a full run lost the menu tap twice in a row on 2026-09-07."""
    for attempt in range(tries):
        last = attempt == tries - 1
        r = x4emu(name, *args, check=last, **kw)
        if r.returncode == 0:
            return r
        if 'no refresh within' not in (r.stdout + r.stderr):
            raise AssertionError(f'x4emu {args} failed ({r.returncode}):\n{r.stdout}\n{r.stderr}')
        print(f'{name}: {args[0]} drew nothing, repeating it once')
    return r


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

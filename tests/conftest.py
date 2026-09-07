import os, subprocess, sys, time, pytest

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

def x4emu_input(name, *args, tries=2, **kw):
    """An input command carrying `--wait`, repeated once when nothing repainted.

    Both firmwares poll touch and the buttons only between rendering passes that take seconds, so an
    input delivered into such a pass is read and then ignored: `--wait` reports "no refresh within Ns"
    while the firmware did take the frame (CLAUDE.md "Lessons"; the session-4 flake in three tests, and
    one input in ~40 lost on a loaded host). `tests/test_stock.py`'s `tap_repaints` and
    `tests/test_device_screenshot.py` already retry for this; this is the same guard for every other
    test. The retry is safe precisely because no refresh happened -- the firmware acted on nothing, so
    nothing advanced. Any other failure is raised at once, unretried."""
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
    name = 'pytest-' + request.node.name.replace('[', '-').replace(']', '')
    x4emu(name, 'stop', check=False)
    yield name
    x4emu(name, 'stop', check=False)

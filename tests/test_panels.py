"""M6: each panel variant answers the boot probe the way its silicon does and paints; the UC8279
answers exactly what the desk unit answers; the UC8179 renders CrossPoint's reader as the UC8279 does,
anti-aliasing included, with the grey its own LUT bank implies."""
import os
from concurrent.futures import ThreadPoolExecutor
import pytest
from conftest import (x4emu, emu_log, emu_state, boot_crosspoint, count_pixels, copy_images, instance_name,
                      open_test_book)

VERDICTS = {
    'ssd1677': 'bus probe VER=FF FF FF FF FF FLG=FF -> default controller',
    'uc8179':  'promoted SSD1677 -> UC8179 (LUT_VER=01)',
    'uc8279':  'promoted SSD1677 -> UC8279 800x480 (LUT_VER=68)',
}
DESK_UNIT = (    # the desk unit's own probe and MTP read (docs/device/), on its panel only
    'bus probe VER=00 0F 68 00 00 FLG=13 -> UltraChip',
    'MTP[0x000..0x02F]: A5 A5 1F 20 25 25 3C 00 97 02 02 03 20 02 58 00',
)


@pytest.mark.parametrize('panel', ['ssd1677', 'uc8179', 'uc8279'])
def test_panel_variant_probe_and_paint(images, emu, tmp_path, panel):
    boot_crosspoint(emu, images, '--panel', panel, quiet_s=2)
    log = emu_log(emu)                       # the probe runs before Home
    for line in (VERDICTS[panel], *(DESK_UNIT if panel == 'uc8279' else ())):
        assert line in log, f'{panel}: no "{line}" in the console'
    st = emu_state(emu)
    assert st['panel'] == panel
    assert st['refresh_count'] >= 2 and st['epd_unknown_cmds'] == 0, st
    shot = tmp_path / f'home-{panel}.png'
    x4emu(emu, 'screenshot', str(shot))
    from PIL import Image
    dark = count_pixels(Image.open(shot).convert('L'), 0, 127)
    assert 2000 < dark < 200000, f'{panel}: {dark} dark pixels (expected a home screen)'


# --- the UC8179's grey passes (docs/grayscale.md §7) ----------------------------------------------
# CrossPoint drives the two UltraChip variants through different drivers (Uc8179Driver.cpp,
# Uc8279X4Driver.cpp) with different LUT layouts, CDI bytes and scan directions, but renders the same
# page into the same base frame and grey masks. So the two panels must show the same picture, with
# each grey at the level its AA bank drives: both grey classes get 2 VDL frames from the black base
# on the UC8179 (kGrayLuts), 3 on the UC8279 (kXtfAa68); at the default gray-k 38 that is 76 and 114.
# Until 2026-10-13 the UC8179's banks were read in the UC8279 layout: its AA and settle passes moved
# no pixel, so the page had no grey and a page turn (a settle pass) left page 1 on the glass.
GRAY_K = 38
GREY = {'uc8179': 2 * GRAY_K, 'uc8279': 3 * GRAY_K}


@pytest.fixture
def ref_emu():
    """A second instance, for the UC8279 reference run."""
    name = instance_name('panelref')
    x4emu(name, 'stop', check=False)
    yield name
    x4emu(name, 'stop', check=False)


def reader_walk(name, images, panel, shots):
    """Boot CrossPoint on `panel`; screenshots of Home and of pages 1 and 2 of the test book."""
    os.makedirs(shots, exist_ok=True)
    boot_crosspoint(name, images, '--panel', panel, quiet_s=2)
    x4emu(name, 'screenshot', os.path.join(shots, 'home.png'))
    open_test_book(name, shots)
    x4emu(name, 'screenshot', os.path.join(shots, 'page1.png'))
    x4emu(name, 'press', 'right', '--wait', 20)
    x4emu(name, 'wait-quiet', '--seconds', 1.5, '--timeout', 60)
    x4emu(name, 'screenshot', os.path.join(shots, 'page2.png'))
    st = emu_state(name)
    assert st['panel'] == panel and st['epd_unknown_cmds'] == 0 and st['gray_model'] == 'waveform', st


def test_uc8179_reader_greys_match_uc8279(images, emu, ref_emu, tmp_path):
    from PIL import Image, ImageChops
    runs = {'uc8179': (emu, images), 'uc8279': (ref_emu, copy_images(images, tmp_path / 'ref'))}
    with ThreadPoolExecutor(2) as ex:
        for f in [ex.submit(reader_walk, n, im, p, str(tmp_path / p)) for p, (n, im) in runs.items()]:
            f.result()

    def shot(panel, page):
        return Image.open(tmp_path / panel / f'{page}.png').convert('L')

    def levels(im):
        return {v: n for v, n in enumerate(im.histogram()) if n}

    # Home goes through the OTP waveform only: the same pixels (the UC8179's scan direction and padding)
    assert count_pixels(ImageChops.difference(shot('uc8179', 'home'), shot('uc8279', 'home'))) == 0
    for page in ('page1', 'page2'):
        a, b = shot('uc8179', page), shot('uc8279', page)
        la, lb = levels(a), levels(b)
        assert set(lb) == {0, GREY['uc8279'], 255}, f'uc8279 {page}: {lb}'
        assert set(la) == {0, GREY['uc8179'], 255}, f'uc8179 {page}: levels {la} (no grey: AA did not run)'
        assert la[GREY['uc8179']] == lb[GREY['uc8279']] > 5000, (la, lb)
        # the same grey layout and the same black and white, pixel for pixel
        a2 = a.point(lambda v: GREY['uc8279'] if v == GREY['uc8179'] else v)
        n = count_pixels(ImageChops.difference(a2, b))
        assert n == 0, f'{page}: uc8179 and uc8279 differ in {n} pixels beyond the grey level'
    # the page turn painted (its base frame goes through the settle bank on this driver)
    assert count_pixels(ImageChops.difference(shot('uc8179', 'page1'), shot('uc8179', 'page2'))) > 1000

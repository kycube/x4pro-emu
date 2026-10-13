"""M6: each panel variant answers the boot probe the way its silicon does and paints; the UC8279
answers exactly what the desk unit answers."""
import pytest
from conftest import x4emu, emu_log, emu_state, boot_crosspoint, count_pixels

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

"""M6: each panel variant answers the boot probe the way its silicon does and paints."""
import json, os, pytest
from conftest import x4emu, ROOT

VERDICTS = {
    'ssd1677': 'bus probe VER=FF FF FF FF FF FLG=FF -> default controller',
    'uc8179':  'promoted SSD1677 -> UC8179 (LUT_VER=01)',
    'uc8279':  'promoted SSD1677 -> UC8279 800x480 (LUT_VER=68)',
}

@pytest.mark.parametrize('panel', ['ssd1677', 'uc8179', 'uc8279'])
def test_panel_variant_probe_and_paint(images, emu, tmp_path, panel):
    x4emu(emu, 'run', '--flash', images['flash'], '--sd', images['sd'], '--fast-epd', '--panel', panel)
    x4emu(emu, 'wait-text', VERDICTS[panel], '--timeout', 60)
    x4emu(emu, 'wait-text', 'Entering activity: Home', '--timeout', 90)
    x4emu(emu, 'wait-quiet', '--seconds', 2, '--timeout', 60)
    st = json.loads(x4emu(emu, 'state').stdout)
    assert st['panel'] == panel
    assert st['refresh_count'] >= 2 and st['epd_unknown_cmds'] == 0, st
    shot = tmp_path / f'home-{panel}.png'
    x4emu(emu, 'screenshot', str(shot))
    from PIL import Image
    im = Image.open(shot).convert('L')
    dark = sum(1 for v in im.get_flattened_data() if v < 128) if hasattr(im, 'get_flattened_data') else sum(1 for v in im.getdata() if v < 128)
    assert 2000 < dark < 200000, f'{panel}: {dark} dark pixels (expected a home screen)'

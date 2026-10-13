"""Values that have to live in several places, held equal.

Lua apps cannot `require` (docs/lua-apps.md §4c), so every app that measures text carries its own copy of
the system font's advance widths (§4b); and the pixel-difference threshold exists both in the installable
x4emu package and in tools/xic2png.py, which cannot import each other. The 2026-10-13 review chose a test
over a code generator for these: the copies stay hand-editable, and drift fails here, fast, with no
emulator.
"""
import os
import re
import sys

from conftest import ROOT

sys.path.insert(0, ROOT)                     # the x4emu package (conftest adds only tools/)
import gamebook                              # noqa: E402  tools/gamebook.py: the measured table, as wrapped
import xic2png                               # noqa: E402
from x4emu import commands                   # noqa: E402

LUA_COPIES = {
    'apps/status-panels/index.lua': r'local ADV_BY_WIDTH = \{(.*?)\n\}',
    'apps/probe-io/index.lua': r'local ADV_BY_WIDTH = \{(.*?)\n\}',
    'tools/gamebook_engine.lua': r'for w, chars in pairs\(\{(.*?)\}\) do',
}
ENTRY = re.compile(r"""\[(\d+)\]\s*=\s*('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")""")


def lua_table(path, region):
    src = open(os.path.join(ROOT, path)).read()
    m = re.search(region, src, re.S)
    assert m, f'{path}: the advance-width table was not found (pattern {region!r})'
    out = {}
    for width, lit in ENTRY.findall(m.group(1)):
        out[int(width)] = re.sub(r'\\(.)', r'\1', lit[1:-1])     # Lua escapes: \\ \' \"
    return out


def test_every_lua_copy_of_the_advance_widths_matches_the_measured_table():
    want = {w: set(chars) for w, chars in gamebook.ADV_BY_WIDTH.items()}
    for path, region in LUA_COPIES.items():
        got = {w: set(chars) for w, chars in lua_table(path, region).items()}
        assert got == want, f'{path} drifted from tools/gamebook.py ADV_BY_WIDTH (docs/lua-apps.md §4b)'


def test_the_measured_table_covers_printable_ascii_once():
    chars = ''.join(gamebook.ADV_BY_WIDTH.values())
    assert sorted(chars) == [chr(c) for c in range(32, 127)], 'each of the 95 printable ASCII characters once'


def test_one_pixel_difference_threshold():
    assert commands.DIFF_THRESHOLD == xic2png.DIFF_THRESHOLD

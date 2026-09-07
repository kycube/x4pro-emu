-- Measure the advance width of every printable ASCII character in the font the host draws with.
--
-- WHY: `g` has no text-measuring call, so a right-aligned or centred string cannot be placed
-- correctly.  apps/status-panels guessed the widths and its date ran 15 px past the right margin.
--
-- HOW: the ink width of n copies of a character is (n-1)*advance + ink_width(c), so
--     advance(c) = (ink(4 copies) - ink(2 copies)) / 2
-- exactly, with no assumption about side bearings.  Page 1 draws both cells for all 95 printable
-- characters in a 6 x 32 grid; page 2 handles what page 1 cannot -- the space (no ink to measure, so
-- it is bracketed by '|'), the characters too wide for a grid cell, and validation strings whose
-- predicted width can be checked against their measured ink.
--
-- Tap to change page.  Read the sheets with a screenshot and a host-side script; the recipe and the
-- resulting table are in docs/lua-apps.md, "Measuring text".

local page = 1

local COLS, X0, PITCH, Y0, ROWH = 6, 12, 78, 6, 24
local chars = {}
for cp = 0x20, 0x7e do chars[#chars + 1] = string.char(cp) end

local WIDE_Y0, WIDE_ROWH, WIDE_X = 6, 40, 12
local wide = {
  '|  |', '|    |',                       -- advance(' ') = (w4 - w2) / 2
  'WW', 'WWWW', 'ii', 'iiii', 'jj', 'jjjj',   -- too wide, or too narrow, for a grid cell
  '63%', 'Mon 7 Sep', 'BATTERY', 'STATUS', '0h 00m', 'unknown',   -- validation
}

local function grid()
  local cell = 0
  for i = 1, #chars do
    for _, n in ipairs({2, 4}) do
      local col, row = cell % COLS, math.floor(cell / COLS)
      g:text(X0 + col * PITCH, Y0 + row * ROWH, string.rep(chars[i], n))
      cell = cell + 1
    end
  end
end

local function rows()
  for i, s in ipairs(wide) do g:text(WIDE_X, WIDE_Y0 + (i - 1) * WIDE_ROWH, s) end
end

function on_draw()
  g:clear(0)
  if page == 1 then grid() else rows() end
end

function on_input(_, _)
  page = (page == 1) and 2 or 1
  pcall(ctx.invalidate)
  return true
end

-- Status (panels) -- the same facts as apps/status-typographic, laid out as framed cards.
--
-- Design notes, so this stays editable:
--   * The host gives you ONE text size (g:text) and NO filled shapes: g holds exactly
--     clear, rect, line, circle, text, size, image, layer.  `fill()` below stacks g:line to make a
--     solid block, and that is what draws the header band, the gauge and the clock digits.
--   * Coordinates are the portrait frame: 480 wide, 800 tall, origin top left.  g:text(x, y, s)
--     puts the TOP of the ~24 px line box at y (the baseline lands near y + 19).
--     g:clear(0) whitens the whole frame including the stock's status bar.
--   * Text is always black, so nothing readable can sit inside a solid band: the header band here
--     is a rule, not a background.
--   * ADV below is the REAL advance width of every printable ASCII character in the host's font,
--     measured on the device's own rendering (see docs/lua-apps.md, "Measuring text").  There is no
--     text-measuring call in the API, and the guess this file used before was up to 18 px short --
--     which is why the date used to run past the right margin.  Right-aligned text is only ever as
--     good as this table.
--
-- Sources: ctx.sys.clock() (userdata: hour/minute/second/year/month/day/weekday), ctx.sys.battery(),
-- ctx.sys.charging(), ctx.sys.uptime_ms(), ctx.sys.network().state, ctx.sys.radio().mode,
-- ctx.gc:count(), ctx.screen.

-- ---------------------------------------------------------------- the spacing scale
-- Every gap on the page is one of these three numbers.  Change one and the page stays consistent.
local M    = 24                        -- page margin: the distance from the screen edge to anything
local PAD  = 18                        -- inside a card: border to text
local GAP  = 16                        -- between two cards
local LINE = 24                        -- the host's text line box

local W     = 480 - 2 * M              -- 432: card width across the page
local RIGHT = M + W                    -- 456: where right-aligned page text ends

-- ---------------------------------------------------------------- text measurement

-- advance width -> the characters that have it (measured, not guessed)
local ADV_BY_WIDTH = {
  [4]  = "'",
  [5]  = '.:Iil|',
  [6]  = ' ,;`j',
  [7]  = '()[]{}',
  [8]  = '"\\frt',
  [9]  = '*-/1^',
  [10] = '?J_sz',
  [11] = '7ackvxy~',
  [12] = '!$23459EFLbdeghnopqu',
  [13] = '+068<=>PRSTZ',
  [14] = 'ABCKVXY',
  [15] = '#&DGHNU',
  [16] = 'OQ',
  [17] = '%@w',
  [18] = 'Mm',
  [20] = 'W',
}
local ADV = {}
for w, chars in pairs(ADV_BY_WIDTH) do
  for k = 1, #chars do ADV[chars:sub(k, k)] = w end
end

local function textw(s)
  local w = 0
  for k = 1, #s do w = w + (ADV[s:sub(k, k)] or 12) end
  return w
end

-- ---------------------------------------------------------------- primitives the host lacks

local function fill(x, y, w, h)
  for i = 0, h - 1 do g:line(x, y + i, x + w - 1, y + i) end
end

-- a card: a frame with a second, inset line -- a double border reads as a panel on 1 bpp e-ink
local function card(x, y, w, h)
  g:rect(x, y, w, h)
  g:rect(x + 3, y + 3, w - 6, h - 6)
end

-- text placed against the left edge, the right edge, or the centre of a box
local function left(x, y, s)   g:text(x, y, s) end
local function right(xr, y, s) g:text(xr - textw(s), y, s) end
local function mid(x, w, y, s) g:text(x + math.floor((w - textw(s)) / 2), y, s) end

-- ---------------------------------------------------------------- the clock face

local SEGS = {
  [0] = 'abcdef', [1] = 'bc',    [2] = 'abged', [3] = 'abgcd', [4] = 'fgbc',
  [5] = 'afgcd',  [6] = 'afgedc', [7] = 'abc',  [8] = 'abcdefg', [9] = 'abcdfg',
}

local function digit(n, x, y, w, h, t)
  local segs = SEGS[n] or ''
  -- a 1 is only the two right-hand strokes; centre it in its cell
  if n == 1 then x = x - math.floor((w - t) / 2) end
  local mid_y = y + math.floor((h - t) / 2)
  local function has(c) return segs:find(c, 1, true) ~= nil end
  if has('a') then fill(x + t,     y,         w - 2 * t, t) end
  if has('g') then fill(x + t,     mid_y,     w - 2 * t, t) end
  if has('d') then fill(x + t,     y + h - t, w - 2 * t, t) end
  if has('f') then fill(x,         y + t,     t, mid_y - y - t) end
  if has('b') then fill(x + w - t, y + t,     t, mid_y - y - t) end
  if has('e') then fill(x,         mid_y + t, t, y + h - t - mid_y - t) end
  if has('c') then fill(x + w - t, mid_y + t, t, y + h - t - mid_y - t) end
end

local function big_time(y, hh, mm, w, h, t, gap)
  local total = 4 * w + 4 * gap + t
  local x = math.floor((480 - total) / 2)
  digit(math.floor(hh / 10), x, y, w, h, t)
  digit(hh % 10, x + w + gap, y, w, h, t)
  local cx = x + 2 * w + 2 * gap
  fill(cx, y + math.floor(h / 3) - t, t, t)
  fill(cx, y + math.floor(2 * h / 3), t, t)
  digit(math.floor(mm / 10), cx + t + gap, y, w, h, t)
  digit(mm % 10, cx + t + gap + w + gap, y, w, h, t)
end

-- ---------------------------------------------------------------- values

local DAYS = {'Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'}
local MONTHS = {'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'}

local function now()
  local ok, c = pcall(ctx.sys.clock)
  if not ok or c == nil then return nil end
  local t = {}
  for _, k in ipairs({'hour', 'minute', 'second', 'year', 'month', 'day', 'weekday'}) do
    local o, v = pcall(function() return c[k] end)
    t[k] = (o and v) or 0
  end
  return t
end

local function get(f, field, dflt)
  local ok, v = pcall(f)
  if not ok or v == nil then return dflt end
  if field == nil then return v end
  local o, w = pcall(function() return v[field] end)
  if o and w ~= nil then return w end
  return dflt
end

-- ---------------------------------------------------------------- the page
--
-- The vertical plan, so the gaps stay honest.  Each card's height is PAD + content + PAD, and every
-- gap between two cards is GAP; the header rule and the footer rule sit the same distance from
-- their edge.
--
--   28  header rule (6)          | 44  title line        | 84  divider
--   100 clock card   h 196       | 312 battery card h 130
--   458 stat row 1   h 92        | 566 stat row 2   h 92
--   674 panel card   h 60        | 766 footer rule  (6)

local HEAD_RULE, TITLE, DIVIDER = 28, 44, 84
local CLOCK_Y,  CLOCK_H  = 100, 196
local BATT_Y,   BATT_H   = 312, 130
local ROW1_Y,   ROW2_Y   = 458, 566
local STAT_H             = PAD + LINE + 8 + LINE + PAD          -- 92
local PANEL_Y, PANEL_H   = 674, PAD + LINE + PAD                -- 60
local FOOT_RULE          = 766
local GW                 = math.floor((W - GAP) / 2)            -- 208

local last_minute = -1

-- one stat card: a caption on the first line, the value on the second
local function stat(x, y, w, caption, value)
  card(x, y, w, STAT_H)
  left(x + PAD, y + PAD, caption)
  left(x + PAD, y + PAD + LINE + 8, value)
end

function on_draw()
  local t = now()
  local hh, mm = (t and t.hour or 0), (t and t.minute or 0)
  last_minute = mm

  local soc      = get(ctx.sys.battery) or 0
  local charging = get(ctx.sys.charging) == true
  local up       = math.floor((get(ctx.sys.uptime_ms) or 0) / 1000)
  local net      = tostring(get(ctx.sys.network, 'state', '-'))
  local radio    = tostring(get(ctx.sys.radio, 'mode', '-'))
  local heap     = math.floor((get(function() return ctx.gc:count() end) or 0) / 1024)
  local sw       = get(function() return ctx.screen.width end) or 480
  local sh       = get(function() return ctx.screen.height end) or 800

  g:clear(0)

  -- 1. header: a solid rule over the title line, the date closing the line on the right
  fill(M, HEAD_RULE, W, 6)
  left(M, TITLE, 'STATUS')
  local stamp = t and string.format('%s %d %s', DAYS[(t.weekday % 7) + 1], t.day, MONTHS[t.month] or '?') or ''
  right(RIGHT, TITLE, stamp)
  g:line(M, DIVIDER, RIGHT - 1, DIVIDER)

  -- 2. the clock card: digits and the year, centred together in the card's inner box
  card(M, CLOCK_Y, W, CLOCK_H)
  local digits_y = CLOCK_Y + PAD + 12
  big_time(digits_y, hh, mm, 56, 100, 9, 12)
  mid(M, W, digits_y + 100 + 12, t and tostring(t.year) or '')

  -- 3. the battery card: caption line, gauge, state line -- PAD above, between and below
  card(M, BATT_Y, W, BATT_H)
  local by = BATT_Y + PAD
  left(M + PAD, by, 'BATTERY')
  right(M + W - PAD, by, soc .. '%')
  local gauge_y, gauge_h = by + LINE + 10, 26
  g:rect(M + PAD, gauge_y, W - 2 * PAD, gauge_h)
  fill(M + PAD + 4, gauge_y + 4,
       math.max(1, math.floor((W - 2 * PAD - 8) * soc / 100)), gauge_h - 8)
  left(M + PAD, gauge_y + gauge_h + 10, charging and 'charging' or 'on battery')

  -- 4. a 2 x 2 grid of small cards
  stat(M,            ROW1_Y, GW, 'UPTIME',   string.format('%dh %02dm', math.floor(up / 3600), math.floor(up / 60) % 60))
  stat(M + GW + GAP, ROW1_Y, GW, 'LUA HEAP', heap .. ' KB')
  stat(M,            ROW2_Y, GW, 'NETWORK',  net)
  stat(M + GW + GAP, ROW2_Y, GW, 'RADIO',    radio)

  -- 5. a wide card for the panel itself
  card(M, PANEL_Y, W, PANEL_H)
  left(M + PAD, PANEL_Y + PAD, string.format('%d x %d e-ink, 1 bpp', sw, sh))

  -- 6. footer rule, the same distance from its edge as the header rule
  fill(M, FOOT_RULE, W, 6)
end

-- on_input(ctx, ev): ev = {type='touch', gesture='tap', x=.., y=.., time_ms=..}, in these
-- same portrait coordinates.
function on_input(_, _)
  pcall(ctx.invalidate)
  return true
end

-- on_tick(ctx, n): repaint only when the minute changes, so the panel does not flash for nothing.
function on_tick()
  local t = now()
  if t and t.minute ~= last_minute then pcall(ctx.invalidate) end
end

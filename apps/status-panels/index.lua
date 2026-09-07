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
--
-- Sources: ctx.sys.clock() (userdata: hour/minute/second/year/month/day/weekday), ctx.sys.battery(),
-- ctx.sys.charging(), ctx.sys.uptime_ms(), ctx.sys.network().state, ctx.sys.radio().mode,
-- ctx.gc:count(), ctx.screen.

local M     = 24                       -- page margin
local W     = 480 - 2 * M              -- panel width
local RIGHT = 480 - M

-- ---------------------------------------------------------------- primitives the host lacks

local function fill(x, y, w, h)
  for i = 0, h - 1 do g:line(x, y + i, x + w - 1, y + i) end
end

-- a card: a frame with a second, inset line -- a double border reads as a panel on 1 bpp e-ink
local function card(x, y, w, h)
  g:rect(x, y, w, h)
  g:rect(x + 3, y + 3, w - 6, h - 6)
end

local NARROW = {i = 4, l = 4, j = 5, t = 6, f = 6, r = 7, [' '] = 5, ['.'] = 5, [','] = 5, [':'] = 5}
local function textw(s)
  local w = 0
  for k = 1, #s do
    local c = s:sub(k, k)
    w = w + (NARROW[c] or (c:match('%u') and 12 or 10))
  end
  return w
end

-- ---------------------------------------------------------------- the clock face

local SEGS = {
  [0] = 'abcdef', [1] = 'bc',    [2] = 'abged', [3] = 'abgcd', [4] = 'fgbc',
  [5] = 'afgcd',  [6] = 'afgedc', [7] = 'abc',  [8] = 'abcdefg', [9] = 'abcdfg',
}

local function digit(n, x, y, w, h, t)
  local segs = SEGS[n] or ''
  -- a 1 is only the two right-hand strokes; centre it in its cell
  if n == 1 then x = x - math.floor((w - t) / 2) end
  local mid = y + math.floor((h - t) / 2)
  local function has(c) return segs:find(c, 1, true) ~= nil end
  if has('a') then fill(x + t,     y,         w - 2 * t, t) end
  if has('g') then fill(x + t,     mid,       w - 2 * t, t) end
  if has('d') then fill(x + t,     y + h - t, w - 2 * t, t) end
  if has('f') then fill(x,         y + t,     t, mid - y - t) end
  if has('b') then fill(x + w - t, y + t,     t, mid - y - t) end
  if has('e') then fill(x,         mid + t,   t, y + h - t - mid - t) end
  if has('c') then fill(x + w - t, mid + t,   t, y + h - t - mid - t) end
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

local last_minute = -1

-- one stat card: a caption on the first line, the value on the second
local function stat(x, y, w, h, caption, value)
  card(x, y, w, h)
  g:text(x + 16, y + 18, caption)
  g:text(x + 16, y + 52, value)
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

  -- 1. header: a solid band over the title line
  fill(M, 30, W, 6)
  g:text(M, 52, 'STATUS')
  local stamp = t and string.format('%s %d %s', DAYS[(t.weekday % 7) + 1], t.day, MONTHS[t.month] or '?') or ''
  g:text(RIGHT - textw(stamp), 52, stamp)
  g:line(M, 88, RIGHT, 88)

  -- 2. the clock card
  card(M, 104, W, 214)
  big_time(140, hh, mm, 56, 100, 9, 12)
  local year = t and tostring(t.year) or ''
  g:text(math.floor((480 - textw(year)) / 2), 274, year)

  -- 3. the battery card, with a gauge that runs across the panel
  card(M, 334, W, 128)
  g:text(M + 16, 352, 'BATTERY')
  local pct = soc .. '%'
  g:text(RIGHT - 16 - textw(pct), 352, pct)
  g:rect(M + 16, 388, W - 32, 26)
  fill(M + 20, 392, math.max(1, math.floor((W - 40) * soc / 100)), 18)
  g:text(M + 16, 424, charging and 'charging' or 'on battery')

  -- 4. a 2 x 2 grid of small cards
  local gw = math.floor((W - 16) / 2)
  stat(M,           478, gw, 90, 'UPTIME',   string.format('%dh %02dm', math.floor(up / 3600), math.floor(up / 60) % 60))
  stat(M + gw + 16, 478, gw, 90, 'LUA HEAP', heap .. ' KB')
  stat(M,           584, gw, 90, 'NETWORK',  net)
  stat(M + gw + 16, 584, gw, 90, 'RADIO',    radio)

  -- 5. a wide card for the panel itself
  card(M, 690, W, 62)
  g:text(M + 16, 708, string.format('%d x %d e-ink, 1 bpp', sw, sh))

  -- 6. footer band
  fill(M, 776, W, 4)
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

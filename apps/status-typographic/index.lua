-- Status (typographic) -- an airy home/status page for the stock xteink_app 7.2.4.
--
-- Design notes, so this stays editable:
--   * The host gives you ONE text size (g:text) and no filled shapes: g holds exactly
--     clear, rect, line, circle, text, size, image, layer.  Everything that looks big or solid
--     here is drawn by hand -- `fill()` stacks g:line, and the clock is a seven-segment face
--     built out of those fills.  That is why there is no ctx.fonts call anywhere: ctx.fonts:handle()
--     answers nil for every built-in name, fonts are app assets and this app ships none.
--   * Coordinates are the portrait frame: 480 wide, 800 tall, origin top left.  g:text(x, y, s)
--     puts the TOP of the ~24 px line box at y (the baseline lands near y + 19), so a rule that
--     should sit under a line goes at y + 30, not y + 4.
--   * g:clear(0) whitens the whole frame, status bar included, so the page owns every pixel.
--
-- Everything on screen comes from the API: ctx.sys.clock() (hour/minute/day/month/year/weekday),
-- ctx.sys.battery(), ctx.sys.charging(), ctx.sys.uptime_ms(), ctx.sys.network().state,
-- ctx.sys.radio().mode and ctx.gc:count().

local M      = 40                 -- page margin
local RIGHT  = 480 - M
local INK    = 0                  -- g:clear(0) = white page, everything drawn is black

-- ---------------------------------------------------------------- primitives the host lacks

-- A solid rectangle. The host has no fill, so stack horizontal lines (h calls, one per row).
local function fill(x, y, w, h)
  for i = 0, h - 1 do g:line(x, y + i, x + w - 1, y + i) end
end

-- A rough text width, for centring only: the system font is proportional, so this is an estimate
-- (caps and digits are wide, i/l/t/punctuation are narrow).
local NARROW = {i = 4, l = 4, j = 5, t = 6, f = 6, r = 7, [' '] = 5, ['.'] = 5, [','] = 5, [':'] = 5}
local function textw(s)
  local w = 0
  for k = 1, #s do
    local c = s:sub(k, k)
    w = w + (NARROW[c] or (c:match('%u') and 12 or 10))
  end
  return w
end

local function centre(y, s)
  g:text(math.floor((480 - textw(s)) / 2), y, s)
end

-- ---------------------------------------------------------------- the big clock

-- Seven-segment digits: a top, b top-right, c bottom-right, d bottom, e bottom-left, f top-left,
-- g middle. Each cell is W x H with a stroke of T.
local SEGS = {
  [0] = 'abcdef', [1] = 'bc',    [2] = 'abged', [3] = 'abgcd', [4] = 'fgbc',
  [5] = 'afgcd',  [6] = 'afgedc', [7] = 'abc',  [8] = 'abcdefg', [9] = 'abcdfg',
}

local function digit(n, x, y, W, H, T)
  local segs = SEGS[n] or ''
  -- a 1 is only the two right-hand strokes; slide the cell so it sits in the middle instead of
  -- leaving a hole after it
  if n == 1 then x = x - math.floor((W - T) / 2) end
  local mid = y + math.floor((H - T) / 2)
  local function has(c) return segs:find(c, 1, true) ~= nil end
  if has('a') then fill(x + T,     y,         W - 2 * T, T) end
  if has('g') then fill(x + T,     mid,       W - 2 * T, T) end
  if has('d') then fill(x + T,     y + H - T, W - 2 * T, T) end
  if has('f') then fill(x,         y + T,     T, mid - y - T) end
  if has('b') then fill(x + W - T, y + T,     T, mid - y - T) end
  if has('e') then fill(x,         mid + T,   T, y + H - T - mid - T) end
  if has('c') then fill(x + W - T, mid + T,   T, y + H - T - mid - T) end
end

-- HH:MM centred on the page. Returns nothing; the layout constants are local so they are easy to
-- retune: bigger W/H = a bigger clock, bigger T = a heavier one.
local function big_time(y, hh, mm, W, H, T, GAP)
  local total = 4 * W + 4 * GAP + T
  local x = math.floor((480 - total) / 2)
  digit(math.floor(hh / 10), x, y, W, H, T)
  digit(hh % 10, x + W + GAP, y, W, H, T)
  local cx = x + 2 * W + 2 * GAP                       -- the colon: two square pips
  fill(cx, y + math.floor(H / 3) - T, T, T)
  fill(cx, y + math.floor(2 * H / 3), T, T)
  digit(math.floor(mm / 10), cx + T + GAP, y, W, H, T)
  digit(mm % 10, cx + T + GAP + W + GAP, y, W, H, T)
end

-- ---------------------------------------------------------------- names and formatting

local DAYS = {'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'}
local MONTHS = {'January', 'February', 'March', 'April', 'May', 'June',
                'July', 'August', 'September', 'October', 'November', 'December'}

-- ctx.sys.clock() is a userdata with hour/minute/second/year/month/day/weekday (weekday 0 = Sunday).
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

local function hms(ms)
  local s = math.floor(ms / 1000)
  return string.format('%dh %02dm', math.floor(s / 3600), math.floor(s / 60) % 60)
end

-- ---------------------------------------------------------------- the page

local last_minute = -1

function on_draw()
  local t = now()
  local hh, mm = (t and t.hour or 0), (t and t.minute or 0)
  last_minute = mm

  local soc      = get(ctx.sys.battery) or 0
  local charging = get(ctx.sys.charging) == true
  local up       = get(ctx.sys.uptime_ms) or 0
  local net      = get(ctx.sys.network, 'state', '-')
  local radio    = get(ctx.sys.radio, 'mode', '-')
  -- ctx.gc:count() is a method (colon): the sub-table functions all want the table as first arg
  local heap     = math.floor((get(function() return ctx.gc:count() end) or 0) / 1024)

  g:clear(INK)                                             -- own the frame: white

  -- 1. the weekday, alone at the top, with a hairline under it
  if t then g:text(M, 76, DAYS[(t.weekday % 7) + 1]) end
  g:line(M, 116, RIGHT, 116)

  -- 2. the hour, as big as the page allows
  big_time(160, hh, mm, 64, 116, 10, 14)

  -- 3. the date, spelled out, centred under the clock
  if t then
    centre(320, string.format('%d %s %d', t.day, MONTHS[t.month] or '?', t.year))
  end

  -- 4. battery: the number opposite its label, then a gauge across the page
  g:line(M, 396, RIGHT, 396)
  g:text(M, 428, 'Battery')
  local pct = soc .. '%'
  g:text(RIGHT - textw(pct), 428, pct)
  g:rect(M, 464, RIGHT - M, 24)                            -- the gauge outline
  local inner = RIGHT - M - 8
  fill(M + 4, 468, math.max(1, math.floor(inner * soc / 100)), 16)
  g:text(M, 506, charging and 'charging' or 'on battery')

  -- 5. four quiet rows, label left, value right, held apart by space rather than rules
  g:line(M, 562, RIGHT, 562)
  local rows = {{'Uptime', hms(up)}, {'Network', tostring(net)}, {'Radio', tostring(radio)},
                {'Lua heap', heap .. ' KB'}}
  local y = 590
  for _, r in ipairs(rows) do
    g:text(M, y, r[1])
    g:text(RIGHT - textw(r[2]), y, r[2])
    y = y + 44
  end

  -- 6. footer
  g:line(M, 754, RIGHT, 754)
  g:text(M, 766, 'tap to refresh')
end

-- A tap repaints. on_input is called as on_input(ctx, ev) with
-- ev = {type = 'touch', gesture = 'tap', x = .., y = .., time_ms = ..} in these same coordinates.
function on_input(_, _)
  pcall(ctx.invalidate)
  return true                                              -- claim the event
end

-- on_tick(ctx, n) fires on its own; repaint only when the displayed minute changes, so the panel is
-- not refreshed for nothing (e-ink: every refresh is a visible flash).
function on_tick()
  local t = now()
  if t and t.minute ~= last_minute then pcall(ctx.invalidate) end
end

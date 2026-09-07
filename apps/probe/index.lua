-- API Probe -- draws what the stock 7.2.4 Lua host actually holds.
--
-- There is no print, no io, and ctx.log.* reaches nothing (docs/lua-apps.md), so the screen is the
-- only output channel: this app enumerates the API and paints it, one page per tap.
--
--   pages 1..N   the text dump: g's methods (via its metatable), every ctx sub-table's members
--                with their Lua type and value, and the return values of the ctx.sys.* getters
--   page  N+1    the table on_input receives, keys and values, refreshed on every tap
--   page  N+2    drawing experiments: does g:rect take a fill flag, does g:invert exist, ...
--
-- Everything is collected once, defensively (pcall around every call and every pairs), so a member
-- that raises still leaves the rest of the dump on screen. Only ctx.sys.* getters are *called*;
-- the other sub-tables are enumerated but never invoked (ctx.quit and ctx.lock.* could end the run).

local PER   = 33          -- body lines per page
local LH    = 22          -- line height, px
local TOP   = 52          -- baseline of the first body line
local X     = 10          -- left margin
local WIDE  = 46          -- characters that fit on a line at this margin

local lines  = {}         -- the static dump, built once
local built  = false
local page   = 1
local taps   = 0
local ev     = {}         -- the last on_input event, flattened to strings
local draw_args, tick_args = '?', '?'  -- what the host passes to on_draw / on_tick
local ev_seen = {}        -- every key ever seen on an event table

local function add(s) lines[#lines + 1] = s end

local function short(s, n)
  s = tostring(s)
  n = n or WIDE
  if #s > n then return s:sub(1, n - 1) .. '~' end
  return s
end

-- pairs() over a table that may be protected; nil when it cannot be walked
local function keys_of(t)
  if type(t) ~= 'table' then return nil end
  local ks = {}
  local ok = pcall(function() for k in pairs(t) do ks[#ks + 1] = k end end)
  if not ok then return nil end
  table.sort(ks, function(a, b) return tostring(a) < tostring(b) end)
  return ks
end

-- a one-line rendering of any value
local function val(v, n)
  local t = type(v)
  if t == 'string' then return '"' .. short(v, n or 26) .. '"' end
  if t == 'number' or t == 'boolean' or t == 'nil' then return tostring(v) end
  if t == 'table' then
    local ks = keys_of(v)
    if not ks then return 'table(?)' end
    local parts = {}
    for _, k in ipairs(ks) do
      local sv = v[k]
      local st = type(sv)
      if st == 'string' or st == 'number' or st == 'boolean' then
        parts[#parts + 1] = tostring(k) .. '=' .. tostring(sv)
      else
        parts[#parts + 1] = tostring(k) .. ':' .. st:sub(1, 3)
      end
    end
    return '{' .. short(table.concat(parts, ' '), n or 34) .. '}'
  end
  return t
end

-- one line per member: name, Lua type, and the value for anything that is not a function
local function dump_members(label, t)
  local ks = keys_of(t)
  if type(t) ~= 'table' then add(label .. ' = ' .. type(t)); return end
  if not ks then add(label .. ' = table (pairs blocked)'); return end
  if #ks == 0 then add(label .. ' = {}  (empty)'); return end
  add(label .. '  (' .. #ks .. ')')
  for _, k in ipairs(ks) do
    local v = t[k]
    if type(v) == 'function' then
      add('  ' .. short(tostring(k), 20) .. '  fn')
    else
      add('  ' .. short(tostring(k), 18) .. '  ' .. type(v):sub(1, 4) .. ' ' .. val(v, 20))
    end
  end
end

-- the drawing object: its own keys plus everything reachable through its metatable
local function dump_g()
  add('== g (' .. type(g) .. ') ==')
  local own = keys_of(g)
  if own and #own > 0 then
    add('own keys:')
    for _, k in ipairs(own) do
      add('  ' .. short(tostring(k), 20) .. '  ' .. type(g[k]))
    end
  else
    add('own keys: none / not walkable')
  end
  local mt = getmetatable(g)
  if type(mt) ~= 'table' then
    add('metatable: ' .. type(mt))
  else
    local mks = keys_of(mt)
    add('metatable: table (' .. (mks and #mks or 0) .. ' keys)')
    if mks then
      for _, k in ipairs(mks) do add('  mt.' .. short(tostring(k), 18) .. '  ' .. type(mt[k])) end
      local idx = mt.__index
      if type(idx) == 'table' then
        local iks = keys_of(idx)
        if iks then
          add('mt.__index methods (' .. #iks .. '):')
          for _, k in ipairs(iks) do
            add('  g:' .. short(tostring(k), 20) .. '  ' .. type(idx[k]))
          end
        end
      end
    end
  end
  -- a name probe as a backstop: works even when nothing above is walkable
  local names = {'clear', 'rect', 'line', 'circle', 'text', 'size', 'image', 'layer', 'stroke',
                 'color', 'invert', 'draw_with', 'offscreen', 'fill', 'fill_rect', 'rect_fill',
                 'pixel', 'point', 'blit', 'bitmap', 'font', 'set_font', 'measure', 'text_width',
                 'width', 'height', 'push', 'pop', 'save', 'restore', 'clip', 'translate',
                 'round_rect', 'triangle', 'ellipse', 'arc', 'polygon', 'dither', 'mode', 'flush'}
  local found, missing = {}, 0
  for _, n in ipairs(names) do
    local ok, v = pcall(function() return g[n] end)
    if ok and v ~= nil then found[#found + 1] = n else missing = missing + 1 end
  end
  add('name probe hits (' .. #found .. ' of ' .. #names .. '):')
  local row = ''
  for _, n in ipairs(found) do
    if #row + #n + 1 > WIDE - 2 then add('  ' .. row); row = n
    else row = (row == '' and n or row .. ' ' .. n) end
  end
  if row ~= '' then add('  ' .. row) end
  -- what g:size() returns, and how many values
  local ok, a, b, c = pcall(function() return g:size() end)
  add('g:size() -> ' .. (ok and (val(a) .. ', ' .. val(b) .. ', ' .. val(c)) or 'error'))
end

local function dump_ctx()
  add('== ctx (' .. type(ctx) .. ') ==')
  local ks = keys_of(ctx)
  if ks then
    local row = ''
    for _, k in ipairs(ks) do
      local n = tostring(k) .. (type(ctx[k]) == 'function' and '()' or '')
      if #row + #n + 1 > WIDE - 2 then add('  ' .. row); row = n
      else row = (row == '' and n or row .. ' ' .. n) end
    end
    if row ~= '' then add('  ' .. row) end
  end
  for _, n in ipairs({'fonts', 'screen', 'input', 'state', 'assets', 'data', 'i18n',
                      'layers', 'lock', 'perf', 'gc', 'display', 'log', 'system'}) do
    add('')
    dump_members('ctx.' .. n, ctx[n])
  end
end

-- every ctx.sys.* getter, called with no arguments; the value is what the app can actually use
local function dump_sys()
  add('')
  add('== ctx.sys.* called with no args ==')
  local ks = keys_of(ctx.sys)
  if not ks then add('ctx.sys not walkable'); return end
  for _, k in ipairs(ks) do
    local f = ctx.sys[k]
    if type(f) ~= 'function' then
      add('  ' .. short(tostring(k), 16) .. ' = ' .. type(f) .. ' ' .. val(f, 22))
    else
      local ok, a, b = pcall(f)
      if not ok then
        add('  ' .. short(tostring(k), 16) .. '() ERR ' .. short(tostring(a), 22))
      else
        local s = val(a, 26)
        if b ~= nil then s = s .. ', ' .. val(b, 12) end
        add('  ' .. short(tostring(k), 16) .. '() -> ' .. s)
      end
    end
  end
end

-- long text across several lines
local function wrap(prefix, s)
  s = tostring(s)
  local n = WIDE - #prefix
  while #s > n do add(prefix .. s:sub(1, n)); s = s:sub(n + 1); prefix = '    ' end
  if #s > 0 then add(prefix .. s) end
end

-- A call's *error message* is the documentation the host never wrote: "bad argument #2 to 'handle'
-- (string expected, got no value)" names the arity and the types. Everything here is called with
-- arguments that cannot change device state.
local function sig(label, f, ...)
  if type(f) ~= 'function' then add('  ' .. label .. ': ' .. type(f)); return end
  local r = table.pack(pcall(f, ...))
  if r[1] then
    local out = {}
    for i = 2, r.n do out[#out + 1] = val(r[i], 22) end
    wrap('  ' .. label .. ' = ', #out > 0 and table.concat(out, ', ') or '(no value)')
  else
    wrap('  ' .. label .. ' ! ', tostring(r[2]):gsub('%[string "probe"%]:%d+: ', ''))
  end
end

-- what a userdata return actually is: its metatable, and the fields that answer
local function dump_userdata(name, u)
  add('  ' .. name .. '  ' .. type(u) .. '  tostring=' .. short(tostring(u), 22))
  local mt = getmetatable(u)
  add('    metatable ' .. type(mt))
  if type(mt) == 'table' then
    local ks = keys_of(mt)
    if ks then for _, k in ipairs(ks) do add('    mt.' .. tostring(k) .. '  ' .. type(mt[k])) end end
  end
  local ok, err = pcall(function() return u.hour end)
  if not ok then wrap('    index ! ', tostring(err):gsub('%[string "probe"%]:%d+: ', '')); return end
  local hits = {}
  for _, f in ipairs({'hour', 'min', 'minute', 'sec', 'second', 'year', 'month', 'day', 'mday',
                      'wday', 'weekday', 'yday', 'text', 'str', 'time', 'date', 'value', 'level',
                      'percent', 'soc', 'state', 'name', 'on', 'enabled', 'connected'}) do
    local o, v = pcall(function() return u[f] end)
    if o and v ~= nil then hits[#hits + 1] = f .. '=' .. val(v, 14) end
  end
  wrap('    fields: ', #hits > 0 and table.concat(hits, ' ') or 'none of the usual names')
end

local function dump_deep()
  add('')
  add('== what the userdata returns are ==')
  for _, n in ipairs({'clock', 'status', 'power', 'network', 'radio', 'bluetooth'}) do
    local ok, u = pcall(ctx.sys[n])
    if ok and type(u) == 'userdata' then pcall(dump_userdata, 'ctx.sys.' .. n .. '()', u) end
  end
  add('')
  add('== signatures, read off the error messages ==')
  sig('fonts.status()', ctx.fonts.status)
  sig('fonts.handle()', ctx.fonts.handle)
  sig("fonts.handle('default')", ctx.fonts.handle, 'default')
  sig("fonts.handle('default',24)", ctx.fonts.handle, 'default', 24)
  sig('fonts.handle(1,1)', ctx.fonts.handle, 1, 1)
  sig("fonts.handle('large','bold')", ctx.fonts.handle, 'large', 'bold')
  sig('i18n.t()', ctx.i18n.t)
  sig("i18n.t('ok')", ctx.i18n.t, 'ok')
  sig('perf.info()', ctx.perf.info)
  sig('gc.count()', ctx.gc.count)
  sig('layers.create()', ctx.layers.create)
  sig('layers.create(480,800)', ctx.layers.create, 480, 800)
  sig('assets.info()', ctx.assets.info)
  sig("assets.info('icon')", ctx.assets.info, 'icon')
  sig("assets.handle('icon')", ctx.assets.handle, 'icon')
  sig("data.exists('a.txt')", ctx.data.exists, 'a.txt')
  sig("data.read_text('a.txt')", ctx.data.read_text, 'a.txt')
  sig("data.size('a.txt')", ctx.data.size, 'a.txt')
  sig('set_tick_rate()', ctx.set_tick_rate)
  sig('g.text ret', function() return g:text(300, 770, '.') end)
  sig('g.clear ret', function() return g:clear(0) end)
  sig('g.image()', function() return g:image() end)
  sig("g.image('a',0,0)", function() return g:image('a', 0, 0) end)
  sig('g.layer()', function() return g:layer() end)
  sig('g.layer(1)', function() return g:layer(1) end)
  sig('g.rect(no args)', function() return g:rect() end)
  sig('g.text(no args)', function() return g:text() end)
  add('')
  dump_members('ctx.input.caps', ctx.input.caps)

  -- Round 3. Every ctx sub-table function turned out to want a leading argument ("bad argument #2
  -- ... got no value" when given one), i.e. they are *methods*: call them with a colon.
  add('')
  add('== the sub-tables called as methods (colon) ==')
  sig('fonts:status()', function() return ctx.fonts:status() end)
  for _, n in ipairs({'default', 'system', 'ui', 'title', 'large', 'small', 'body', 'bold',
                      'regular', 'mono', 'digit', 'sans', 'serif', '24', '32', '48'}) do
    sig("fonts:handle('" .. n .. "')", function() return ctx.fonts:handle(n) end)
  end
  sig('i18n:t("ok")', function() return ctx.i18n:t('ok') end)
  sig('i18n:t("app_name")', function() return ctx.i18n:t('app_name') end)
  sig('data:exists("a.txt")', function() return ctx.data:exists('a.txt') end)
  sig('data:read_text("a.txt")', function() return ctx.data:read_text('a.txt') end)
  sig('assets:handle("icon")', function() return ctx.assets:handle('icon') end)
  sig('layers:create(64,64)', function() return ctx.layers:create(64, 64) end)
  sig('perf:info()', function() return ctx.perf:info() end)
  sig('gc:count()', function() return ctx.gc:count() end)
  -- does g:text take a font as a fourth argument?
  sig('g:text(x,y,s,1)', function() return g:text(300, 770, '.', 1) end)
  sig('g:text(x,y,s,"large")', function() return g:text(300, 770, '.', 'large') end)
  sig('g:clear(1)', function() return g:clear(1) end)
  sig('g:clear(0) again', function() return g:clear(0) end)

  -- the userdata getters, probed with a much wider set of field names
  add('')
  add('== userdata fields, wide probe ==')
  for _, n in ipairs({'status', 'power', 'radio', 'bluetooth', 'network', 'clock'}) do
    local ok, u = pcall(ctx.sys[n])
    if ok and type(u) == 'userdata' then
      local hits = {}
      for _, f in ipairs({'battery', 'percent', 'soc', 'level', 'charging', 'charge', 'plugged',
                          'usb', 'wifi', 'bt', 'ble', 'on', 'enabled', 'connected', 'state',
                          'ssid', 'rssi', 'ip', 'mode', 'sd', 'card', 'storage', 'free', 'total',
                          'brightness', 'light', 'temp', 'voltage', 'mv', 'millis', 'uptime',
                          'hour', 'minute', 'second', 'year', 'month', 'day', 'weekday', 'yday',
                          'epoch', 'iso', 'text', 'label', 'ok', 'busy', 'locked', 'awake'}) do
        local o, v = pcall(function() return u[f] end)
        if o and v ~= nil then hits[#hits + 1] = f .. '=' .. val(v, 12) end
      end
      wrap('  sys.' .. n .. '(): ', #hits > 0 and table.concat(hits, ' ') or 'no named field answered')
    end
  end
end

local function build()
  built = true
  pcall(function() add('_VERSION = ' .. tostring(_VERSION)) end)
  pcall(dump_g)
  pcall(function() add('') end)
  pcall(dump_ctx)
  pcall(dump_sys)
  pcall(dump_deep)
end

------------------------------------------------------------------ the event table

function on_input(...)
  taps = taps + 1
  ev = {}
  local a = table.pack(...)
  ev[#ev + 1] = '  arguments: ' .. a.n
  for i = 1, a.n do
    ev[#ev + 1] = '  #' .. i .. ' is a ' .. type(a[i]) ..
                  (a[i] == ctx and '  (it IS ctx)' or (a[i] == g and '  (it IS g)' or ''))
  end
  ev[#ev + 1] = '  on_draw args ' .. draw_args
  ev[#ev + 1] = '  on_tick args ' .. tick_args
  local evt = a[a.n]                                 -- the last one: the event, if there is one
  if a.n > 1 then evt = a[2] end
  if type(evt) == 'table' then
    local ks = keys_of(evt)
    if ks then
      for _, k in ipairs(ks) do
        ev[#ev + 1] = '  ' .. short(tostring(k), 16) .. '  ' .. type(evt[k]) .. ' = ' .. val(evt[k], 20)
        ev_seen[tostring(k)] = true
      end
    else
      ev[#ev + 1] = '  (table, pairs blocked)'
    end
    local mt = getmetatable(evt)
    ev[#ev + 1] = '  metatable: ' .. type(mt)
    if type(mt) == 'table' then
      local mks = keys_of(mt)
      if mks then for _, k in ipairs(mks) do ev[#ev + 1] = '  mt.' .. tostring(k) end end
    end
  else
    ev[#ev + 1] = '  the argument is a ' .. type(evt) .. ': ' .. val(evt, 24)
  end
  page = page + 1
  if pcall(function() return ctx.invalidate end) and ctx.invalidate then pcall(ctx.invalidate) end
  return true                                        -- claim the tap
end

------------------------------------------------------------------ drawing

-- the experiment page: does anything fill, invert or change the pen?
local function draw_experiments()
  local y = TOP
  local function label(s) g:text(X, y, s); y = y + LH end
  label('DRAW EXPERIMENTS   (empty box = the call did nothing)')
  y = y + 4

  local function try(name, f)
    local ok, err = pcall(f)
    g:text(X, y + 34, (ok and 'ok  ' or 'ERR ') .. name)
    if not ok then g:text(X, y + 34 + LH, '    ' .. short(tostring(err), 40)) end
    y = y + 34 + LH + (ok and 6 or LH + 6)
  end

  -- 1. rect with a 5th argument: a fill flag?
  g:rect(300, y, 60, 26)
  try('g:rect(x,y,w,h,1)  -> box at right', function() g:rect(370, y, 60, 26, 1) end)

  -- 2. a fill/fill_rect method under any of the usual names
  local filled = false
  for _, n in ipairs({'fill', 'fill_rect', 'rect_fill', 'box'}) do
    local has = select(2, pcall(function() return g[n] end))
    if has ~= nil then
      local ok = pcall(function() g[n](g, 370, y, 60, 26) end)
      g:text(X, y + 34, (ok and 'ok  ' or 'ERR ') .. 'g:' .. n .. '(x,y,w,h)')
      y = y + 34 + 6
      filled = true
    end
  end
  if not filled then g:text(X, y + 34, 'no fill / fill_rect / rect_fill / box method'); y = y + 34 + 6 end

  -- 3. invert over a drawn box
  g:rect(300, y, 60, 26)
  g:text(305, y + 20, 'ab')
  try('g:invert(300,y,60,26)', function() g:invert(300, y, 60, 26) end)

  -- 4. colour / stroke pens
  try('g:color(1) then rect', function() g:color(1); g:rect(370, y, 60, 26) end)
  try('g:stroke(3) then line', function() g:stroke(3); g:line(300, y + 12, 440, y + 12) end)

  -- 5. a bigger face through ctx.fonts
  local fk = keys_of(ctx.fonts)
  if fk and #fk > 0 then
    try('ctx.fonts.' .. tostring(fk[1]) .. ' used below', function()
      local f = ctx.fonts[fk[1]]
      if type(f) == 'function' then f(48) end
      g:text(300, y + 24, 'Aa 123')
    end)
  else
    g:text(X, y + 34, 'ctx.fonts is empty'); y = y + 34 + 6
  end
end

local function argsig(...)
  local a = table.pack(...)
  local out = {}
  for i = 1, a.n do
    out[#out + 1] = type(a[i]) .. (a[i] == ctx and '(ctx)' or (a[i] == g and '(g)' or ''))
  end
  return a.n .. ': ' .. table.concat(out, ', ')
end

function on_tick(...)
  tick_args = argsig(...)
end

function on_draw(...)
  draw_args = argsig(...)
  if not built then pcall(build) end
  g:clear(0)                                          -- own the frame: white

  local body = math.max(1, math.ceil(#lines / PER))
  local total = body + 2
  if page < 1 then page = 1 end
  if page > total then page = 1 end

  g:text(X, 26, 'API PROBE   page ' .. page .. '/' .. total .. '   tap = next')
  g:line(0, 34, 480, 34)

  if page <= body then
    local first = (page - 1) * PER + 1
    for i = 0, PER - 1 do
      local s = lines[first + i]
      if not s then break end
      g:text(X, TOP + i * LH, short(s, WIDE))
    end
  elseif page == body + 1 then
    g:text(X, TOP, 'on_input EVENT TABLE   (taps: ' .. taps .. ')')
    local names = {}
    for k in pairs(ev_seen) do names[#names + 1] = k end
    table.sort(names)
    g:text(X, TOP + LH, 'keys ever seen: ' .. (#names > 0 and short(table.concat(names, ' '), 36) or 'none'))
    for i, s in ipairs(ev) do
      if i > PER - 3 then break end
      g:text(X, TOP + (i + 2) * LH, short(s, WIDE))
    end
  else
    pcall(draw_experiments)
  end

  g:line(0, 780, 480, 780)
  g:text(X, 796, 'lines: ' .. #lines .. '   tap anywhere to page')
end

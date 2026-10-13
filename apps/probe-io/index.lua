-- IO Probe -- what a gamebook app can rely on in the stock Lua host (xteink_app 7.6.8).
--
-- The host has no print, no io/os/debug, and ctx.log reaches nothing (docs/lua-apps.md), so every
-- page DRAWS its own measurements. The questions: which paths ctx.data resolves and whether
-- read_text returns exact bytes; what open_text returns; require; how fast g:line / g:rect / g:text
-- are; what a long callback does; how much memory a script gets; whether ctx.state survives
-- ctx.save + leaving + a reboot; what on_input receives for buttons and swipes; how layers are
-- drawn and blitted; the on_tick interval and ctx.set_tick_rate; which refresh the panel does.
--
-- NAVIGATION (portrait frame, 480 x 800, the frame on_input reports and g draws in):
--   page 1 is an index: tap a box to jump to that page.
--   top band (y < 56): back to the index.   bottom band (y >= 728): next page.
--   a box on a page: runs that box's action.  Any other tap: redraws the page (speed pages
--   measure again on every draw).
-- From the emulator's 800 x 480 landscape panel: `x4emu tap X Y` with X = portrait y and
-- Y = 479 - portrait x (index box k = page - 2: col = k % 2, row = k // 2, centre
-- x = 124 or 356, y = 86 + 50 * row).
--
-- Found on 7.6.8 in the emulator (2026-10-12): ctx.data and require resolve nothing for a plain
-- card directory (the host's package source is null: "module source unavailable", read_text ->
-- nil,"not_found"/"invalid_name"); ctx.save() is a stub returning false; a frame keeps its first
-- 1024 commands, a layer painted with draw_with has no such limit; set_tick_rate/request_refresh
-- take their option as argument #2 (ctx:set_tick_rate('low')). Leaving the app saves ctx.state,
-- encrypted, to /XTApps/<app_id>/state.xtsv (DMA-AES; the emulator hung there until
-- qemu-patches/0019 let the GDMA reach PSRAM), and the next start restores it (docs/lua-apps.md 4c).
--
-- The card: `apps/probe-io/mkcard.py CARD.img` installs this directory and adds the generated
-- files (big100k.txt, big400k.txt, lad*.txt) and the markers outside the app directory.
-- Pages 20..22 (LONG, MEMORY, LADDER) only run when their boxes are tapped: they can stall or
-- kill the app, so use a fresh emulator instance for them.

local PX, LH, TOP, BOT = 10, 22, 60, 728
local MAXW = 462                       -- usable text width in px (measured advances below)

local cur = 1                          -- current page
local draws = 0
local last_input_ms, last_draw_ms = nil, nil
local last_error = nil
local LIFE = {}                        -- lifecycle callbacks seen: "name@ms"
local STORY_LEN, STORY_ADLER, STORY_CHARS, STORY_LINES = 1029, 0x545883b1, 970, 18
local FILL = 'abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ 0'

local function now() return ctx.sys.millis() end

------------------------------------------------------------------ text helpers
-- the advance widths of the host's font (docs/lua-apps.md 4b), so long lines wrap instead of
-- running off the panel. A UTF-8 lead byte counts 12 px, continuation bytes 0.
local ADV_BY_WIDTH = {
  [4] = "'", [5] = '.:Iil|', [6] = ' ,;`j', [7] = '()[]{}', [8] = '"\\frt', [9] = '*-/1^',
  [10] = '?J_sz', [11] = '7ackvxy~', [12] = '!$23459EFLbdeghnopqu', [13] = '+068<=>PRSTZ',
  [14] = 'ABCKVXY', [15] = '#&DGHNU', [16] = 'OQ', [17] = '%@w', [18] = 'Mm', [20] = 'W',
}
local ADV = {}
for w, chars in pairs(ADV_BY_WIDTH) do
  for k = 1, #chars do ADV[chars:byte(k)] = w end
end
local function cw(b)
  if b < 0x80 then return ADV[b] or 12 end
  if b >= 0xC0 then return 12 end
  return 0
end

-- the longest head of s that fits maxw px, and the rest (never split inside a UTF-8 sequence)
local function fit(s, maxw)
  local w, i, n = 0, 1, #s
  while i <= n do
    local a = cw(s:byte(i))
    if w + a > maxw then break end
    w = w + a
    i = i + 1
  end
  while i <= n and i > 2 and s:byte(i) >= 0x80 and s:byte(i) < 0xC0 do i = i - 1 end
  if i == 1 then i = 2 end
  return s:sub(1, i - 1), s:sub(i)
end

local function add(out, s)
  s = tostring(s)
  local head, rest = fit(s, MAXW)
  out[#out + 1] = head
  local guard = 0
  while #rest > 0 and guard < 30 do
    head, rest = fit('    ' .. rest, MAXW)
    out[#out + 1] = head
    guard = guard + 1
  end
end

local function short(s, n)
  s = tostring(s)
  n = n or 40
  if #s > n then return s:sub(1, n - 1) .. '~' end
  return s
end

local function keys_of(t)
  if type(t) ~= 'table' then return nil end
  local ks = {}
  local ok = pcall(function() for k in pairs(t) do ks[#ks + 1] = k end end)
  if not ok then return nil end
  table.sort(ks, function(a, b) return tostring(a) < tostring(b) end)
  return ks
end

local function join(ks, sep)
  if not ks then return '(not walkable)' end
  local parts = {}
  for i = 1, #ks do parts[i] = tostring(ks[i]) end
  return table.concat(parts, sep or ' ')
end

local function val(v, n)
  local t = type(v)
  if t == 'string' then return '"' .. short(v, n or 30) .. '"' end
  if t == 'number' then
    return tostring(v) .. (math.type(v) == 'float' and 'f' or '')
  end
  if t == 'boolean' or t == 'nil' then return tostring(v) end
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
    return '{' .. short(table.concat(parts, ' '), n or 60) .. '}'
  end
  return t .. ' ' .. short(tostring(v), 24)
end

-- error text without the chunk prefix ("[string ...]:12:" or "path.lua:12:" -> "L12:")
local function errs(e)
  local s = tostring(e)
  s = (s:gsub('^%[string "[^"]*"%]:(%d+):', 'L%1:'))
  s = (s:gsub('^[^:%s]+%.lua:(%d+):', 'L%1:'))
  return s
end

-- a pcall result packed by table.pack(pcall(...)), as one string
local function results(r, n)
  if not r[1] then return 'ERR ' .. errs(r[2]) end
  if r.n < 2 then return '(no value)' end
  local out = {}
  for i = 2, r.n do out[#out + 1] = val(r[i], n or 24) end
  return table.concat(out, ', ')
end

local function call(f, ...) return table.pack(pcall(f, ...)) end

local function argsig(...)
  local a = table.pack(...)
  local out = {}
  for i = 1, a.n do
    local v = a[i]
    out[#out + 1] = type(v) .. (rawequal(v, ctx) and '(ctx)' or (rawequal(v, g) and '(g)' or ''))
  end
  return a.n .. ': ' .. table.concat(out, ', ')
end

local function gcc()
  local ok, v = pcall(ctx.gc.count, ctx.gc)
  if ok then return v end
  return 'ERR ' .. errs(v)
end
local function gccollect() pcall(ctx.gc.collect, ctx.gc) end

local function adler32(s)
  local a, b = 1, 0
  local n = #s
  local i = 1
  while i <= n do
    local j = math.min(i + 255, n)
    local bytes = { s:byte(i, j) }
    for k = 1, #bytes do
      a = a + bytes[k]
      b = b + a
    end
    a = a % 65521
    b = b % 65521
    i = j + 1
  end
  return (b << 16) | a
end

local function line_of(k) return string.format('%07d %s\n', k, FILL) end

local function data_call(name, ...)
  return call(ctx.data[name], ctx.data, ...)
end

------------------------------------------------------------------ drawing helpers
local function draw_lines(ls, y0, ymax)
  local y = y0 or TOP
  ymax = ymax or (BOT - 4)
  for i = 1, #ls do
    if y + LH > ymax and i < #ls then
      g:text(PX, y, '... ' .. (#ls - i + 1) .. ' more lines')
      return y + LH
    end
    g:text(PX, y, ls[i])
    y = y + LH
  end
  return y
end

local function box(x, y, w, h, label)
  g:rect(x, y, w, h)
  g:text(x + 8, y + (h - 24) // 2, label)
end

------------------------------------------------------------------ pages
local PAGES = {}
local function page(name, def)
  def.name = name
  PAGES[#PAGES + 1] = def
  return def
end

local function buttons_of(p)
  if type(p.buttons) == 'function' then return p.buttons() end
  return p.buttons or {}
end

-- a grid of boxes: cols columns, starting at y0, each h tall, gap px apart
local function grid(specs, cols, y0, h, gap)
  local w = (480 - 20 - (cols - 1) * gap) // cols
  local bs = {}
  for i, s in ipairs(specs) do
    local k = i - 1
    bs[#bs + 1] = { x = 10 + (k % cols) * (w + gap), y = y0 + (k // cols) * (h + gap), w = w, h = h,
                    label = s[1], fn = s[2] }
  end
  return bs
end

------------------------------------------------------------------ input log (shared by all pages)
local EVLOG, EVN = {}, 0
local EVKINDS = {}
local function ev_string(ev, extra)
  if type(ev) ~= 'table' then return type(ev) .. ' ' .. tostring(ev) end
  local parts = {}
  for _, k in ipairs(keys_of(ev) or {}) do
    local v = ev[k]
    local s
    if type(v) == 'table' then s = val(v, 80)
    elseif type(v) == 'number' then s = val(v)
    else s = tostring(v) end
    parts[#parts + 1] = tostring(k) .. '=' .. s
  end
  local mt = getmetatable(ev)
  if mt ~= nil then parts[#parts + 1] = 'metatable=' .. type(mt) end
  if extra and extra > 0 then parts[#parts + 1] = '+' .. extra .. ' more args' end
  return table.concat(parts, ' ')
end

local function log_event(ev, t, extra)
  EVN = EVN + 1
  table.insert(EVLOG, 1, '#' .. EVN .. ' @' .. t .. ' ' .. ev_string(ev, extra))
  if #EVLOG > 16 then EVLOG[#EVLOG] = nil end
  if type(ev) == 'table' then
    local kind = tostring(ev.type) .. '/' .. tostring(ev.gesture or ev.key or ev.action or '-')
    EVKINDS[kind] = (EVKINDS[kind] or 0) + 1
  end
end

------------------------------------------------------------------ 1 INDEX
page('INDEX', {
  buttons = function()
    local bs = {}
    for i = 2, #PAGES do
      local k = i - 2
      local target = i
      bs[#bs + 1] = { x = (k % 2 == 0) and 16 or 248, y = 64 + (k // 2) * 50, w = 216, h = 44,
                      label = i .. ' ' .. PAGES[i].name, fn = function() cur = target end }
    end
    return bs
  end,
  lines = function()
    local out = {}
    add(out, 'life: ' .. table.concat(LIFE, ' '))
    add(out, 'last event: ' .. tostring(EVLOG[1] or 'none'))
    add(out, 'gc ' .. tostring(gcc()) .. '  last error: ' .. tostring(last_error or 'none'))
    return out
  end,
  ytop = 626,
})

------------------------------------------------------------------ 2 API (7.6.8 may differ from 7.2.4)
local api_cache
page('API', {
  lines = function()
    if api_cache then return api_cache end
    local out = {}
    add(out, '_VERSION ' .. tostring(_VERSION) .. '  globals: ' .. join(keys_of(_G)))
    add(out, 'ctx: ' .. join(keys_of(ctx)))
    for _, n in ipairs({ 'data', 'display', 'layers', 'gc', 'state', 'sys', 'system', 'assets', 'fonts',
                         'lock', 'perf', 'input', 'screen', 'i18n', 'log' }) do
      local t = ctx[n]
      local s = type(t)
      if type(t) == 'table' then
        s = join(keys_of(t))
        local mt = getmetatable(t)
        if mt ~= nil then s = s .. '  [mt ' .. type(mt) .. ']' end
      end
      add(out, 'ctx.' .. n .. ': ' .. s)
    end
    add(out, 'g: ' .. join(keys_of(g)) .. '  mt ' .. type(getmetatable(g)))
    local _, where = pcall(function() error('here') end)
    add(out, 'own chunk name: ' .. tostring(where))
    add(out, 'math.maxinteger ' .. tostring(math.maxinteger) .. ', 1<<31 = ' .. tostring(1 << 31) ..
             ', %.17g of 0.1 = ' .. string.format('%.17g', 0.1) .. ', 2^53+1 = ' .. string.format('%.17g', 2 ^ 53 + 1))
    add(out, 'ctx.longtask: ' .. (type(ctx.longtask) == 'table' and join(keys_of(ctx.longtask)) or type(ctx.longtask)))
    api_cache = out
    return out
  end,
})

------------------------------------------------------------------ 3 FILES: path forms
local FILES_PATHS = { 'where.txt', './where.txt', 'data/where.txt', '/where.txt', '../where.txt',
  '../../where.txt', '/sdcard/where.txt', '/sdcard/XTApps/where.txt',
  '/sdcard/XTApps/probe-io/where.txt', '/XTApps/probe-io/where.txt', 'XTApps/probe-io/where.txt',
  'data/story.txt', 'story.txt', 'STORY.TXT', 'nope.txt', 'index.lua', 'data', '' }
local files_cache
local function brief(r)
  if not r[1] then return 'ERR ' .. short(errs(r[2]), 40) end
  local s = val(r[2], 12)
  if r.n >= 3 then s = s .. ',' .. val(r[3], 14) end
  return s
end
page('FILES', {
  lines = function()
    if files_cache then return files_cache end
    local out = {}
    add(out, 'path: exists | size | read_text (where.txt says where it is)')
    for _, p in ipairs(FILES_PATHS) do
      local e = data_call('exists', p)
      local z = data_call('size', p)
      local r = data_call('read_text', p)
      local rd
      if r[1] and type(r[2]) == 'string' then
        rd = '"' .. short((r[2]:gsub('\n', '/')), 12) .. '" #' .. #r[2]
      else
        rd = brief(r)
      end
      add(out, "'" .. p .. "': " .. brief(e) .. ' | ' .. brief(z) .. ' | ' .. rd)
    end
    files_cache = out
    return out
  end,
})

------------------------------------------------------------------ 4 DATA-API: members, signatures, writes
local WRITE_NAMES = { 'write', 'write_text', 'writeText', 'save', 'save_text', 'put', 'put_text', 'append',
  'append_text', 'set', 'store', 'open', 'open_write', 'open_file', 'create', 'mkdir', 'remove', 'delete',
  'unlink', 'rename', 'list', 'dir', 'ls', 'files', 'listdir', 'read', 'read_bytes', 'read_binary',
  'open_binary', 'path', 'root', 'base', 'abs', 'stat', 'mtime', 'lines' }
local dapi_cache
page('DATA-API', {
  lines = function()
    if dapi_cache then return dapi_cache end
    local out = {}
    add(out, 'ctx.data pairs: ' .. join(keys_of(ctx.data)))
    local mt = getmetatable(ctx.data)
    add(out, 'ctx.data metatable: ' .. type(mt) .. (type(mt) == 'table' and (' ' .. join(keys_of(mt))) or ''))
    local hits = {}
    for _, n in ipairs(WRITE_NAMES) do
      local ok, v = pcall(function() return ctx.data[n] end)
      if ok and v ~= nil then hits[#hits + 1] = n end
    end
    add(out, 'other names on ctx.data: ' .. (#hits > 0 and table.concat(hits, ' ') or 'none'))
    local chits = {}
    for _, n in ipairs({ 'write', 'store', 'kv', 'prefs', 'storage', 'settings', 'fs', 'file', 'files',
                         'io', 'os', 'persist', 'db', 'load', 'save_state', 'storage' }) do
      local ok, v = pcall(function() return ctx[n] end)
      if ok and v ~= nil then chits[#chits + 1] = n .. ':' .. type(v) end
    end
    add(out, 'write-ish names on ctx: ' .. (#chits > 0 and table.concat(chits, ' ') or 'none'))
    add(out, 'signatures (error text):')
    local tests = {
      { 'exists()', 'exists' }, { 'size()', 'size' }, { 'read_text()', 'read_text' },
      { 'read_text(1)', 'read_text', 1 }, { 'open_text()', 'open_text' },
      { "read_text('story.txt',10)", 'read_text', 'story.txt', 10 },
      { "read_text('story.txt',0,10)", 'read_text', 'story.txt', 0, 10 },
      { "size('data')", 'size', 'data' }, { "exists('data')", 'exists', 'data' },
      { "exists('lib/mod.lua')", 'exists', 'lib/mod.lua' },
      { "size('big400k.txt')", 'size', 'big400k.txt' }, { "size('lad4m.txt')", 'size', 'lad4m.txt' },
      { "exists('/')", 'exists', '/' },
    }
    for _, t in ipairs(tests) do
      local r = call(ctx.data[t[2]], ctx.data, table.unpack(t, 3))
      if r[1] and type(r[2]) == 'string' then
        add(out, '  ' .. t[1] .. ' -> string #' .. #r[2] .. ' ' .. val(r[2], 16))
      else
        add(out, '  ' .. t[1] .. ' -> ' .. results(r))
      end
    end
    dapi_cache = out
    return out
  end,
})

------------------------------------------------------------------ 5 CONTENT: exact bytes
local function check_big(name, size)
  local t0 = now()
  local r = data_call('read_text', name)
  local t1 = now()
  if not r[1] or type(r[2]) ~= 'string' then
    return name .. ': ' .. results(r) .. ' (' .. (t1 - t0) .. ' ms)'
  end
  local s = r[2]
  r = nil
  local n = #s
  local bad = nil
  for k = 1, size // 64 do
    if s:sub((k - 1) * 64 + 1, k * 64) ~= line_of(k) then bad = k; break end
  end
  local t2 = now()
  local verdict
  if bad then verdict = 'line ' .. bad .. ' differs'
  elseif n == size then verdict = 'EXACT'
  else verdict = 'longer than expected' end
  s = nil
  gccollect()
  return name .. ': ' .. n .. ' B (want ' .. size .. ') ' .. verdict .. ', read ' .. (t1 - t0) ..
         ' ms, compare ' .. (t2 - t1) .. ' ms'
end

local function check_bytes(name, rev)
  local r = data_call('read_text', name)
  if not r[1] or type(r[2]) ~= 'string' then return name .. ': ' .. results(r) end
  local s = r[2]
  local bad
  for i = 1, #s do
    local want = (i - 1) % 256
    if rev then want = 255 - want end
    if s:byte(i) ~= want then bad = i; break end
  end
  local verdict
  if bad then verdict = 'byte ' .. bad .. ' is ' .. s:byte(bad) .. ', differs'
  elseif #s == 1024 then verdict = 'EXACT'
  else verdict = 'prefix ok' end
  return name .. ': ' .. #s .. ' B (want 1024) ' .. verdict .. ', adler ' .. string.format('%08x', adler32(s))
end

local content_cache
page('CONTENT', {
  lines = function()
    if content_cache then return content_cache end
    local out = {}
    local t0 = now()
    local r = data_call('read_text', 'story.txt')
    local t1 = now()
    if r[1] and type(r[2]) == 'string' then
      local s = r[2]
      local okl, ul = pcall(utf8.len, s)
      local _, nl = s:gsub('\n', '')
      local a = adler32(s)
      local exact = (#s == STORY_LEN and a == STORY_ADLER)
      add(out, 'story.txt: ' .. #s .. ' B (want ' .. STORY_LEN .. '), adler ' .. string.format('%08x', a) ..
               ' (want ' .. string.format('%08x', STORY_ADLER) .. ') ' .. (exact and 'EXACT' or 'DIFFERENT'))
      add(out, '  utf8.len ' .. tostring(okl and ul or 'ERR') .. ' (want ' .. STORY_CHARS .. '), lines ' .. nl ..
               ' (want ' .. STORY_LINES .. '), read ' .. (t1 - t0) .. ' ms')
      add(out, '  CR bytes: ' .. select(2, s:gsub('\r', '')) .. ', 2nd return: ' .. val(r[3]))
    else
      add(out, 'story.txt: ' .. results(r))
    end
    add(out, check_big('big100k.txt', 102400))
    add(out, check_big('big400k.txt', 409600))
    add(out, check_bytes('bytes.bin', false))
    add(out, check_bytes('bytes-r.bin', true))
    local r2 = data_call('read_text', 'big100k.txt', 100)
    add(out, "read_text('big100k.txt',100) -> " .. (r2[1] and type(r2[2]) == 'string' and ('#' .. #r2[2]) or results(r2)))
    r2 = nil
    gccollect()
    add(out, 'gc after: ' .. tostring(gcc()))
    content_cache = out
    return out
  end,
})

------------------------------------------------------------------ 6 OPEN_TEXT
local HNAMES = { 'read', 'read_line', 'readline', 'readLine', 'line', 'lines', 'next', 'next_line',
  'get_line', 'getline', 'gets', 'read_all', 'readall', 'all', 'read_text', 'text', 'chunk',
  'read_chunk', 'eof', 'is_eof', 'done', 'tell', 'pos', 'seek', 'size', 'length', 'len', 'close',
  'dispose', 'release', 'free', 'reset', 'rewind', 'remaining', 'available', 'ok', 'valid', 'path',
  'name', 'iter', 'peek', 'skip', 'offset' }
local READERS = { 'read_line', 'readline', 'readLine', 'line', 'next_line', 'get_line', 'getline', 'gets',
  'next', 'read' }

local function open(name, ...) return call(ctx.data.open_text, ctx.data, name, ...) end
local function member(h, n)
  local ok, v = pcall(function() return h[n] end)
  if ok then return v end
  return nil
end
local function close_h(h)
  for _, n in ipairs({ 'close', 'dispose', 'release', 'free' }) do
    local f = member(h, n)
    if type(f) == 'function' then pcall(f, h); return n end
  end
  return nil
end

local OT = { reader = nil }
-- the first method that returns a string line on a fresh handle
local function find_reader()
  if OT.reader ~= nil then return OT.reader end
  OT.reader = false
  for _, n in ipairs(READERS) do
    local r = open('story.txt')
    if r[1] and r[2] ~= nil then
      local h = r[2]
      local f = member(h, n)
      if type(f) == 'function' then
        local x = call(f, h)
        close_h(h)
        if x[1] and type(x[2]) == 'string' then OT.reader = n; break end
      end
    end
  end
  gccollect()
  return OT.reader
end

-- stream a generated file through the reader: lines, bytes, newline kept?, content check
local function stream(name, want_lines)
  local rd = find_reader()
  if not rd then return name .. ': no line reader found' end
  local r = open(name)
  if not r[1] or r[2] == nil then return name .. ': open_text -> ' .. results(r) end
  local h = r[2]
  local f = member(h, rd)
  local t0 = now()
  local count, bytes, bad, nl, stop = 0, 0, nil, nil, 'cap'
  for k = 1, want_lines + 10 do
    local x = call(f, h)
    if not x[1] then stop = 'ERR ' .. errs(x[2]); break end
    local l = x[2]
    if type(l) ~= 'string' then stop = val(l) .. (x.n >= 3 and (',' .. val(x[3])) or ''); break end
    count = count + 1
    bytes = bytes + #l
    if nl == nil then nl = (l:sub(-1) == '\n') end
    local want = line_of(k)
    if not nl then want = want:sub(1, -2) end
    if bad == nil and l ~= want then bad = k end
  end
  local t1 = now()
  close_h(h)
  return name .. ' via :' .. rd .. '(): ' .. count .. ' lines (want ' .. want_lines .. '), ' .. bytes ..
         ' B, newline kept ' .. tostring(nl) .. ', ' .. (bad and ('line ' .. bad .. ' differs') or 'all lines match') ..
         ', end: ' .. stop .. ', ' .. (t1 - t0) .. ' ms'
end

local ot_cache
page('OPEN_TEXT', {
  lines = function()
    if ot_cache then return ot_cache end
    local out = {}
    local r = open('story.txt')
    add(out, "open_text('story.txt') -> " .. results(r))
    if not r[1] or r[2] == nil then ot_cache = out; return out end
    local h = r[2]
    add(out, 'type ' .. type(h) .. ', pairs: ' .. join(keys_of(h)))
    local mt = getmetatable(h)
    local ms = 'metatable: ' .. type(mt)
    if type(mt) == 'table' then
      ms = ms .. ' keys ' .. join(keys_of(mt))
      if type(mt.__index) == 'table' then ms = ms .. ' | __index: ' .. join(keys_of(mt.__index)) end
    end
    add(out, ms)
    local found = {}
    for _, n in ipairs(HNAMES) do
      if member(h, n) ~= nil then found[#found + 1] = n end
    end
    add(out, 'names that answer: ' .. (#found > 0 and table.concat(found, ' ') or 'none'))
    close_h(h)
    h = nil
    -- every function member, called twice on a fresh handle (does it advance?)
    for _, n in ipairs(found) do
      local r2 = open('story.txt')
      if r2[1] and r2[2] ~= nil then
        local h2 = r2[2]
        local f = member(h2, n)
        if type(f) == 'function' then
          local a = call(f, h2)
          local b = call(f, h2)
          add(out, ':' .. n .. '() -> ' .. results(a, 20) .. ' | again -> ' .. results(b, 20))
        else
          add(out, '.' .. n .. ' = ' .. val(f))
        end
        if n ~= 'close' then close_h(h2) end
      end
    end
    -- read(n) / read(fmt) variants, if there is a read
    for _, arg in ipairs({ 10, 'l', 'L', 'a', '*l', 'n' }) do
      local r3 = open('story.txt')
      if r3[1] and r3[2] ~= nil and type(member(r3[2], 'read')) == 'function' then
        local x = call(r3[2].read, r3[2], arg)
        add(out, ':read(' .. val(arg) .. ') -> ' .. results(x, 20))
        close_h(r3[2])
      end
    end
    gccollect()
    add(out, 'line reader: ' .. tostring(find_reader()))
    add(out, stream('big100k.txt', 1600))
    add(out, stream('big400k.txt', 6400))
    add(out, "open_text('nope.txt') -> " .. results(open('nope.txt')))
    add(out, "open_text('w.txt','w') -> " .. results(open('w.txt', 'w')) ..
             '; exists after: ' .. results(data_call('exists', 'w.txt')))
    gccollect()
    ot_cache = out
    return out
  end,
})

------------------------------------------------------------------ 7 REQUIRE
local rq_cache
page('REQUIRE', {
  lines = function()
    if rq_cache then return rq_cache end
    local out = {}
    add(out, 'type(require) ' .. type(require) .. ', type(package) ' .. type(package))
    if type(package) == 'table' then
      add(out, 'package: ' .. join(keys_of(package)))
      add(out, 'package.path: ' .. tostring(package.path))
      if type(package.loaded) == 'table' then add(out, 'package.loaded: ' .. join(keys_of(package.loaded))) end
    end
    local a = call(require, 'mod')
    local b = call(require, 'mod')
    add(out, "require('mod') -> " .. results(a, 30))
    if a[1] and type(a[2]) == 'table' then
      local m = a[2]
      add(out, '  loads ' .. tostring(m.loads) .. ', sees ctx ' .. tostring(m.sees_ctx) .. ', g ' .. tostring(m.sees_g))
      add(out, '  args: ' .. tostring(m.arg1) .. ' | ' .. tostring(m.arg2))
      add(out, '  chunk: ' .. tostring(m.chunk))
      add(out, '  2nd require same table: ' .. tostring(b[1] and rawequal(a[2], b[2])) ..
               ', PROBE_MOD_LOADS now ' .. tostring(PROBE_MOD_LOADS))
    end
    for _, n in ipairs({ 'lib.mod', 'lib/mod', 'lib', './mod', 'mod.lua', 'nope' }) do
      local r = call(require, n)
      if r[1] and type(r[2]) == 'table' then
        add(out, "require('" .. n .. "') -> " .. tostring(r[2].name) .. ', arg2 ' .. tostring(r[2].arg2) ..
                 (r[2].chunk and (', chunk ' .. tostring(r[2].chunk)) or ''))
      elseif r[1] then
        add(out, "require('" .. n .. "') -> " .. results(r))
      else
        add(out, "require('" .. n .. "') ERR " .. tostring(r[2]))
      end
    end
    local s = call(require, 'string')
    add(out, "require('string') is string: " .. tostring(s[1] and rawequal(s[2], string)) .. ' ' .. results(s, 12))
    rq_cache = out
    return out
  end,
})

------------------------------------------------------------------ 8 PIXELS: what one call inks
local MARKS = {
  { 'A', 100, 450, 'g:line(100,450,100,450)', function() g:line(100, 450, 100, 450) end },
  { 'B', 200, 450, 'g:rect(200,450,1,1)', function() g:rect(200, 450, 1, 1) end },
  { 'C', 300, 450, 'g:rect(300,450,2,2)', function() g:rect(300, 450, 2, 2) end },
  { 'D', 400, 450, 'g:circle(400,450,0)', function() g:circle(400, 450, 0) end },
  { 'E', 100, 560, 'g:rect(100,560,10,5)', function() g:rect(100, 560, 10, 5) end },
  { 'F', 200, 560, 'g:line(200,560,209,560)', function() g:line(200, 560, 209, 560) end },
  { 'G', 300, 560, 'g:line(300,560,300,569)', function() g:line(300, 560, 300, 569) end },
  { 'H', 400, 560, 'g:circle(400,560,3)', function() g:circle(400, 560, 3) end },
  { 'I', 100, 660, 'g:rect(100,660,0,0)', function() g:rect(100, 660, 0, 0) end },
  { 'J', 200, 660, 'g:line(209,660,200,660)', function() g:line(209, 660, 200, 660) end },
  { 'K', 300, 660, 'g:rect(300,660,1,10)', function() g:rect(300, 660, 1, 10) end },
  { 'L', 400, 660, 'g:line(400,660,409,669)', function() g:line(400, 660, 409, 669) end },
}
page('PIXELS', {
  draw = function()
    local out = {}
    for _, m in ipairs(MARKS) do
      local ok, e = pcall(m[5])
      add(out, m[1] .. ' at (' .. m[2] .. ',' .. m[3] .. ') ' .. m[4] .. (ok and '' or (' ERR ' .. errs(e))))
      g:text(m[2] - 4, m[3] - 44, m[1])
    end
    draw_lines(out, TOP, 395)
  end,
})

------------------------------------------------------------------ 9..11 SPEED, 12..13 CAP
-- A frame holds at most 1024 drawing commands (g:clear included): the 7.6.8 host drops every
-- call after that, text too, so these pages draw their text FIRST and show the measurement of the
-- PREVIOUS draw (tap the middle to redraw = measure again). The Lua-side times are the cost of
-- recording the calls; the rasterising happens after on_draw returns.
local SP = { hist10 = {}, hist40 = {}, histpx = {} }
local function nop() end
local function push_hist(h, s)
  table.insert(h, 1, s)
  if #h > 3 then h[#h] = nil end
end
local function rate(n, ms) if ms <= 0 then return 'inf' end return tostring(n * 1000 // ms) end
local function speed_text(desc, hist)
  local out = {}
  for _, h in ipairs(hist) do add(out, h) end
  for _, d in ipairs(desc) do add(out, d) end
  if #hist == 0 then add(out, '(first draw: tap the middle to see the measurement)') end
  draw_lines(out, TOP, 148)
end
local function perf_line()
  local ok, info = pcall(ctx.perf.info, ctx.perf)
  return 'perf:info() ' .. (ok and val(info, 80) or ('ERR ' .. errs(info)))
end

page('SPEED-10K', {
  draw = function()
    speed_text({ perf_line() }, SP.hist10)
    local t0 = now()
    for row = 0, 499 do
      local y = 150 + row
      for c = 0, 19 do
        local x = 20 + c * 22
        nop(g, x, y, x + c, y)
      end
    end
    local t1 = now()
    for row = 0, 499 do
      local y = 150 + row
      for c = 0, 19 do
        local x = 20 + c * 22
        g:line(x, y, x + c, y)
      end
    end
    local t2 = now()
    push_hist(SP.hist10, 'draw #' .. draws .. ': 10,000 g:line ' .. (t2 - t1) .. ' ms = ' .. rate(10000, t2 - t1) ..
              '/s (Lua no-op loop ' .. (t1 - t0) .. ' ms)')
  end,
})

page('SPEED-40K', {
  draw = function()
    speed_text({ perf_line() }, SP.hist40)
    local t0 = now()
    for p = 1, 4 do
      local L = 5 * p
      for row = 0, 499 do
        local y = 150 + row
        for c = 0, 19 do
          local x = 20 + c * 22
          g:line(x, y, x + L - 1, y)
        end
      end
    end
    local t1 = now()
    push_hist(SP.hist40, 'draw #' .. draws .. ': 40,000 g:line ' .. (t1 - t0) .. ' ms = ' .. rate(40000, t1 - t0) .. '/s')
  end,
})

page('SPEED-PX', {
  draw = function()
    speed_text({ perf_line() }, SP.histpx)
    local t0 = now()
    for r = 0, 99 do
      local y = 150 + 2 * r
      for c = 0, 99 do
        local x = 20 + 2 * c
        g:line(x, y, x, y)
      end
    end
    local t1 = now()
    for r = 0, 99 do
      local y = 150 + 2 * r
      for c = 0, 99 do
        local x = 260 + 2 * c
        g:rect(x, y, 1, 1)
      end
    end
    local t2 = now()
    local k = 0
    for i = 1, 1000000 do k = k + i end
    local t3 = now()
    local s = 'The quick brown fox jumps over the lazy dog 0123'
    for i = 0, 199 do g:text(PX, 380 + (i % 11) * 24, s) end
    local t4 = now()
    push_hist(SP.histpx, 'draw #' .. draws .. ': line px ' .. (t1 - t0) .. ' ms, rect 1x1 ' .. (t2 - t1) ..
              ' ms, 1e6 adds ' .. (t3 - t2) .. ' ms, 200 text ' .. (t4 - t3) .. ' ms')
  end,
})

-- CAP: 6 text lines, then 2000 numbered 40-px segments (segment i: row i//10 at y 200+2*row,
-- column i%10 at x 20+44*col). The last segment that shows tells the frame's command limit and
-- whether a text call counts as one command.
page('CAP', {
  draw = function()
    for i = 1, 6 do g:text(PX, TOP + (i - 1) * 22, 'text line ' .. i .. ' of 6 drawn before the segments') end
    for i = 0, 1999 do
      local row, col = i // 10, i % 10
      local y = 200 + 2 * row
      local x = 20 + 44 * col
      g:line(x, y, x + 39, y)
    end
  end,
})

-- CAP-LAYER: a 480x320 layer painted with 3000 segments in one draw_with (segment i: col i%20 at
-- layer x 10+23*col, 18 px; row i//20 at layer y 10+2*row), blitted at y 300. Does a layer have
-- its own limit, and how long does painting it take?
local CL = {}
page('CAP-LAYER', {
  draw = function()
    if not CL.L then
      local r = call(ctx.layers.create, ctx.layers, 480, 320)
      CL.create = results(r)
      if r[1] and r[2] then
        CL.L = r[2]
        local t0 = now()
        CL.dw = results(call(function()
          return CL.L:draw_with(function(q)
            for i = 0, 2999 do
              local col, row = i % 20, i // 20
              local x, y = 10 + 23 * col, 10 + 2 * row
              q:line(x, y, x + 17, y)
            end
          end)
        end))
        CL.ms = now() - t0
      end
    end
    local out = {}
    add(out, 'create -> ' .. tostring(CL.create))
    add(out, 'draw_with(3000 segments) -> ' .. tostring(CL.dw) .. ' in ' .. tostring(CL.ms) .. ' ms (first draw only)')
    add(out, perf_line())
    draw_lines(out, TOP, 290)
    if CL.L then
      local t0 = now()
      local b = call(g.layer, g, CL.L, 0, 300)
      add(out, 'blit -> ' .. results(b) .. ' ' .. (now() - t0) .. ' ms')
    end
  end,
})

------------------------------------------------------------------ 14 TEXTPAGE: a page of story text
local story_lines
page('TEXTPAGE', {
  draw = function()
    if not story_lines then
      story_lines = {}
      local r = data_call('read_text', 'story.txt')
      if r[1] and type(r[2]) == 'string' then
        for l in r[2]:gmatch('([^\n]*)\n') do add(story_lines, l) end
      else
        story_lines[1] = 'story.txt: ' .. results(r)
      end
    end
    local t0 = now()
    local y = TOP
    for _, l in ipairs(story_lines) do
      if y > 640 then break end
      g:text(PX, y, l)
      y = y + 26
    end
    local t1 = now()
    g:text(PX, 690, #story_lines .. ' wrapped lines, g:text total ' .. (t1 - t0) .. ' ms')
  end,
})

------------------------------------------------------------------ 15 LAYERS
local LY = { tried = false, log = {}, blits = 0 }
local function ly_log(s) add(LY.log, s) end
local function layer_setup()
  LY.tried = true
  local r = call(ctx.layers.create, ctx.layers, 480, 320)
  ly_log('create(480,320) -> ' .. results(r))
  if not r[1] or r[2] == nil then return end
  local L = r[2]
  LY.L = L
  local mt = getmetatable(L)
  ly_log('layer ' .. type(L) .. ' keys ' .. join(keys_of(L)) .. ' mt ' .. type(mt))
  local seen = 'callback not called'
  local function paint(...)
    seen = argsig(...)
    local a = table.pack(...)
    local gg
    for i = 1, a.n do
      if (type(a[i]) == 'table' or type(a[i]) == 'userdata') and member(a[i], 'line') then gg = a[i] end
    end
    if not gg then return end
    LY.g2_is_g = rawequal(gg, g)
    local okz, w, h = pcall(function() return gg:size() end)
    LY.g2_size = okz and (tostring(w) .. 'x' .. tostring(h)) or 'ERR'
    gg:rect(0, 0, 480, 320)
    gg:rect(4, 4, 472, 312)
    gg:line(0, 0, 479, 319)
    gg:line(0, 319, 479, 0)
    gg:circle(240, 160, 100)
    gg:text(20, 20, 'LAYER, painted once at draw #' .. draws)
    gg:text(20, 270, 'white inside? hatch shows through?')
  end
  local t0 = now()
  local d = call(function() return L:draw_with(paint) end)
  LY.paint_ms = now() - t0
  ly_log('L:draw_with(fn) -> ' .. results(d) .. '; fn got ' .. seen .. '; g2==g ' .. tostring(LY.g2_is_g) ..
         ', g2:size ' .. tostring(LY.g2_size) .. ', ' .. LY.paint_ms .. ' ms')
  if not d[1] or seen == 'callback not called' then
    local d2 = call(function() return L.draw_with(paint) end)
    ly_log('L.draw_with(fn) -> ' .. results(d2) .. '; fn got ' .. seen)
  end
  -- a small second layer, to see an x offset
  local r2 = call(ctx.layers.create, ctx.layers, 150, 80)
  if r2[1] and r2[2] ~= nil then
    LY.S = r2[2]
    pcall(function()
      LY.S:draw_with(function(...)
        local a = table.pack(...)
        for i = 1, a.n do
          if member(a[i], 'line') then
            local q = a[i]
            q:rect(0, 0, 150, 80)
            q:line(0, 0, 149, 79)
            q:text(10, 28, 'small 150x80')
          end
        end
      end)
    end)
  end
end

page('LAYERS', {
  draw = function()
    if not LY.tried then layer_setup() end
    -- a hatch in the frame under the layer area: does the layer cover it?
    for y = 300, 620, 20 do g:line(0, y, 479, y) end
    for x = 0, 479, 40 do g:line(x, 300, x, 620) end
    local blit = 'no layer'
    if LY.L then
      local variants = {
        { 'g:layer(L,0,300)', function() return g:layer(LY.L, 0, 300) end },
        { 'g:layer(L,{x=0,y=300})', function() return g:layer(LY.L, { x = 0, y = 300 }) end },
        { 'g:layer(L)', function() return g:layer(LY.L) end },
      }
      blit = ''
      for _, v in ipairs(variants) do
        local r = call(v[2])
        blit = blit .. v[1] .. ' -> ' .. results(r) .. '; '
        if r[1] then break end
      end
      local t0 = now()
      for _ = 1, 10 do pcall(g.layer, g, LY.L, 0, 300) end
      LY.blit_ms = (now() - t0) / 10
      LY.blits = LY.blits + 11
    end
    if LY.S then pcall(g.layer, g, LY.S, 320, 636) end
    local out = {}
    for _, s in ipairs(LY.log) do out[#out + 1] = s end
    add(out, blit)
    add(out, 'blit 480x320: ' .. tostring(LY.blit_ms) .. ' ms each (10 timed); blits so far ' .. LY.blits ..
             '; the layer is painted only once')
    draw_lines(out, TOP, 296)
    box(10, 640, 290, 70, 'redraw (blit only)')
  end,
  buttons = { { x = 10, y = 640, w = 290, h = 70, label = '', fn = function() end } },
})

------------------------------------------------------------------ 16 TICKS
local TK = { log = {}, count = 0, set_at = nil, set_count = 0, rate = 'default', ret = '-', extra = 0 }
function on_tick(c, n, ...)
  local t = now()
  TK.count = TK.count + 1
  local lg = TK.log
  lg[#lg + 1] = { t, n }
  if #lg > 40 then table.remove(lg, 1) end
  local e = select('#', ...)
  if e > TK.extra then TK.extra = e end
  if TK.first == nil then TK.first = t; TK.first_arg = argsig(c, n, ...) end
end

-- set_tick_rate reads its option from argument #2 (luaL_optstring, default 'normal'), so it is
-- called as ctx:set_tick_rate(name); 7.6.8 maps realtime 33, high 50, normal 100, low 500,
-- idle 2000 ms and anything else to 100 ms (disassembly of 0x42228390). A number becomes '5'.
local TICK_RATES = { 'realtime', 'high', 'normal', 'low', 'idle', 5, 'x', '(none)', 'dot 1' }
page('TICKS', {
  lines = function()
    local out = {}
    local t = now()
    add(out, 'ticks ' .. TK.count .. ', first at t=' .. tostring(TK.first) .. ' args ' .. tostring(TK.first_arg))
    add(out, 'rate: ' .. tostring(TK.rate) .. '  set_tick_rate -> ' .. TK.ret)
    local since_t = TK.set_at or TK.first or t
    local since_n = TK.count - TK.set_count
    add(out, 'since set: ' .. since_n .. ' ticks in ' .. (t - since_t) .. ' ms' ..
             (since_n > 0 and (' = ' .. ((t - since_t) // since_n) .. ' ms/tick') or ''))
    local lg = TK.log
    local first = math.max(2, #lg - 15)
    for i = first, #lg do
      local e = lg[i]
      add(out, 't=' .. e[1] .. ' dt=' .. (e[1] - lg[i - 1][1]) .. ' n=' .. val(e[2]) ..
               (type(e[2]) == 'number' and (' ' .. math.type(e[2])) or ''))
    end
    return out
  end,
  ymax = 556,
  buttons = function()
    local specs = {}
    for _, r in ipairs(TICK_RATES) do
      specs[#specs + 1] = { 'rate ' .. tostring(r), function()
        if r == '(none)' then TK.ret = results(call(ctx.set_tick_rate, ctx))
        elseif r == 'dot 1' then TK.ret = results(call(ctx.set_tick_rate, 1))
        else TK.ret = results(call(ctx.set_tick_rate, ctx, r)) end
        TK.rate = r
        TK.set_at = now()
        TK.set_count = TK.count
        TK.log = {}
      end }
    end
    return grid(specs, 3, 562, 48, 8)
  end,
})

------------------------------------------------------------------ 17 REFRESH
local RF = { log = {} }
-- request_refresh also reads argument #2: ctx:request_refresh('full' | 'partial'), default
-- 'partial'; it only sets the mode of the next refresh (no refresh of its own).
local RR_ARGS = { 'full', 'partial', 'fast', 'gc' }
local rr_i = 1
local function rf_log(s)
  table.insert(RF.log, 1, s)
  if #RF.log > 12 then RF.log[#RF.log] = nil end
end
local function disp(name)
  local r = call(ctx.display[name], ctx.display)
  if not r[1] then r = call(ctx.display[name]) end
  return results(r)
end
page('REFRESH', {
  lines = function()
    local out = {}
    add(out, 'draw #' .. draws .. '; ctx.display: ' .. join(keys_of(ctx.display)))
    for _, s in ipairs(RF.log) do add(out, s) end
    return out
  end,
  ymax = 506,
  buttons = function()
    return grid({
      { 'invalidate', function(t) rf_log(t .. ' invalidate()') end },
      { 'request_refresh', function(t) rf_log(t .. ' request_refresh() -> ' .. results(call(ctx.request_refresh)) .. ' (no inv)'); return 'noinv' end },
      { 'rr() + inv', function(t) rf_log(t .. ' request_refresh()+inv -> ' .. results(call(ctx.request_refresh))) end },
      { 'full + inv', function(t) rf_log(t .. ' display:full_refresh()+inv -> ' .. disp('full_refresh')) end },
      { 'full only', function(t) rf_log(t .. ' display:full_refresh() -> ' .. disp('full_refresh') .. ' (no inv)'); return 'noinv' end },
      { 'defer + inv', function(t) rf_log(t .. ' display:defer_auto_full()+inv -> ' .. disp('defer_auto_full')) end },
      { ':rr(' .. tostring(RR_ARGS[rr_i]) .. ')+inv', function(t)
          local a = RR_ARGS[rr_i]
          rr_i = rr_i % #RR_ARGS + 1
          rf_log(t .. ' ctx:request_refresh(' .. val(a) .. ')+inv -> ' .. results(call(ctx.request_refresh, ctx, a)))
        end },
    }, 2, 512, 46, 6)
  end,
})

------------------------------------------------------------------ 18 INPUT
page('INPUT', {
  lines = function()
    local out = {}
    local kinds = {}
    for k, v in pairs(EVKINDS) do kinds[#kinds + 1] = k .. ' x' .. v end
    table.sort(kinds)
    add(out, 'kinds: ' .. table.concat(kinds, ', '))
    for _, s in ipairs(EVLOG) do add(out, s) end
    return out
  end,
})

------------------------------------------------------------------ 19 STATE: ctx.state + ctx.save
local ST = { log = {}, at_load = {} }
local function st_log(s) table.insert(ST.log, 1, s); if #ST.log > 8 then ST.log[#ST.log] = nil end end
local function dump_tree(v, out, prefix, depth)
  local ks = keys_of(v)
  if not ks then out[#out + 1] = prefix .. '(not walkable: ' .. type(v) .. ')'; return end
  if #ks == 0 then out[#out + 1] = prefix .. '(empty)'; return end
  for _, k in ipairs(ks) do
    local x = v[k]
    if type(x) == 'table' and depth < 3 then
      out[#out + 1] = prefix .. tostring(k) .. ' = {'
      dump_tree(x, out, prefix .. '  ', depth + 1)
    else
      local ts = type(x)
      if ts == 'number' then ts = math.type(x) end
      out[#out + 1] = prefix .. tostring(k) .. ' (' .. ts .. ') = ' .. val(x, 30)
    end
  end
end
local function state_snapshot()
  local out = {}
  dump_tree(ctx.state, out, '  ', 0)
  return out
end

page('STATE', {
  lines = function()
    local out = {}
    add(out, 'at on_load (t=' .. tostring(ST.load_t) .. ', type ' .. tostring(ST.type) .. ', mt ' .. tostring(ST.mt) .. '):')
    for i, s in ipairs(ST.at_load) do if i <= 12 then add(out, s) end end
    add(out, 'now:')
    local snap = state_snapshot()
    for i, s in ipairs(snap) do if i <= 12 then add(out, s) end end
    for _, s in ipairs(ST.log) do add(out, s) end
    return out
  end,
  ymax = 552,
  buttons = function()
    return grid({
      { 'set + save', function(t)
          local s = ctx.state
          s.x = 42
          s.f = 3.25
          s.big = math.maxinteger
          s.bigf = 2 ^ 40
          s.neg = -7
          s.str = 'h\u{e9}llo \u{201c}q\u{201d}'
          s.yes = true
          s.no = false
          s.t = { a = 1, b = { c = 'deep' } }
          s.arr = { 10, 20, 30 }
          s.saves = (s.saves or 0) + 1
          s.saved_at = t
          local r = call(ctx.save)
          local how = 'ctx.save()'
          if not r[1] then r = call(ctx.save, ctx); how = 'ctx.save(ctx)' end
          st_log(t .. ' set + ' .. how .. ' -> ' .. results(r))
        end },
      { 'set unsaved', function(t) ctx.state.unsaved = 'set at ' .. t .. ', no save'; st_log(t .. ' state.unsaved set (no save)') end },
      { 'save(args)', function(t)
          st_log("save('x') -> " .. results(call(ctx.save, 'x')))
          st_log('save({y=1}) -> ' .. results(call(ctx.save, { y = 1 })))
          st_log('save(ctx.state) -> ' .. results(call(ctx.save, ctx.state)))
        end },
      { 'write tests', function(t)
          local tried = {}
          for _, n in ipairs({ 'write', 'write_text', 'save', 'save_text', 'put', 'append', 'append_text', 'store' }) do
            local f = member(ctx.data, n)
            if type(f) == 'function' then
              tried[#tried + 1] = n .. ' ' .. results(call(f, ctx.data, 'w.txt', 'hello'))
            end
          end
          st_log('data writers: ' .. (#tried > 0 and table.concat(tried, '; ') or 'none bound') ..
                 '; exists(w.txt) ' .. results(data_call('exists', 'w.txt')))
        end },
      { 'fn in state', function(t)
          ctx.state.fn = function() end
          local r = call(ctx.save)
          ctx.state.fn = nil
          st_log(t .. ' save with a function value -> ' .. results(r))
        end },
      { 'clear + save', function(t)
          for _, k in ipairs(keys_of(ctx.state) or {}) do ctx.state[k] = nil end
          st_log(t .. ' cleared, save -> ' .. results(call(ctx.save)))
        end },
    }, 2, 558, 50, 6)
  end,
})

------------------------------------------------------------------ 20 LONG callbacks
local LG = { log = {}, pending = nil }
local function lg_log(s) table.insert(LG.log, 1, s); if #LG.log > 12 then LG.log[#LG.log] = nil end end
local function busy(n)
  local t0 = now()
  while now() - t0 < n do end
  return now() - t0
end
local function note(s)
  pcall(function() ctx.state.long_note = s; ctx.save() end)
end
local function long_draw(n) return function(t) LG.pending = n; note('on_draw ' .. n .. ' ms queued at ' .. t) end end
local function long_input(n)
  return function(t)
    note('on_input ' .. n .. ' ms started at ' .. t)
    local took = busy(n)
    lg_log('on_input busy ' .. n .. ': returned after ' .. took .. ' ms (started t=' .. t .. ')')
    note('on_input ' .. n .. ' ms finished, took ' .. took)
  end
end
-- ctx.longtask (7.6.8): start() gives a budget of 11 s (0xa7d8c0 us), feed() extends it by
-- another 11 s, stop() ends it; past the deadline the host raises "script long task exceeded
-- time budget" (disassembly of 0x422282dc / 0x42228274 / 0x422223d8).
local function long_task(n, feed)
  return function(t)
    local s = call(ctx.longtask.start, ctx.longtask)
    lg_log('longtask:start() -> ' .. results(s))
    note('longtask ' .. n .. ' started at ' .. t)
    local t0 = now()
    local last = t0
    local ok, e = pcall(function()
      while now() - t0 < n do
        if feed and now() - last > 1000 then ctx.longtask:feed(); last = now() end
      end
    end)
    lg_log('longtask ' .. n .. (feed and ' (fed)' or '') .. ': ' .. (ok and 'ran' or ('ERR ' .. errs(e))) ..
           ' after ' .. (now() - t0) .. ' ms')
    lg_log('longtask:stop() -> ' .. results(call(ctx.longtask.stop, ctx.longtask)))
    note('longtask ' .. n .. ' finished')
  end
end
page('LONG', {
  draw = function()
    if LG.pending then
      local n = LG.pending
      LG.pending = nil
      local t0 = now()
      note('on_draw ' .. n .. ' ms started at ' .. t0)
      local took = busy(n)
      lg_log('on_draw busy ' .. n .. ': returned after ' .. took .. ' ms (started t=' .. t0 .. ')')
      note('on_draw ' .. n .. ' ms finished, took ' .. took)
    end
    local out = {}
    add(out, 'note saved before/after each test, as found at on_load: ' .. tostring(ST.load_note))
    for _, s in ipairs(LG.log) do add(out, s) end
    draw_lines(out, TOP, 446)
  end,
  buttons = function()
    return grid({
      { 'draw 2 s', long_draw(2000) }, { 'input 2 s', long_input(2000) },
      { 'draw 5 s', long_draw(5000) }, { 'input 5 s', long_input(5000) },
      { 'draw 15 s', long_draw(15000) }, { 'input 15 s', long_input(15000) },
      { 'draw 30 s', long_draw(30000) }, { 'longtask 15 s', long_task(15000, false) },
      { 'longtask 30 s+feed', long_task(30000, true) }, { 'input 30 s', long_input(30000) },
    }, 2, 452, 48, 6)
  end,
})

------------------------------------------------------------------ 21 MEMORY
local MEM = { lines = nil, pending = false }
local function mem_run()
  local out = {}
  gccollect()
  local g0 = gcc()
  add(out, 'gc:count() before: ' .. val(g0))
  local n, best, err = 1024, 0, nil
  local t0 = now()
  while n <= (1 << 31) do
    local ok, s = pcall(string.rep, 'x', n)
    if not ok then err = s; break end
    best = n
    s = nil
    gccollect()
    n = n * 2
  end
  local lo, hi = best, n
  if err then
    for _ = 1, 12 do
      local mid = (lo + hi) // 2
      local ok, s = pcall(string.rep, 'x', mid)
      if ok then lo = mid else hi = mid end
      s = nil
      gccollect()
    end
  end
  add(out, 'largest string.rep: ' .. lo .. ' B (fails at ' .. hi .. '), ' .. (now() - t0) .. ' ms')
  add(out, '  first error: ' .. tostring(err))
  add(out, 'gc:count() after the strings: ' .. val(gcc()))
  local t = {}
  local t1 = now()
  local ok, e = pcall(function()
    for i = 1, 50000000 do t[i] = 'k' .. i end
  end)
  local cnt = #t
  local ms_fill = now() - t1
  local peak = gcc()
  t = nil
  gccollect()
  add(out, 'table of small strings ("k"..i): ' .. cnt .. ' entries in ' .. ms_fill .. ' ms, ' ..
           (ok and 'no failure' or ('error: ' .. tostring(e))))
  add(out, 'gc:count() at the peak: ' .. val(peak))
  add(out, 'gc:count() after release + collect: ' .. val(gcc()))
  local ok2, s2 = pcall(string.rep, 'y', 100000)
  add(out, 'a 100 KB string afterwards: ' .. (ok2 and ('ok #' .. #s2) or tostring(s2)))
  s2 = nil
  gccollect()
  MEM.lines = out
end
page('MEMORY', {
  draw = function()
    if MEM.pending then MEM.pending = false; local ok, e = pcall(mem_run); if not ok then MEM.lines = { 'mem_run ERR ' .. tostring(e) } end end
    local out = {}
    add(out, 'gc:count() now: ' .. val(gcc()))
    for _, s in ipairs(MEM.lines or { 'tap "run" (allocates until it fails)' }) do add(out, s) end
    draw_lines(out, TOP, 630)
  end,
  buttons = function()
    return grid({ { 'run memory test', function() MEM.pending = true end },
                  { 'redraw', function() end } }, 2, 640, 70, 10)
  end,
})

------------------------------------------------------------------ 22 LADDER: the size limit
local LADDER = { { 'lad064k.txt', 65536 }, { 'lad128k.txt', 131072 }, { 'lad256k.txt', 262144 },
  { 'lad512k.txt', 524288 }, { 'lad1m.txt', 1048576 }, { 'lad2m.txt', 2097152 }, { 'lad4m.txt', 4194304 } }
local LD = { lines = nil, pending = false }
local function ladder_run()
  local out = {}
  for _, e in ipairs(LADDER) do
    local z = data_call('size', e[1])
    local t0 = now()
    local r = data_call('read_text', e[1])
    local t1 = now()
    local s
    if r[1] and type(r[2]) == 'string' then
      local tail_ok = (r[2]:sub(-64) == line_of(e[2] // 64))
      s = '#' .. #r[2] .. ' tail ' .. (tail_ok and 'ok' or 'WRONG')
    else
      s = results(r)
    end
    r = nil
    gccollect()
    add(out, e[1] .. ' size ' .. brief(z) .. ' read_text ' .. s .. ' ' .. (t1 - t0) .. ' ms')
  end
  add(out, stream('lad1m.txt', 16384))
  add(out, stream('lad4m.txt', 65536))
  add(out, 'gc after: ' .. val(gcc()))
  LD.lines = out
end
page('LADDER', {
  draw = function()
    if LD.pending then LD.pending = false; local ok, e = pcall(ladder_run); if not ok then LD.lines = { 'ladder ERR ' .. tostring(e) } end end
    draw_lines(LD.lines or { 'tap "run" (reads 64 KB .. 4 MB with read_text)' }, TOP, 630)
  end,
  buttons = function()
    return grid({ { 'run ladder', function() LD.pending = true end },
                  { 'redraw', function() end } }, 2, 640, 70, 10)
  end,
})

------------------------------------------------------------------ callbacks
function on_load(c, ...)
  LIFE[#LIFE + 1] = 'load@' .. now() .. '(' .. argsig(c, ...) .. ')'
  ST.load_t = now()
  ST.type = type(ctx.state)
  ST.mt = type(getmetatable(ctx.state))
  ST.at_load = state_snapshot()
  if type(ctx.state) == 'table' then ST.load_note = ctx.state.long_note end
end

function on_enter(c, ...)
  LIFE[#LIFE + 1] = 'enter@' .. now()
  if #ST.at_load == 0 then ST.at_load = state_snapshot() end
end

function on_leave(c, ...)
  pcall(function() ctx.state.leave_seen = now() end)       -- no save: does the host save on leave?
end

function on_unload(c, ...)
  pcall(function() ctx.state.unload_seen = now() end)
end

function on_input(c, ev, ...)
  local t = now()
  last_input_ms = t
  pcall(log_event, ev, t, select('#', ...))
  local p = PAGES[cur]
  local redraw = (p.name == 'INPUT')
  if type(ev) == 'table' and ev.type == 'touch' and type(ev.x) == 'number' and type(ev.y) == 'number'
     and (ev.gesture == nil or ev.gesture == 'tap') then
    local x, y = ev.x, ev.y
    redraw = true
    if y < 56 then
      cur = 1
    elseif y >= BOT then
      cur = cur % #PAGES + 1
    else
      for _, b in ipairs(buttons_of(p)) do
        if x >= b.x and x < b.x + b.w and y >= b.y and y < b.y + b.h then
          local ok, r = pcall(b.fn, t)
          if not ok then last_error = 'box "' .. tostring(b.label) .. '": ' .. tostring(r)
          elseif r == 'noinv' then redraw = false end
          break
        end
      end
    end
  end
  if redraw then pcall(ctx.invalidate) end
  return true
end

function on_draw(c, gg)
  local t0 = now()
  draws = draws + 1
  g:clear(0)
  local p = PAGES[cur]
  -- header and footer first: a frame keeps only its first 1024 commands (see CAP)
  g:text(PX, 4, cur .. '/' .. #PAGES .. ' ' .. p.name .. '   draw #' .. draws .. ' (prev took ' ..
                tostring(last_draw_ms or '-') .. ' ms)')
  g:text(PX, 28, 'input->draw ' .. (last_input_ms and tostring(t0 - last_input_ms) or '-') ..
                 ' ms   draw at t=' .. t0)
  g:line(0, 54, 479, 54)
  g:line(0, BOT, 479, BOT)
  g:text(PX, BOT + 22, 'NEXT PAGE: tap this band.   INDEX: top band')
  local ok, e = pcall(function()
    if p.draw then p.draw() end
    if p.lines then draw_lines(p.lines(), p.ytop or TOP, p.ymax or (BOT - 4)) end
  end)
  if not ok then
    local ls = {}
    add(ls, 'PAGE ERROR: ' .. tostring(e))
    draw_lines(ls, 420, BOT - 4)
    last_error = tostring(e)
  end
  for _, b in ipairs(buttons_of(p)) do
    if b.label ~= '' then box(b.x, b.y, b.w, b.h, b.label) end
  end
  last_draw_ms = now() - t0
end

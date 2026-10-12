-- tools/gamebook_engine.lua: the gamebook engine. tools/gamebook.py writes the STORY and ART tables
-- and appends this file to make an app's index.lua; it runs on the stock firmware's Lua host
-- (docs/lua-apps.md). What it leans on, all confirmed on 7.6.8 in the emulator (2026-10-12 probe):
--   * a frame keeps its first 1024 drawing commands, a layer has no cap: plates are painted once
--     into a layer (L:draw_with) and blitted with g:layer, one command; text never goes into a layer
--     (it does not appear there);
--   * input: taps and swipe_left/right from the panel, key up/down from the two side buttons;
--   * ctx.invalidate() is a fast refresh, ctx.display:full_refresh() a full one (used on a new
--     passage, so a plate never ghosts the next);
--   * ctx.state may be persisted by the host when the app is left (ctx.save() is a stub); only flat
--     strings and numbers go into it.

local W = 480
local MX, TW = 28, 424            -- the text column (tools/gamebook.py wraps to these widths)
local PITCH, PARA = 30, 12        -- line pitch; extra space between paragraphs
local TOP, BOTTOM = 40, 740       -- line-box tops on a page without a plate
local PLATE_H = 300               -- plates are 480 x 300 at the top of their page
local FOOT_Y = 758

local ADV = {}
for w, chars in pairs({[4] = "'", [5] = '.:Iil|', [6] = ' ,;`j', [7] = '()[]{}', [8] = '"\\frt',
                       [9] = '*-/1^', [10] = '?J_sz', [11] = '7ackvxy~', [12] = '!$23459EFLbdeghnopqu',
                       [13] = '+068<=>PRSTZ', [14] = 'ABCKVXY', [15] = '#&DGHNU', [16] = 'OQ',
                       [17] = '%@w', [18] = 'Mm', [20] = 'W'}) do
  for k = 1, #chars do ADV[chars:sub(k, k)] = w end
end
local function textw(s)
  local w = 0
  for k = 1, #s do w = w + (ADV[s:sub(k, k)] or 12) end
  return w
end

local st                    -- progress: passage, page, flags (",a,b,"), found (",1,3,")
local pages, pageno, current
local layers = {}           -- art name -> its painted layer

-- ---------------------------------------------------------------- state

local function has(list, item) return list:find(',' .. item .. ',', 1, true) ~= nil end
local function flag_on(f) return has(st.flags, f) end
local function count_found()
  local n = 0
  for _ in st.found:gmatch('%d+') do n = n + 1 end
  return n
end

-- ---------------------------------------------------------------- art

local function paint(lg, a)
  local d, s = a.d, a.s
  local i, n, y, x, gap = 1, #d, 0, 0, nil
  while i <= n do
    local c = d:byte(i)
    if c == 33 then                      -- '!' ends a logical row
      y, x, i = y + 1, 0, i + 1
    else
      local v
      if c == 126 then                   -- '~' + two digits, base 91
        v = (d:byte(i + 1) - 35) * 91 + (d:byte(i + 2) - 35)
        i = i + 3
      else
        v = c - 35
        i = i + 1
      end
      if gap == nil then
        gap = v
      else
        x = x + gap
        local x0, x1, yy = x * s, (x + v) * s - 1, y * s
        for k = 0, s - 1 do lg:line(x0, yy + k, x1, yy + k) end
        x, gap = x + v, nil
      end
    end
  end
end

local function layer_for(name)
  local L = layers[name]
  if not L then
    local a = ART[name]
    L = ctx.layers:create(a.w * a.s, a.h * a.s)
    L:draw_with(function(lg) paint(lg, a) end)
    layers[name] = L
  end
  return L
end

local function keep_layers(keep)
  for name, L in pairs(layers) do
    if not keep[name] then
      pcall(L.dispose, L)
      layers[name] = nil
    end
  end
end

-- ---------------------------------------------------------------- layout

local function visible_choices(p)
  local out = {}
  for _, c in ipairs(p.choices) do
    if not c.flag or flag_on(c.flag) ~= (c.neg == true) then out[#out + 1] = c end
  end
  return out
end

-- A page is {ops = {...}, hits = {...}}: draw operations and choice boxes, laid out once per passage.
local function new_layout()
  local L = {pages = {}, y = 0}
  function L.page(plate, ornament)
    L.cur = {ops = {}, hits = {}}
    L.pages[#L.pages + 1] = L.cur
    if plate then
      L.op({'art', plate, 0, 0})
      L.op({'line', 0, PLATE_H, W - 1, PLATE_H})
      L.y = PLATE_H + 20
    elseif ornament and ART._orn then
      L.op({'art', '_orn', 0, TOP - 12})
      L.y = TOP - 12 + ART._orn.h * ART._orn.s + 18
    else
      L.y = TOP
    end
  end
  function L.op(o) L.cur.ops[#L.cur.ops + 1] = o end
  function L.text(s, centred)
    if L.y + 24 > BOTTOM then L.page() end
    L.op({'text', centred and (W - textw(s)) // 2 or MX, L.y, s})
    L.y = L.y + PITCH
  end
  -- Choices go under the text; returns true when they had to start a page of their own. A page
  -- that turns out to be the passage's only one has no footer, so it may use the footer's room.
  function L.choices(list)
    local need = 16
    for _, c in ipairs(list) do need = need + #c.lines * PITCH + 16 + 10 end
    local limit = (#L.pages == 1) and 792 or (FOOT_Y - 6)
    local broke = false
    if L.y + need > limit then
      L.page()
      broke = true
    end
    L.y = L.y + 16
    for _, c in ipairs(list) do
      local h = #c.lines * PITCH + 16
      L.op({'box', MX, L.y, TW, h})
      L.op({'arrow', MX + 10, L.y + h // 2})
      for k, s in ipairs(c.lines) do L.op({'text', MX + 26, L.y + 11 + (k - 1) * PITCH, s}) end
      L.cur.hits[#L.cur.hits + 1] = {MX, L.y, TW, h, c.to}
      L.y = L.y + h + 10
    end
    return broke
  end
  return L
end

-- `brk`: the paragraph that starts a new page, so the last paragraph travels with the choices
-- instead of leaving them alone on a page.
local function layout_passage(p, brk)
  local L = new_layout()
  L.page(p.img, true)
  local para, last_start = 1, 1
  for _, s in ipairs(p.lines) do
    if s == '' then
      para = para + 1
      if para == brk then L.page() else L.y = L.y + PARA end
      last_start = #L.pages
    else
      L.text(s)
    end
  end
  if p.ending then
    -- the ending's own page, its block centred: ornament, title, epigraph, the tally, the way back
    L.page()
    local a = ART['_end' .. p.ending]
    local orn = ART._orn and ART._orn.h * ART._orn.s or 0
    local h = orn + 28 + a.h + 20 + (#p.epigraph + 1) * PITCH + 16 + #p.choices * (PITCH + 26)
    L.y = math.max(TOP, (800 - h) // 2)
    if ART._orn then
      L.op({'art', '_orn', 0, L.y})
      L.y = L.y + orn + 28
    end
    L.op({'art', '_end' .. p.ending, 0, L.y})
    L.y = L.y + a.h + 20
    for _, s in ipairs(p.epigraph) do L.text(s, true) end
    L.text(string.format('Ending %d of %d  -  %d found so far', p.ending, STORY.endings, count_found()), true)
    L.choices(visible_choices(p))
    return L.pages
  end
  -- choices alone on a page, or the last paragraph split across the break: move that paragraph
  local split = #L.pages > last_start
  if (L.choices(visible_choices(p)) or split) and not brk and para > 1 then
    return layout_passage(p, para)
  end
  return L.pages
end

local function layout_title()
  local L = new_layout()
  L.page(STORY.passages[STORY.start].img)
  L.y = PLATE_H + 30
  L.op({'art', '_title', 0, L.y})
  L.y = L.y + ART._title.h + 24
  local list = {}
  if st.passage and STORY.passages[st.passage] then list[#list + 1] = {lines = {'Continue'}, to = '_continue'} end
  list[#list + 1] = {lines = {(#list > 0) and 'Begin again' or 'Begin'}, to = '_begin'}
  L.choices(list)
  if count_found() > 0 then
    L.y = BOTTOM - PITCH
    L.text(string.format('Endings found: %d of %d', count_found(), STORY.endings), true)
  end
  return L.pages
end

-- ---------------------------------------------------------------- navigation

local function show(pid, pg)
  current = pid
  pages = (pid == '_title') and layout_title() or layout_passage(STORY.passages[pid])
  pageno = math.max(1, math.min(pg or 1, #pages))
  local keep = {}
  for _, page in ipairs(pages) do
    for _, o in ipairs(page.ops) do
      if o[1] == 'art' then keep[o[2]] = true end
    end
  end
  keep_layers(keep)
end

local function refresh(full)
  ctx.invalidate()
  if full then pcall(function() ctx.display:full_refresh() end) end
end

local function go(pid)
  local p = STORY.passages[pid]
  if pid == STORY.start then st.flags = ',' end
  for _, f in ipairs(p.set or {}) do
    if not flag_on(f) then st.flags = st.flags .. f .. ',' end
  end
  if p.ending and not has(st.found, tostring(p.ending)) then
    st.found = st.found .. p.ending .. ','
  end
  st.passage, st.page = pid, 1
  show(pid, 1)
  refresh(true)
end

local function turn(d)
  local n = pageno + d
  if n < 1 or n > #pages then return end
  pageno = n
  if current ~= '_title' then st.page = n end
  refresh(false)
end

local function choose(to)
  if to == '_begin' then
    go(STORY.start)
  elseif to == '_continue' then
    show(st.passage, st.page)
    refresh(true)
  else
    go(to)
  end
end

local function init(c)
  st = (c and c.state) or ctx.state or {}
  if type(st.flags) ~= 'string' then st.flags = ',' end
  if type(st.found) ~= 'string' then st.found = ',' end
  pcall(function() ctx:set_tick_rate('idle') end)
  show('_title')
end

-- ---------------------------------------------------------------- host callbacks

function on_load(c) init(c) end

function on_enter(c)
  if not st then init(c) else show('_title') end
  ctx.invalidate()
end

function on_draw(c, gg)
  if not pages then init(c) end
  g:clear(0)
  for _, o in ipairs(pages[pageno].ops) do
    local k = o[1]
    if k == 'text' then
      g:text(o[2], o[3], o[4])
    elseif k == 'art' then
      g:layer(layer_for(o[2]), o[3], o[4])
    elseif k == 'line' then
      g:line(o[2], o[3], o[4], o[5])
    elseif k == 'box' then
      g:rect(o[2], o[3], o[4], o[5])
    elseif k == 'arrow' then
      for i = 0, 5 do g:line(o[2] + i, o[3] - 5 + i, o[2] + i, o[3] + 5 - i) end
    end
  end
  if #pages > 1 then
    local s = pageno .. ' / ' .. #pages
    g:text(W - MX - textw(s), FOOT_Y, s)
  end
end

function on_input(c, ev)
  if ev.type == 'key' then
    if ev.key == 'down' then turn(1) elseif ev.key == 'up' then turn(-1) end
    return true
  end
  if ev.type ~= 'touch' then return false end
  if ev.gesture == 'tap' then
    for _, h in ipairs(pages[pageno].hits) do
      if ev.x >= h[1] and ev.x < h[1] + h[3] and ev.y >= h[2] and ev.y < h[2] + h[4] then
        choose(h[5])
        return true
      end
    end
    if ev.x < W // 3 then turn(-1) else turn(1) end
    return true
  elseif ev.gesture == 'swipe_left' then
    turn(1)
    return true
  elseif ev.gesture == 'swipe_right' then
    turn(-1)
    return true
  end
  return false
end

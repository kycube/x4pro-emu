-- Hello Lua -- the app tests/test_lua_apps.py runs on the stock xteink_app 7.2.4.
--
-- Deliberately boring: everything it paints is constant, so the screen is a stable golden
-- (tests/golden/stock-lua-hello.png); the only moving part is the tap counter, and the test
-- screenshots before it taps. The API is documented in docs/lua-apps.md, and `tools/xtapp.py new`
-- writes a fuller template.
--
-- `g:clear(0)` blanks the whole frame to white, status bar and footer included, so the app owns
-- every pixel; a bare `g:clear()` paints it black instead, and no clear at all leaves the page the
-- app was started from underneath (session 8). `ctx.log.info` is called anyway, to keep proving
-- that 7.2.4 accepts it and prints nothing anywhere (see the test's console assertion). There is
-- no `print` on this host.

local taps = 0

local function log(msg)
  if type(ctx) == 'table' and type(ctx.log) == 'table' and ctx.log.info then
    pcall(ctx.log.info, 'hello-lua: ' .. msg)
  end
end

function on_load()
  log('on_load')
end

function on_enter()
  log('on_enter')
end

function on_input(ev)
  taps = taps + 1
  log('on_input ' .. type(ev))
  if type(ctx) == 'table' and type(ctx.invalidate) == 'function' then
    pcall(ctx.invalidate)          -- mark dirty; on_draw follows
  end
  return true                      -- claim the event
end

function on_draw()
  g:clear(0)                       -- 0 = white; g:clear() alone paints the frame black
  g:rect(24, 64, 432, 132)
  g:text(48, 116, 'Hello Lua')
  g:text(48, 158, 'a stock screen written in Lua')
  g:line(24, 236, 456, 236)
  g:circle(240, 392, 104)
  g:text(48, 596, 'taps: ' .. taps)
end

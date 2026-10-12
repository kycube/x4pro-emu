-- probe-io: the module `require('mod')` should find next to index.lua. It counts its own loads in a
-- global, so a second require that returns the cached table leaves the count at 1, and it reports
-- the two values a Lua 5.4+ `require` passes to a loaded chunk (the name and the loader data,
-- usually the file path), its own chunk name (read off an error message), and whether it sees the
-- app's globals.
PROBE_MOD_LOADS = (PROBE_MOD_LOADS or 0) + 1
local a1, a2 = ...
local _, where = pcall(function() error('here') end)
return { name = 'mod', loads = PROBE_MOD_LOADS, arg1 = tostring(a1), arg2 = tostring(a2),
         chunk = tostring(where), sees_ctx = type(ctx), sees_g = type(g) }

-- probe-io: `require('lib.mod')` (a module in a subdirectory).
PROBE_LIBMOD_LOADS = (PROBE_LIBMOD_LOADS or 0) + 1
local a1, a2 = ...
local _, where = pcall(function() error('here') end)
return { name = 'lib/mod', loads = PROBE_LIBMOD_LOADS, arg1 = tostring(a1), arg2 = tostring(a2),
         chunk = tostring(where) }

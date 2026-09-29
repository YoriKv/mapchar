-- mapchar capture: the probe server, run headless. It replays a capture once
-- from a ring state, recording the reference — the writes to one observed
-- range (CFG.obs, from frame value CFG.obsFrom to CFG.obsTo) — and a
-- savestate every CFG.spacing frames but in the moment's gap (CFG.gap); then
-- it answers probes: load the latest state before a frame, change ROM bytes
-- (or what a read returns), run, undo the changes, and report how the
-- observed writes differ from the reference. Savestates do not hold ROM,
-- which is why the writes are undone.
--
--   <- probe <id> <state> <first|full> <change>,...
--        a change is <addr>:<value> (a ROM byte), or
--        r:<memory>:<addr>:<value>[:<pc>[:<n>[:<w>]]] (what reads of that
--        byte return; only the reads by that PC, only the n-th from the
--        state, only between the state's w-th write to the byte and the next)
--   -> res <id> same [v=n,lo,hi d=a:v,...] [s=frame] [r=...] [w=0]
--   -> res <id> diff <position> <remaining reference> <values written> [n=count]
--        [v=... d=...] [s=frame] [r=...] [w=0]
--   <- settle <state> <lo> <hi>     -> settled <frame>
--   <- vobs <state> <frame>         -> vobs <frame>
--   <- vwin <lo> <hi> <stable> <min> <max>   -> vwin <lo> <hi>
--   <- rfrom <ROM offset>          -> rfrom <ROM offset>
--   <- shot <state> <frame> <file name> <change>,...   -> shot <frame>
--   <- quit
--
-- Values written are sent up to the remaining reference and 64 more; n= says
-- how many there were when that cut them. w=0 says a ROM byte did not take
-- its new value.
--
-- CFG.robs = { lo, hi, pc, from, cpu, n } also reports the reader's first ROM
-- reads in lo..hi from frame value `from` (all readers when pc is nil),
-- answering as soon as n are in; each read as bus/ROM offset when the
-- emulator converts it. After rfrom, the reads are reported from the
-- reader's first read of that ROM offset on. CFG.vobs = { memory type, frame } also reports how
-- that memory differs from the reference at that frame. With a window (vwin)
-- the VRAM is compared by event instead: from the first read of a changed
-- byte, once the window has not changed for <stable> frames and at least
-- <min> have passed (at most <max>), only within the window; s= is that frame.

local inputs = {}
for p, k in slurp(CFG.input):gmatch("(%d+) ([^\n]*)") do inputs[tonumber(p)] = decInput(k) end
connect()

local frame, polls = 0, 0
local phase = "boot"            -- boot, reference, idle, arming, probe
local states = {}               -- { frame, poll, data }
local ref = {}                  -- { frame, addr, value }
local lastRefFrame = 0
local got, probe = nil, nil
local obsFrom, obsTo = CFG.obsFrom or 0, CFG.obsTo or 1e12
local pend = CFG.pend or 0
local vobs = CFG.vobs
local vref, vdiff = nil, nil
local vwin = nil                -- { lo, hi, stable, min, max }
local rfrom = nil               -- the ROM offset the reader's reads count from
local stale = {}                -- callbacks to remove at the next frame end

-- Every processor of the profile: its readers, the main CPU, the writers.
local CPUS = {}
do
  local seen = {}
  local function add(c) if c and not seen[c] then seen[c] = true; CPUS[#CPUS + 1] = c end end
  for _, r in ipairs(CFG.readers) do add(r) end
  add(CFG.cpu)
  for _, h in ipairs(CFG.writes or {}) do add(h[1]) end
end

-- A memory callback kept to be removed once the probe is over; removed at a
-- frame end, never inside a memory callback of the same kind.
local function addTemp(list, fn, kind, lo, hi, cpu, mt)
  local id = emu.addMemoryCallback(fn, kind, lo, hi, cpu, mt)
  list[#list + 1] = { id, kind, lo, hi, cpu, mt }
end

local function removeStale()
  for _, c in ipairs(stale) do emu.removeMemoryCallback(c[1], c[2], c[3], c[4], c[5], c[6]) end
  stale = {}
end

local function undo()
  for _, w in ipairs(probe.undo) do emu.write(w[1], w[2], ROM) end
  for _, c in ipairs(probe.temp) do stale[#stale + 1] = c end
  probe.temp = {}
end

local function tail()
  return (vdiff or "") .. (probe.settled and string.format(" s=%d", probe.settled) or "")
    .. probe.rtext() .. (probe.fixed and " w=0" or "")
end

local function finish(kind, pos)
  undo()
  if kind == "same" then
    send(string.format("res %d same%s", probe.id, tail()))
  else
    local left = #ref - probe.k0 + 1
    local vals = {}
    for i = 1, math.min(#got, left + 64) do vals[i] = string.format("%02X", got[i][2]) end
    local count = #got > #vals and string.format(" n=%d", #got) or ""
    send(string.format("res %d diff %d %d %s%s%s", probe.id, pos, left,
      table.concat(vals, "."), count, tail()))
  end
  probe, got, phase, vdiff = nil, nil, "idle", nil
end

-- The observed range's writes, through the profile's write hooks that land
-- in its memory: at the memory's own offsets, or on the writer's bus.
-- CFG.bulk = { { page, from, to }, ... }: the pages of the observed range
-- whose writes the replay dropped from frame `from` to before `to` (nil: to
-- the end), dropped here too.
local function dropped(a)
  if not CFG.bulk then return false end
  local page = a >> 8
  for _, b in ipairs(CFG.bulk) do
    if b[1] == page and frame >= b[2] and (not b[3] or frame < b[3]) then return true end
  end
  return false
end

if CFG.obs then
  local function onWrite(a, v)
    if frame < obsFrom or frame > obsTo or dropped(a) then return end
    if phase == "reference" then
      ref[#ref + 1] = { frame, a, v }; lastRefFrame = frame
    elseif phase == "probe" and probe.mode ~= "shot" then
      got[#got + 1] = { a, v }
      local r = ref[probe.k0 + #got - 1]
      if probe.mode == "first" and (not r or r[2] ~= a or r[3] ~= v) then finish("diff", #got) end
    end
  end
  local mt, lo, hi = ramType(CFG.obs[1]), CFG.obs[2], CFG.obs[3]
  local done, n = {}, 0
  for _, h in ipairs(writeHooks()) do
    if h.ram == CFG.obs[1] then
      local cpu = emu.cpuType[h.cpu]
      if h.byBus then
        emu.addMemoryCallback(onWrite, emu.callbackType.write, h.lo + lo, h.lo + hi, cpu, h.mt); n = n + 1
      elseif not done[h.cpu] then
        done[h.cpu] = true
        emu.addMemoryCallback(onWrite, emu.callbackType.write, lo, hi, cpu, mt); n = n + 1
      end
    end
  end
  if n == 0 then emu.addMemoryCallback(onWrite, emu.callbackType.write, lo, hi, CPU, mt) end
end

local robs = CFG.robs
if robs then
  local rcpu = robs[5]
  local pcOf = PCS[rcpu]
  emu.addMemoryCallback(function(a)
    if phase == "probe" and (probe.mode == "first" or probe.mode == "full")
        and #probe.reads < 64 and frame >= (robs[4] or 0) then
      if robs[3] and pcOf() ~= robs[3] then return end
      local off = romOffset(rcpu, a)
      if rfrom and not probe.counting then
        if (off or a) ~= rfrom then return end
        probe.counting = true
      end
      probe.reads[#probe.reads + 1] = off and string.format("%X/%X", a, off) or string.format("%X", a)
      -- Answer as soon as n reads are in: a change that later stalls the
      -- game still reports where the reader went.
      if robs[6] and #probe.reads >= robs[6] then finish("same") end
    end
  end, emu.callbackType.read, robs[1], robs[2], emu.cpuType[rcpu], ROM)
end

-- The watched memory (lo..hi, or all of it) as words, four bytes a read: a
-- snapshot of a whole VRAM in one callback must stay well inside its second.
local vsize = vobs and emu.getMemorySize(MEM(vobs[1])) or 0
local function vsnap(lo, hi)
  local mt, t = MEM(vobs[1]), {}
  if not lo then lo, hi = 0, vsize - 1 end
  for a = lo & ~3, hi, 4 do t[a] = emu.read32(a, mt, false) end
  return t
end

-- How the watched memory differs from the reference: " v=n,lo,hi d=a:v,...",
-- within the window when there is one.
local function vdiffOf(t)
  local lo, hi = 0, vsize - 1
  if vwin then lo, hi = vwin[1], vwin[2] end
  local d, n, dlo, dhi = {}, 0, -1, -1
  for a = lo & ~3, hi, 4 do
    local w, r = t[a], vref[a]
    if w ~= nil and w ~= r then
      for k = 0, 3 do
        local i, x = a + k, (w >> (8 * k)) & 0xFF
        if i >= lo and i <= hi and i < vsize and x ~= ((r or 0) >> (8 * k)) & 0xFF then
          n = n + 1
          if dlo < 0 then dlo = i end
          dhi = i
          if #d < 1024 then d[#d + 1] = string.format("%X:%X", i, x) end
        end
      end
    end
  end
  return string.format(" v=%d,%d,%d d=%s", n, dlo, dhi, table.concat(d, ","))
end

oneShot(function()
  local data = slurp(CFG.statePath)
  emu.loadSavestate(data)
  frame, polls, phase = CFG.stateFrame, CFG.statePoll, "reference"
  states[1] = { frame = CFG.stateFrame, poll = CFG.statePoll, data = data }
end)

emu.addEventCallback(function()
  if phase == "reference" or phase == "probe" then
    polls = polls + 1
    setInputs(inputs[polls])
  end
end, emu.eventType.inputPolled)

-- What reads of one byte return, for this probe: the substitute on the
-- reads by that PC, or the n-th of them, and nothing else.
local function substitute(p, sub)
  local mt = ramType(sub.mem)
  if sub.after then
    -- Armed from the after-th write to the byte until the next.
    sub.writes, sub.armed = 0, false
    for _, cpuName in ipairs(CPUS) do
      addTemp(p.temp, function()
        if probe ~= p then return end
        sub.writes = sub.writes + 1
        sub.armed = sub.writes == sub.after
      end, emu.callbackType.write, sub.addr, sub.addr, emu.cpuType[cpuName], mt)
    end
  end
  local value = sub.value & 0xFF
  for _, cpuName in ipairs(CPUS) do
    local pcOf = PCS[cpuName]
    -- On the GBA a halfword or word access arrives at its aligned address:
    -- every access from the byte's word start up to the byte counts, as the
    -- tracer counts the replay's reads (trace.py, Tracer._only_read).
    local lo = CFG.console == "gba" and (sub.addr & ~3) or sub.addr
    addTemp(p.temp, function(a, v)
      if probe ~= p or phase ~= "probe" then return end
      if sub.after and not sub.armed then return end
      if sub.pc and pcOf() ~= sub.pc then return end
      sub.n = sub.n + 1
      if sub.nth and sub.n ~= sub.nth then return end
      if CFG.console == "gba" then
        -- The byte's lane within the access.
        local shift = ((sub.addr & 3) - (a & 3)) * 8
        return (v & ~(0xFF << shift)) | (value << shift)
      end
      return value
    end, emu.callbackType.read, lo, sub.addr, emu.cpuType[cpuName], mt)
  end
end

local function startProbe(p)
  oneShot(function()
    local s = states[p.si]
    emu.loadSavestate(s.data)
    frame, polls = s.frame, s.poll
    p.undo, p.temp = {}, {}
    for _, w in ipairs(p.writes) do
      p.undo[#p.undo + 1] = { w[1], emu.read(w[1], ROM, false) }
      emu.write(w[1], w[2], ROM)
      if emu.read(w[1], ROM, false) ~= w[2] then p.fixed = true end
    end
    for _, sub in ipairs(p.subs) do substitute(p, sub) end
    if vwin and vobs then
      -- The event the comparison waits for: a changed byte read.
      local function trig() if probe == p and not p.trig then p.trig = frame end end
      for _, w in ipairs(p.writes) do
        for _, r in ipairs(CFG.readers) do
          addTemp(p.temp, trig, emu.callbackType.read, w[1], w[1], emu.cpuType[r], ROM)
        end
      end
      for _, sub in ipairs(p.subs) do
        for _, cpuName in ipairs(CPUS) do
          addTemp(p.temp, trig, emu.callbackType.read, sub.addr, sub.addr, emu.cpuType[cpuName], ramType(sub.mem))
        end
      end
    end
    p.k0 = 1
    while ref[p.k0] and ref[p.k0][1] < s.frame do p.k0 = p.k0 + 1 end
    p.reads = {}
    p.rtext = function()
      if not robs then return "" end
      return " r=" .. table.concat(p.reads, ",")
    end
    probe, got, phase = p, {}, "probe"
  end)
end

-- The changes of a probe: ROM bytes, and substitutes for reads.
local function parseChanges(ws)
  local writes, subs = {}, {}
  for item in (ws or ""):gmatch("[^,%s]+") do
    if item:sub(1, 2) == "r:" then
      local f = {}
      for x in (item:sub(3) .. ":"):gmatch("([^:]*):") do f[#f + 1] = x end
      subs[#subs + 1] = {
        mem = f[1], addr = tonumber(f[2], 16), value = tonumber(f[3], 16),
        pc = (f[4] and f[4] ~= "") and tonumber(f[4], 16) or nil,
        nth = (f[5] and f[5] ~= "") and tonumber(f[5]) or nil,
        after = (f[6] and f[6] ~= "") and tonumber(f[6]) or nil, n = 0,
      }
    else
      local a, v = item:match("^(%x+):(%x+)$")
      if a then writes[#writes + 1] = { tonumber(a, 16), tonumber(v, 16) } end
    end
  end
  return writes, subs
end

local function serve()
  -- Idle: wait a little for a command rather than spin. The wait counts
  -- against the callback's one second, so it stays short.
  local wait = 0.05
  while true do
    local line, err = receive(wait)
    wait = nil
    if not line then
      if err == "closed" then emu.stop(0) end
      return
    end
    local cmd, rest = line:match("^(%S+)%s*(.*)$")
    if cmd == "quit" then emu.stop(0); return end
    local p
    if cmd == "settle" then
      -- The frame after which vobs memory lo..hi stays as at the vobs frame.
      local si, lo, hi = rest:match("^(%d+) (%d+) (%d+)$")
      p = { id = 0, si = tonumber(si), mode = "settle", writes = {}, subs = {}, lo = tonumber(lo), hi = tonumber(hi), last = 0 }
    elseif cmd == "vobs" then
      -- Move the reference snapshot of the vobs memory to this frame.
      local si, at = rest:match("^(%d+) (%d+)$")
      p = { id = 0, si = tonumber(si), mode = "vobs", writes = {}, subs = {}, at = tonumber(at) }
    elseif cmd == "vwin" then
      local lo, hi, st, mn, mx = rest:match("^(%d+) (%d+) (%d+) (%d+) (%d+)$")
      vwin = { tonumber(lo), tonumber(hi), tonumber(st), tonumber(mn), tonumber(mx) }
      send(string.format("vwin %d %d", vwin[1], vwin[2]))
    elseif cmd == "rfrom" then
      rfrom = tonumber(rest, 16)
      send(string.format("rfrom %X", rfrom))
    elseif cmd == "shot" then
      local si, at, path, ws = rest:match("^(%d+) (%d+) (%S+) ?(.*)$")
      local writes, subs = parseChanges(ws)
      p = { id = 0, si = tonumber(si), mode = "shot", at = tonumber(at), path = path, writes = writes, subs = subs }
    elseif cmd == "probe" then
      local id, si, mode, ws = rest:match("^(%d+) (%d+) (%S+) ?(.*)$")
      local writes, subs = parseChanges(ws)
      p = { id = tonumber(id), si = tonumber(si), mode = mode, writes = writes, subs = subs }
    else
      send("err unknown command " .. tostring(cmd))
    end
    if p then
      phase = "arming"
      startProbe(p)
      return
    end
  end
end

local function sameWindow(a, b)
  for i = vwin[1] & ~3, vwin[2], 4 do if a[i] ~= b[i] then return false end end
  return true
end

emu.addEventCallback(function()
  if phase == "boot" then return end
  removeStale()
  frame = frame + 1
  if phase == "probe" and probe.mode == "settle" then
    local t = vsnap(probe.lo, probe.hi)
    for a = probe.lo & ~3, probe.hi, 4 do
      if t[a] ~= vref[a] then probe.last = frame; break end
    end
    if frame >= vobs[2] then
      undo()
      send(string.format("settled %d", probe.last + 1))
      probe, got, phase = nil, nil, "idle"
    end
  elseif phase == "probe" and probe.mode == "vobs" then
    if frame == probe.at then
      vref = vsnap(); vobs[2] = probe.at; pend = probe.at
      undo()
      send(string.format("vobs %d", probe.at))
      probe, got, phase = nil, nil, "idle"
    end
  elseif phase == "probe" and probe.mode == "shot" then
    if frame >= probe.at then
      save(CFG.dir .. "/" .. probe.path, emu.takeScreenshot())
      undo()
      send(string.format("shot %d", frame))
      probe, got, phase = nil, nil, "idle"
    end
  elseif phase == "probe" and vwin and probe.trig then
    -- Compared by event: once the window holds still.
    local t = vsnap(vwin[1], vwin[2])
    if probe.prev and sameWindow(t, probe.prev) then
      probe.still = probe.still + 1
    else
      probe.still = 0
    end
    probe.prev = t
    if (probe.still >= vwin[3] and frame >= probe.trig + vwin[4]) or frame >= probe.trig + vwin[5] then
      vdiff = vdiffOf(t)
      probe.settled = frame
    end
  elseif vobs and frame == vobs[2] then
    if phase == "reference" then
      vref = vsnap()
    elseif phase == "probe" then
      vdiff = vdiffOf(vsnap())
    end
  end
  if phase == "reference" then
    if frame % CFG.spacing == 0 and not inGap(frame) then
      local f, p = frame, polls
      oneShot(function() states[#states + 1] = { frame = f, poll = p, data = emu.createSavestate() } end)
    end
    if frame >= CFG.frame then
      phase = "idle"
      local fs = {}
      for i, s in ipairs(states) do fs[i] = tostring(s.frame) end
      send(string.format("ref %d %d %s", #ref, lastRefFrame, table.concat(fs, ",")))
      local vals = {}
      for i, r in ipairs(ref) do vals[i] = string.format("%d:%X:%02X", r[1], r[2], r[3]) end
      send("refw " .. table.concat(vals, " "))
    end
  elseif phase == "probe" and probe.mode ~= "shot" then
    local byEvent = vwin and vobs and probe.trig
    local over = byEvent and probe.settled
      or (not byEvent and frame > math.max(lastRefFrame, pend, vobs and vobs[2] or 0))
    if over then
      local same = #got == #ref - probe.k0 + 1
      local pos = 0
      for i = 1, #got do
        local r = ref[probe.k0 + i - 1]
        if not r or r[2] ~= got[i][1] or r[3] ~= got[i][2] then same = false; pos = i; break end
      end
      if same then finish("same") else finish("diff", pos == 0 and #got + 1 or pos) end
    end
  end
  if phase == "idle" then serve() end
end, emu.eventType.endFrame)

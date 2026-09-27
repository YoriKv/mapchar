-- mapchar capture: the probe server, run headless. It replays a capture once
-- from a ring state, recording the reference — the writes to one observed
-- range (CFG.obs, from frame value CFG.obsFrom to CFG.obsTo) — and a
-- savestate every CFG.spacing frames but in the moment's gap (CFG.gap); then
-- it answers probes: load the latest state before a frame, write ROM bytes,
-- run, undo the writes, and report how the observed writes differ from the
-- reference. Savestates do not hold ROM, which is why the writes are undone.
--
--   <- probe <id> <state> <first|full> <addr>:<value>,...
--   -> res <id> same [v=…] [r=…]
--   -> res <id> diff <position> <remaining reference> <values written> [v=…] [r=…]
--   <- settle <state> <lo> <hi>     -> settled <frame>
--   <- vobs <state> <frame>         -> vobs <frame>
--   <- shot <state> <frame> <file name> <addr>:<value>,...   -> shot <frame>
--   <- quit
--
-- CFG.robs = { lo, hi, pc, from, cpu, n } also reports the reader's first ROM
-- reads in lo..hi from frame value `from` (all readers when pc is nil),
-- answering as soon as n are in. CFG.vobs = { memory type, frame } also
-- reports how that memory differs from the reference at that frame.

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

local function undo()
  for _, w in ipairs(probe.undo) do emu.write(w[1], w[2], ROM) end
end

local function finish(kind, pos)
  undo()
  if kind == "same" then
    send(string.format("res %d same%s%s", probe.id, vdiff or "", probe.rtext()))
  else
    local vals = {}
    for i = 1, math.min(#got, 4096) do vals[i] = string.format("%02X", got[i][2]) end
    send(string.format("res %d diff %d %d %s%s%s", probe.id, pos, #ref - probe.k0 + 1,
      table.concat(vals, "."), vdiff or "", probe.rtext()))
  end
  probe, got, phase, vdiff = nil, nil, "idle", nil
end

if CFG.obs then
  emu.addMemoryCallback(function(a, v)
    if frame < obsFrom or frame > obsTo then return end
    if phase == "reference" then
      ref[#ref + 1] = { frame, a, v }; lastRefFrame = frame
    elseif phase == "probe" and probe.mode ~= "shot" then
      got[#got + 1] = { a, v }
      local r = ref[probe.k0 + #got - 1]
      if probe.mode == "first" and (not r or r[2] ~= a or r[3] ~= v) then finish("diff", #got) end
    end
  end, emu.callbackType.write, CFG.obs[2], CFG.obs[3], CPU, MEM(CFG.obs[1]))
end

local robs = CFG.robs
if robs then
  local rcpu = robs[5]
  local pcOf = PCS[rcpu]
  emu.addMemoryCallback(function(a)
    if phase == "probe" and (probe.mode == "first" or probe.mode == "full")
        and #probe.reads < 64 and frame >= (robs[4] or 0) then
      if robs[3] and pcOf() ~= robs[3] then return end
      probe.reads[#probe.reads + 1] = rcpu == "nes" and nesPrg(a) or a
      -- Answer as soon as n reads are in: a change that later stalls the
      -- game still reports where the reader went.
      if robs[6] and #probe.reads >= robs[6] then finish("same") end
    end
  end, emu.callbackType.read, robs[1], robs[2], emu.cpuType[rcpu], ROM)
end

local function vsnap()
  local mt, t = MEM(vobs[1]), {}
  for i = 0, emu.getMemorySize(mt) - 1 do t[i] = emu.read(i, mt, false) end
  return t
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

local function startProbe(p)
  oneShot(function()
    local s = states[p.si]
    emu.loadSavestate(s.data)
    frame, polls = s.frame, s.poll
    p.undo = {}
    for _, w in ipairs(p.writes) do
      p.undo[#p.undo + 1] = { w[1], emu.read(w[1], ROM, false) }
      emu.write(w[1], w[2], ROM)
    end
    p.k0 = 1
    while ref[p.k0] and ref[p.k0][1] < s.frame do p.k0 = p.k0 + 1 end
    p.reads = {}
    p.rtext = function()
      if not robs then return "" end
      local r = {}
      for i, a in ipairs(p.reads) do r[i] = string.format("%X", a) end
      return " r=" .. table.concat(r, ",")
    end
    probe, got, phase = p, {}, "probe"
  end)
end

local function parseWrites(ws)
  local t = {}
  for a, v in (ws or ""):gmatch("(%x+):(%x+)") do t[#t + 1] = { tonumber(a, 16), tonumber(v, 16) } end
  return t
end

local function serve()
  while true do
    local line, err = receive()
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
      p = { id = 0, si = tonumber(si), mode = "settle", writes = {}, lo = tonumber(lo), hi = tonumber(hi), last = 0 }
    elseif cmd == "vobs" then
      -- Move the reference snapshot of the vobs memory to this frame.
      local si, at = rest:match("^(%d+) (%d+)$")
      p = { id = 0, si = tonumber(si), mode = "vobs", writes = {}, at = tonumber(at) }
    elseif cmd == "shot" then
      local si, at, path, ws = rest:match("^(%d+) (%d+) (%S+) ?(.*)$")
      p = { id = 0, si = tonumber(si), mode = "shot", at = tonumber(at), path = path, writes = parseWrites(ws) }
    elseif cmd == "probe" then
      local id, si, mode, ws = rest:match("^(%d+) (%d+) (%S+) ?(.*)$")
      p = { id = tonumber(id), si = tonumber(si), mode = mode, writes = parseWrites(ws) }
    end
    if p then
      phase = "arming"
      startProbe(p)
      return
    end
    send("err unknown command " .. tostring(cmd))
  end
end

emu.addEventCallback(function()
  if phase == "boot" then return end
  frame = frame + 1
  if phase == "probe" and probe.mode == "settle" then
    local mt = MEM(vobs[1])
    for i = probe.lo, probe.hi do
      if emu.read(i, mt, false) ~= vref[i] then probe.last = frame; break end
    end
    if frame >= vobs[2] then
      undo()
      send(string.format("settled %d", probe.last + 1))
      probe, got, phase = nil, nil, "idle"
    end
  elseif phase == "probe" and probe.mode == "vobs" then
    if frame == probe.at then
      vref = vsnap(); vobs[2] = probe.at; pend = probe.at
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
  elseif vobs and frame == vobs[2] then
    if phase == "reference" then
      vref = vsnap()
    elseif phase == "probe" then
      local t, d, n, lo, hi = vsnap(), {}, 0, -1, -1
      for i = 0, #vref do
        if t[i] ~= vref[i] then
          n = n + 1
          if lo < 0 then lo = i end
          hi = i
          if #d < 1024 then d[#d + 1] = string.format("%X:%X", i, t[i]) end
        end
      end
      vdiff = string.format(" v=%d,%d,%d d=%s", n, lo, hi, table.concat(d, ","))
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
    if frame > math.max(lastRefFrame, pend, vobs and vobs[2] or 0) then
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

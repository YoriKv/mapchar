-- mapchar capture: the replay, run headless. It loads a ring state, feeds the
-- recorded input back by poll, and at the capture point compares the RAM and
-- VRAM hash with the recorder's. With CFG.evidence it logs, in order, every
-- ROM data read (E pc address value), every RAM write (W pc address value)
-- and each frame's start (F n), and at the capture point saves every RAM and
-- which ROM bytes this replay read or executed. In the moment's gap (CFG.gap)
-- it logs nothing.

local inputs = {}
for p, k in slurp(CFG.input):gmatch("(%d+) ([^\n]*)") do inputs[tonumber(p)] = decInput(k) end

local frame, polls, loaded, done = 0, 0, false, false
local ev, buf, events = nil, {}, 0
local function out(s)
  if done then return end
  buf[#buf + 1] = s; events = events + 1
  if #buf >= 4096 then ev:write(table.concat(buf, "\n"), "\n"); buf = {} end
end

-- One callback may run a second at most, so the end is spread over several:
-- each step runs in an execution callback of its own.
local function steps(list)
  local i = 0
  local function nextStep()
    i = i + 1
    if list[i] then oneShot(function() list[i](); nextStep() end) end
  end
  nextStep()
end

local function finish()
  if done then return end
  done = true
  local h = hash()
  local tail = {}
  if ev then
    if #buf > 0 then ev:write(table.concat(buf, "\n"), "\n") end
    ev:close()
    for _, name in ipairs(CFG.rams) do
      tail[#tail + 1] = function() save(CFG.evidence .. "." .. name, dump(name)) end
    end
    -- The ROM bytes this replay read or executed: the counters were reset
    -- at its start, where the emulator's own code log spans every run.
    local size = emu.getMemorySize(ROM)
    if size <= 0x800000 then
      local rc, ec, parts = nil, nil, {}
      local CHUNK = 0x80000
      tail[#tail + 1] = function() rc = emu.getAccessCounters(ROM, emu.counterType.readCount) end
      tail[#tail + 1] = function() ec = emu.getAccessCounters(ROM, emu.counterType.execCount) end
      for lo = 0, size - 1, CHUNK do
        tail[#tail + 1] = function()
          local acc = {}
          for i = lo, math.min(lo + CHUNK, size) - 1 do
            acc[#acc + 1] = (rc[i] > 0 or ec[i] > 0) and "\1" or "\0"
          end
          parts[#parts + 1] = table.concat(acc)
        end
      end
      tail[#tail + 1] = function() save(CFG.evidence .. ".acc", table.concat(parts)) end
    end
  end
  tail[#tail + 1] = function()
    print(string.format("replay frame=%d poll=%d hash=%s match=%d events=%d",
      frame, polls, h, h == CFG.hash and 1 or 0, events))
    io.stdout:flush()
    emu.stop(0)
  end
  steps(tail)
end

oneShot(function()
  emu.loadSavestate(slurp(CFG.statePath))
  frame, polls, loaded = CFG.stateFrame, CFG.statePoll, true
  if CFG.evidence then
    ev = assert(io.open(CFG.evidence, "w"))
    hookReads(function(pc, a, v) out(string.format("E %X %X %X", pc, a, v)) end)
    hookWrites(function(pc, a, v) out(string.format("W %X %X %X", pc, a, v)) end)
  end
  emu.resetAccessCounters()
end)

emu.addEventCallback(function()
  if not loaded then return end
  polls = polls + 1
  setInputs(inputs[polls])
end, emu.eventType.inputPolled)

emu.addEventCallback(function()
  if not loaded or done then return end
  frame = frame + 1
  hooksOff = inGap(frame)
  if ev and not hooksOff then out("F " .. frame) end
  if frame == CFG.frame then
    if CFG.clock then
      -- The pause fell inside the next frame: the capture point is the
      -- first instruction at or past its master clock.
      local id
      id = emu.addMemoryCallback(function()
        if emu.getMasterClock() >= CFG.clock then
          emu.removeMemoryCallback(id, emu.callbackType.exec, 0, CFG.top, CPU)
          finish()
        end
      end, emu.callbackType.exec, 0, CFG.top, CPU)
    else
      finish()
    end
  elseif frame > CFG.frame + 2 then
    finish()
  end
end, emu.eventType.endFrame)

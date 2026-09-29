-- mapchar capture: the recorder, loaded into the emulator the user plays.
-- It keeps a ring of savestates (one every CFG.ring frames, CFG.keep kept,
-- each taken by a one-shot execution callback) and every polled input. When
-- the user pauses, it writes the moment — the ring, the input since its oldest
-- state, a screenshot, and the RAM and VRAM hash with the master clock they
-- were taken at — into CFG.out and tells mapchar.
--
-- With CFG.font it sets a font breakpoint: a read callback on the font's range
-- that removes itself on its first hit and is set again at the next frame's
-- end. A hit CFG.quiet frames or more after the last starts a text, and pins
-- the ring as it stands — the states of the 30 seconds before the text, in
-- which it may have been decoded — so the ring's turning no longer drops them.
--
-- A savestate the user loads, or a rewind, starts a new timeline: what was
-- kept describes the old one, so it is dropped. A debugger break close after
-- the last pause is the same pause, and writes no second moment.

local frame, polls = 0, 0
local inputs = {}              -- poll index -> encoded input
local pruned = 0               -- inputs up to this poll are dropped
local ring = {}                -- { frame, poll, state }
local pins = {}                -- the ring as it stood when the latest text started
local text = nil               -- { first, last }: the latest text's font reads
local moments = 0
local armed = false
local lastClock = nil          -- the master clock at the latest frame end
local lastMoment = nil         -- the master clock of the latest moment written
local SAME_PAUSE = 4           -- frames within which a break is the same pause

connect()
send("hello recorder")

local function snapshot()
  armed = false
  ring[#ring + 1] = { frame = frame, poll = polls, clock = emu.getMasterClock(), state = emu.createSavestate() }
  if #ring > CFG.keep then table.remove(ring, 1) end
end

-- The pins the ring no longer reaches, then the ring.
local function states()
  local list = {}
  for _, p in ipairs(pins) do
    if ring[1] and p.frame < ring[1].frame then list[#list + 1] = { s = p, pinned = true } end
  end
  for _, r in ipairs(ring) do list[#list + 1] = { s = r } end
  return list
end

local armFont, fontSet = nil, {}
if CFG.font then
  local f = CFG.font
  local mt = ramType(f.memory)
  local function disarm()
    for _, c in ipairs(fontSet) do
      emu.removeMemoryCallback(c[1], emu.callbackType.read, f.lo, f.hi, c[2], mt)
    end
    fontSet = {}
  end
  local function hit()
    if #fontSet == 0 then return end   -- another processor's, in the same frame
    if not text or frame - text.last >= CFG.quiet then
      text = { first = frame, last = frame }
      pins = {}
      for i = 1, #ring do pins[i] = ring[i] end
    else
      text.last = frame
    end
    disarm()
  end
  armFont = function()
    for _, name in ipairs(f.cpus) do
      local cpu = emu.cpuType[name]
      fontSet[#fontSet + 1] = { emu.addMemoryCallback(hit, emu.callbackType.read, f.lo, f.hi, cpu, mt), cpu }
    end
  end
end

emu.addEventCallback(function()
  polls = polls + 1
  inputs[polls] = encInput()
  -- Only what the oldest state still reaches is kept.
  local oldest = ring[1]
  if pins[1] and oldest and pins[1].frame < oldest.frame then oldest = pins[1] end
  if oldest then
    for p = pruned + 1, oldest.poll do inputs[p] = nil end
    pruned = math.max(pruned, oldest.poll)
  end
end, emu.eventType.inputPolled)

-- A new timeline: nothing kept reaches it.
local function restart()
  ring, pins, text, inputs = {}, {}, nil, {}
  pruned, armed = polls, false
  lastMoment = nil
end

emu.addEventCallback(restart, emu.eventType.stateLoaded)

emu.addEventCallback(function()
  -- The master clock only runs forward in one timeline: a rewind sets it back.
  local clock = emu.getMasterClock()
  if lastClock and clock < lastClock then restart() end
  lastClock = clock
  frame = frame + 1
  if frame % CFG.ring == 0 and not armed then
    armed = true
    oneShot(snapshot)
  end
  if armFont and #fontSet == 0 then armFont() end
end, emu.eventType.endFrame)

-- A moment's id: the session's prefix and a number no moment of this
-- session has, the script reloaded or not.
local function nextId()
  local root = CFG.out .. "/../"
  while true do
    moments = moments + 1
    local id = string.format("%s%04d", CFG.prefix, moments)
    if not exists(CFG.out .. "/" .. id .. ".txt") and not exists(root .. id .. "/moment.txt") then
      return id
    end
  end
end

-- The emulator's pause: the one event scripts get before it sleeps.
emu.addEventCallback(function()
  local clock = emu.getMasterClock()
  if lastMoment and lastClock and clock >= lastMoment then
    -- A frame's length in master clocks, from the ring's spacing; without
    -- it, only the very same clock is the same pause.
    local r1, r2 = ring[#ring - 1], ring[#ring]
    local perFrame = r1 and r2 and r2.frame > r1.frame and (r2.clock - r1.clock) / (r2.frame - r1.frame) or 0
    if clock - lastMoment <= SAME_PAUSE * perFrame then return end
  end
  local list = states()
  if #list == 0 then
    send("early")                -- nothing to replay from yet
    return
  end
  lastMoment = clock
  local id = nextId()
  local base = CFG.out .. "/" .. id
  local meta = {
    string.format("capture frame=%d poll=%d clock=%d hash=%s", frame, polls, clock, hash()),
  }
  if CFG.font then
    meta[#meta + 1] = "font watched"
    if text then meta[#meta + 1] = string.format("text first=%d last=%d", text.first, text.last) end
  end
  for i, e in ipairs(list) do
    save(string.format("%s_s%02d.mss", base, i), e.s.state)
    meta[#meta + 1] = string.format("state %d frame=%d poll=%d%s", i, e.s.frame, e.s.poll,
      e.pinned and " pinned" or "")
  end
  local inp = {}
  for p = list[1].s.poll + 1, polls do inp[#inp + 1] = string.format("%d %s", p, inputs[p] or "") end
  save(base .. "_input.txt", table.concat(inp, "\n") .. "\n")
  save(base .. ".png", emu.takeScreenshot())
  save(base .. ".txt", table.concat(meta, "\n") .. "\n")
  send("moment " .. id)
end, emu.eventType.codeBreak)

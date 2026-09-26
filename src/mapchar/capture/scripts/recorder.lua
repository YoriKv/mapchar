-- mapchar capture: the recorder, loaded into the emulator the user plays.
-- It watches no memory: it keeps a ring of savestates (one every CFG.ring
-- frames, CFG.keep kept, each taken by a one-shot execution callback) and
-- every polled input. When the user pauses, it writes the moment — the ring,
-- the input since its oldest state, a screenshot, and the RAM and VRAM hash
-- with the master clock they were taken at — into CFG.out and tells mapchar.

local frame, polls = 0, 0
local inputs = {}              -- poll index -> encoded input
local ring = {}                -- { frame, poll, state }
local moments = 0
local armed = false

connect()
send("hello recorder")

local function snapshot()
  armed = false
  ring[#ring + 1] = { frame = frame, poll = polls, state = emu.createSavestate() }
  if #ring > CFG.keep then table.remove(ring, 1) end
end

emu.addEventCallback(function()
  polls = polls + 1
  inputs[polls] = encInput()
  -- Only what the ring still reaches is kept.
  if ring[1] then
    for p = (ring[1].prune or 1), ring[1].poll do inputs[p] = nil end
    ring[1].prune = ring[1].poll + 1
  end
end, emu.eventType.inputPolled)

emu.addEventCallback(function()
  frame = frame + 1
  if frame % CFG.ring == 0 and not armed then
    armed = true
    oneShot(snapshot)
  end
  local line, err = receive()
  while line do
    if line == "quit" then emu.stop(0) end
    line, err = receive()
  end
end, emu.eventType.endFrame)

-- The emulator's pause: the one event scripts get before it sleeps.
emu.addEventCallback(function()
  if #ring == 0 then
    send("early")                -- nothing to replay from yet
    return
  end
  moments = moments + 1
  local id = string.format("%s%04d", CFG.prefix, moments)
  local base = CFG.out .. "/" .. id
  local meta = {
    string.format("capture frame=%d poll=%d clock=%d hash=%s", frame, polls, emu.getMasterClock(), hash()),
  }
  for i, r in ipairs(ring) do
    save(string.format("%s_s%02d.mss", base, i), r.state)
    meta[#meta + 1] = string.format("state %d frame=%d poll=%d", i, r.frame, r.poll)
  end
  local inp = {}
  for p = ring[1].poll + 1, polls do inp[#inp + 1] = string.format("%d %s", p, inputs[p] or "") end
  save(base .. "_input.txt", table.concat(inp, "\n") .. "\n")
  save(base .. ".png", emu.takeScreenshot())
  save(base .. ".txt", table.concat(meta, "\n") .. "\n")
  send("moment " .. id)
end, emu.eventType.codeBreak)

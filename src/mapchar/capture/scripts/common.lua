-- mapchar capture: what every script shares. The generated header before this
-- defines CFG (the console profile and the role's settings); the role's own
-- script follows.

local function slurp(path)
  local fh = assert(io.open(path, "rb")); local d = fh:read("a"); fh:close(); return d
end

local function save(path, data)
  local fh = assert(io.open(path, "wb")); fh:write(data); fh:close()
end

-- The connection to mapchar: whole lines both ways. A send blocks until all
-- of it is out, and a line that arrives in pieces is kept until its newline
-- (LuaSocket hands a non-blocking receive's partial line back as its third
-- result, and drops it unless it is passed back in).
local conn, pending = nil, ""
local function connect()
  if not CFG.port then return end
  local socket = require("socket.core")
  conn = socket.tcp(); conn:settimeout(10)
  assert(conn:connect("127.0.0.1", CFG.port))
  conn:settimeout(0); conn:setoption("tcp-nodelay", true)
end

local function send(s)
  if not conn then print(s); return end
  conn:settimeout(30); local ok, err = conn:send(s .. "\n"); conn:settimeout(0)
  if not ok then print("! send: " .. tostring(err)) end
end

-- The next whole line from mapchar, or nil; "closed" as the second result
-- when mapchar has gone.
local function receive()
  if not conn then return nil end
  local line, err, part = conn:receive("*l", pending)
  if not line then
    pending = part or pending
    return nil, err
  end
  pending = ""
  return line
end

-- A callback that errors or overruns stops the script with the error only in
-- the script window: report it on stdout and to mapchar instead.
local function guard(fn)
  return function(...)
    local ok, err = pcall(fn, ...)
    if not ok then
      print("! " .. tostring(err)); io.stdout:flush()
      pcall(send, "err " .. tostring(err):gsub("%s+", " "))
    end
  end
end
local addMemoryCallback, addEventCallback = emu.addMemoryCallback, emu.addEventCallback
emu.addMemoryCallback = function(fn, ...) return addMemoryCallback(guard(fn), ...) end
emu.addEventCallback = function(fn, ...) return addEventCallback(guard(fn), ...) end

local MEM = function(name) return emu.memType[name] end
local CPU = emu.cpuType[CFG.cpu]
local ROM = MEM(CFG.rom)

-- Adler-32 of every RAM of the profile, VRAM last.
local function hash()
  local h = {}
  for _, name in ipairs(CFG.rams) do
    local mt = MEM(name)
    local a, b = 1, 0
    for i = 0, emu.getMemorySize(mt) - 1 do
      a = (a + emu.read(i, mt, false)) % 65521; b = (b + a) % 65521
    end
    h[#h + 1] = string.format("%08X", (b << 16) | a)
  end
  return table.concat(h, ":")
end

local function dump(name)
  local mt, parts = MEM(name), {}
  for i = 0, emu.getMemorySize(mt) - 1 do parts[#parts + 1] = string.char(emu.read(i, mt, false)) end
  return table.concat(parts)
end

-- An execution callback over the main CPU's whole space fires on the next
-- instruction: where a savestate can be taken or loaded.
local function oneShot(fn)
  local id
  id = emu.addMemoryCallback(function()
    emu.removeMemoryCallback(id, emu.callbackType.exec, 0, CFG.top, CPU)
    fn()
  end, emu.callbackType.exec, 0, CFG.top, CPU)
end

-- Input: every port's buttons, encoded "a+start" per port, "|" between ports.
local PORTS = 2
local function encInput()
  local ports = {}
  for p = 0, PORTS - 1 do
    local ok, t = pcall(emu.getInput, p)
    local k = {}
    if ok and t then
      for name, v in pairs(t) do if v == true then k[#k + 1] = name end end
    end
    table.sort(k)
    ports[#ports + 1] = table.concat(k, "+")
  end
  return table.concat(ports, "|")
end

local function decInput(s)
  local ports, p = {}, 0
  for part in (s .. "|"):gmatch("([^|]*)|") do
    local t = {}
    for b in part:gmatch("[^+]+") do t[b] = true end
    ports[p] = t; p = p + 1
  end
  return ports
end

local function setInputs(ports)
  for p = 0, PORTS - 1 do pcall(emu.setInput, ports and ports[p] or {}, p) end
end

-- A NES bus address as a PRG ROM offset, under the mapper's banks now.
local function nesPrg(bus)
  local c = emu.convertAddress(bus, emu.memType.nesMemory, emu.cpuType.nes)
  return c and c.memType == emu.memType.nesPrgRom and c.address or (0x1000000 | bus)
end

local function nesPc() return nesPrg(emu.getCpuState(emu.cpuType.nes).pc) end

-- The PC a reader's read is logged with, per processor.
local PCS = {
  snes = function() local st = emu.getCpuState(emu.cpuType.snes); return (st.k << 16) | st.pc end,
  gsu = function() local st = emu.getCpuState(emu.cpuType.gsu); return 0x1000000 | (st.programBank << 16) | st.r15 end,
  gba = function() return emu.getCpuState(emu.cpuType.gba)["pipeline.execute.address"] end,
  nes = nesPc,
}

-- While true, the read and write hooks below drop everything at once: the
-- replay's gap, where nothing is logged.
local hooksOff = false

-- Whether a frame lies in the moment's gap (CFG.gap = { from, to }).
local function inGap(f) return CFG.gap ~= nil and f >= CFG.gap[1] and f < CFG.gap[2] end

-- Every ROM data read of the profile's readers: fn(pc, address, value), the
-- address as the console reports it (the bus address; a PRG offset for the
-- NES). Each reader's filter drops what is not a data read of its own.
local function hookReads(fn)
  local size = emu.getMemorySize(ROM)
  for _, r in ipairs(CFG.readers) do
    if r == "snes" then
      emu.addMemoryCallback(function(a, v)
        if hooksOff then return end
        local st = emu.getCpuState(emu.cpuType.snes)
        fn((st.k << 16) | st.pc, a, v)
      end, emu.callbackType.read, 0, size - 1, emu.cpuType.snes, ROM)
    elseif r == "gsu" then
      -- The SuperFX filling its instruction cache is reported as data reads:
      -- a read of the program bank near R15 is a code fetch, and its page is
      -- dropped from then on before any state is asked for.
      local codePage = {}
      emu.addMemoryCallback(function(a, v)
        if hooksOff then return end
        local page = a >> 8
        if codePage[page] then return end
        local st = emu.getCpuState(emu.cpuType.gsu)
        if (a >> 16) == st.programBank and math.abs((a & 0xFFFF) - st.r15) < 0x200 then
          codePage[page] = true; return
        end
        fn(0x1000000 | (st.programBank << 16) | st.r15, a, v)
      end, emu.callbackType.read, 0, size - 1, emu.cpuType.gsu, ROM)
    elseif r == "nes" then
      -- Bank-switched: addresses and PCs as PRG ROM offsets. A read at or just
      -- past the PC is the 6502's dummy read after an implied opcode.
      local nc = emu.cpuType.nes
      emu.addMemoryCallback(function(a, v)
        if hooksOff then return end
        local pc = emu.getCpuState(nc).pc
        if a >= pc - 1 and a <= pc + 3 then return end
        fn(nesPrg(pc), nesPrg(a), v)
      end, emu.callbackType.read, 0, size - 1, nc, ROM)
    elseif r == "gba" then
      -- ARM code loads its constants from just past itself: a literal pool.
      emu.addMemoryCallback(function(a, v)
        if hooksOff then return end
        local pc = emu.getCpuState(emu.cpuType.gba)["pipeline.execute.address"]
        if math.abs((a | 0x08000000) - pc) < 0x1000 then return end
        fn(pc, a, v)
      end, emu.callbackType.read, 0, size - 1, emu.cpuType.gba, ROM)
    end
  end
end


-- Every RAM write by the main CPU: fn(pc, address, value), the address as
-- the bus has it.
local function hookWrites(fn)
  if CFG.console == "snes" then
    emu.addMemoryCallback(function(a, v)
      if hooksOff then return end
      local st = emu.getCpuState(emu.cpuType.snes)
      fn((st.k << 16) | st.pc, a, v)
    end, emu.callbackType.write, 0, 0x1FFFF, emu.cpuType.snes, emu.memType.snesWorkRam)
  elseif CFG.console == "nes" then
    local w = function(a, v) if hooksOff then return end fn(nesPc(), a, v) end
    emu.addMemoryCallback(w, emu.callbackType.write, 0x0000, 0x07FF, emu.cpuType.nes, emu.memType.nesMemory)
    emu.addMemoryCallback(w, emu.callbackType.write, 0x6000, 0x7FFF, emu.cpuType.nes, emu.memType.nesMemory)
  end
end

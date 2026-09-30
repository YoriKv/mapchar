-- mapchar capture: what every script shares. The generated header before this
-- defines CFG (the console profile and the role's settings); the role's own
-- script follows.

local function slurp(path)
  local fh = assert(io.open(path, "rb")); local d = fh:read("a"); fh:close(); return d
end

local function save(path, data)
  local fh = assert(io.open(path, "wb")); fh:write(data); fh:close()
end

local function exists(path)
  local fh = io.open(path, "rb")
  if fh then fh:close(); return true end
  return false
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
-- when mapchar has gone. With `wait`, a receive that finds nothing waits up
-- to that many seconds for a line before it gives up.
local function receive(wait)
  if not conn then return nil end
  local line, err, part = conn:receive("*l", pending)
  if not line and err == "timeout" and wait then
    pending = part or pending
    conn:settimeout(wait)
    line, err, part = conn:receive("*l", pending)
    conn:settimeout(0)
  end
  if not line then
    pending = part or pending
    return nil, err
  end
  pending = ""
  return line
end

-- A callback that errors or overruns stops the script with the error only in
-- the script window: report it on stdout and to mapchar instead. What the
-- callback returns is passed on: a memory callback's integer replaces the
-- value read or written.
-- In the probe server, a callback stopped by the one-second watchdog is a
-- "stall", not an error: a headless run's thread can stall for seconds in a
-- callback doing a millisecond's work, and the server is restarted for the
-- next request. Anywhere else it is an error: a replay missing what the
-- stopped callback would have logged, or a pause that wrote no moment, is
-- not to be trusted.
local function report(err)
  local msg = tostring(err):gsub("%s+", " ")
  local stall = CFG.role == "probe" and msg:find("Maximum execution time", 1, true)
  local kind = stall and "stall " or "err "
  print((kind == "err " and "! " or "~ ") .. msg); io.stdout:flush()
  pcall(send, kind .. msg)
end
local function passOn(ok, ...)
  if ok then return ... end
  report((...))
end
local function guard(fn)
  return function(...) return passOn(pcall(fn, ...)) end
end
local addMemoryCallback, addEventCallback = emu.addMemoryCallback, emu.addEventCallback
emu.addMemoryCallback = function(fn, ...) return addMemoryCallback(guard(fn), ...) end
emu.addEventCallback = function(fn, ...) return addEventCallback(guard(fn), ...) end

local MEM = function(name) return emu.memType[name] end
local CPU = emu.cpuType[CFG.cpu]
local ROM = MEM(CFG.rom)

-- Memory type names by value.
local MEM_NAMES = {}
for name, v in pairs(emu.memType) do if v < 0x100 then MEM_NAMES[v] = name end end

-- Each processor's own address space, for converting its addresses.
local REL = {
  snes = "snesMemory", sa1 = "sa1Memory", gsu = "gsuMemory", nes = "nesMemory", gba = "gbaMemory",
  gameboy = "gameboyMemory", sms = "smsMemory", pce = "pceMemory",
}

-- Processors whose ROM is banked in and out too often to keep a conversion.
local BANKED = { nes = true, gameboy = true, sms = true, pce = true }

-- A memory of the profile, or its stand-in when the cartridge has none (the
-- NES's work RAM for its save RAM).
local function ramType(name)
  local mt = MEM(name)
  local alias = CFG.aliases and CFG.aliases[name]
  if alias and emu.getMemorySize(mt) == 0 then return MEM(alias) end
  return mt
end

-- Every memory hashed and saved: the profile's RAMs (the VRAM last), then
-- the extra ones. A hash taken with fewer is a prefix of one taken with more.
local function allRams()
  local list = {}
  for _, name in ipairs(CFG.rams) do list[#list + 1] = name end
  for _, name in ipairs(CFG.extraRams or {}) do list[#list + 1] = name end
  return list
end

-- Adler-32 of every memory, in allRams order: the profile's RAMs as they
-- are named (an empty one hashes as empty, as it always has), the extras with
-- their stand-ins.
local function hash()
  local h = {}
  local nrams = #CFG.rams
  for i, name in ipairs(allRams()) do
    local mt = i <= nrams and MEM(name) or ramType(name)
    local a, b = 1, 0
    for i = 0, emu.getMemorySize(mt) - 1 do
      a = (a + emu.read(i, mt, false)) % 65521; b = (b + a) % 65521
    end
    h[#h + 1] = string.format("%08X", (b << 16) | a)
  end
  return table.concat(h, ":")
end

-- Whether a hash is the one wanted: equal, or wanted's memories a prefix.
local function hashMatches(h, want)
  if not want then return false end
  return h == want or h:sub(1, #want + 1) == want .. ":"
end

local function dump(name)
  local mt, parts = ramType(name), {}
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
-- A replay sets only the buttons pressed: setting the others false as well
-- (which Mesen honours) broke Dragon Warrior II's replay.
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

-- A banked processor's address as a ROM offset under its banks now, or with
-- bit 24 set where it is not ROM (code running from RAM).
local function bankedOffset(cpu, a)
  local ok, c = pcall(emu.convertAddress, a, MEM(REL[cpu]), emu.cpuType[cpu])
  if ok and c and c.memType == ROM then return c.address end
  return 0x1000000 | a
end
local function bankedPc(cpu)
  return function() return bankedOffset(cpu, emu.getCpuState(emu.cpuType[cpu]).pc) end
end
-- The PC a reader's read is logged with, per processor: the SuperFX's
-- marked with bit 24, the SA-1's with bit 25.
local PCS = {
  snes = function() local st = emu.getCpuState(emu.cpuType.snes); return (st.k << 16) | st.pc end,
  -- The SA-1's state is its chip's, the CPU's registers under "cpu.".
  sa1 = function()
    local st = emu.getCpuState(emu.cpuType.sa1)
    return 0x2000000 | ((st.k or st["cpu.k"]) << 16) | (st.pc or st["cpu.pc"])
  end,
  gsu = function() local st = emu.getCpuState(emu.cpuType.gsu); return 0x1000000 | (st.programBank << 16) | st.r15 end,
  gba = function() return emu.getCpuState(emu.cpuType.gba)["pipeline.execute.address"] end,
  nes = nesPc,
  -- Banked: the PC as a ROM offset, like the NES's.
  gameboy = bankedPc("gameboy"),
  sms = bankedPc("sms"),
  pce = bankedPc("pce"),
}

-- A bus address as a ROM offset, as the emulator maps it: nil where it is
-- not ROM. Kept per processor and 4 KiB page; the SA-1's bank registers
-- ($2220-$2223) forget what was kept. The NES's banks change too often:
-- nesPrg converts every address.
local romPages = {}
local function forgetPages() romPages = {} end
local function romOffset(cpu, a)
  if BANKED[cpu] then
    local off = cpu == "nes" and nesPrg(a) or bankedOffset(cpu, a)
    return off < 0x1000000 and off or nil
  end
  local pages = romPages[cpu]
  if not pages then pages = {}; romPages[cpu] = pages end
  local page = a >> 12
  local base = pages[page]
  if base == nil then
    local ok, c = pcall(emu.convertAddress, a, MEM(REL[cpu]), emu.cpuType[cpu])
    base = ok and c and c.memType == ROM and c.address - (a & 0xFFF) or false
    pages[page] = base
  end
  return base and base + (a & 0xFFF) or nil
end
for _, r in ipairs(CFG.readers) do
  if r == "sa1" then
    -- Every bank the registers are visible in.
    for bank = 0x00, 0xBF do
      if bank < 0x40 or bank >= 0x80 then
        emu.addMemoryCallback(forgetPages, emu.callbackType.write, (bank << 16) | 0x2220,
          (bank << 16) | 0x2223, emu.cpuType.snes, emu.memType.snesMemory)
      end
    end
  end
end

-- While true, the read and write hooks below drop everything at once: the
-- replay's gap, where nothing is logged.
local hooksOff = false

-- Whether a frame lies in the moment's gap (CFG.gap = { from, to }).
local function inGap(f) return CFG.gap ~= nil and f >= CFG.gap[1] and f < CFG.gap[2] end

-- Every ROM data read of the profile's readers: fn(pc, address, value, rom),
-- the address as the reader's bus has it (the PRG offset for the NES) and rom
-- its ROM offset as the emulator maps it, nil when it could not. Each
-- reader's filter drops what is not a data read of its own.
local function hookReads(fn)
  local size = emu.getMemorySize(ROM)
  for _, r in ipairs(CFG.readers) do
    if r == "snes" or r == "sa1" then
      local ct, pcOf = emu.cpuType[r], PCS[r]
      emu.addMemoryCallback(function(a, v)
        if hooksOff then return end
        fn(pcOf(), a, v, romOffset(r, a))
      end, emu.callbackType.read, 0, size - 1, ct, ROM)
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
        fn(0x1000000 | (st.programBank << 16) | st.r15, a, v, romOffset("gsu", a))
      end, emu.callbackType.read, 0, size - 1, emu.cpuType.gsu, ROM)
    elseif r == "nes" then
      -- Bank-switched: addresses and PCs as PRG ROM offsets. A read at or just
      -- past the PC is the 6502's dummy read after an implied opcode.
      local nc = emu.cpuType.nes
      emu.addMemoryCallback(function(a, v)
        if hooksOff then return end
        local pc = emu.getCpuState(nc).pc
        if a >= pc - 1 and a <= pc + 3 then return end
        local off = nesPrg(a)
        fn(nesPrg(pc), off, v, off < 0x1000000 and off or nil)
      end, emu.callbackType.read, 0, size - 1, nc, ROM)
    elseif r == "gba" then
      -- ARM code loads its constants from just past itself: a literal pool.
      emu.addMemoryCallback(function(a, v)
        if hooksOff then return end
        local pc = emu.getCpuState(emu.cpuType.gba)["pipeline.execute.address"]
        if math.abs((a | 0x08000000) - pc) < 0x1000 then return end
        fn(pc, a, v, romOffset("gba", a))
      end, emu.callbackType.read, 0, size - 1, emu.cpuType.gba, ROM)
    elseif BANKED[r] then
      -- The Game Boy, Master System and PC Engine: opcode and operand
      -- fetches are exec, not read. The PC Engine's HuC6280 also makes a
      -- dummy read of the byte at the PC (after an implied instruction, a
      -- taken branch, a block transfer's start), dropped as the 6502's is;
      -- the Game Boy and Master System were seen to make none.
      local ct, pcOf = emu.cpuType[r], PCS[r]
      local dummy = r == "pce"
      emu.addMemoryCallback(function(a, v)
        if hooksOff then return end
        if dummy then
          local pc = emu.getCpuState(ct).pc
          if a >= pc - 1 and a <= pc + 3 then return end
        end
        fn(pcOf(), a, v, romOffset(r, a))
      end, emu.callbackType.read, 0, size - 1, ct, ROM)
    end
  end
end

-- The profile's write hooks (CFG.writes: { cpu, memory type, lo, hi, RAM, by
-- bus }), each with its memory type resolved and its range's end: nil for a
-- memory the cartridge does not have.
local function writeHooks()
  local list = {}
  for _, h in ipairs(CFG.writes or {}) do
    local cpuName, mem, lo, hi, ram, byBus = h[1], h[2], h[3], h[4], h[5], h[6]
    local mt = (mem == ram) and ramType(mem) or MEM(mem)
    local size = emu.getMemorySize(mt)
    if hi < 0 then hi = size - 1 end
    if size > 0 and hi >= lo then
      list[#list + 1] = { cpu = cpuName, mt = mt, lo = lo, hi = hi, ram = ram, byBus = byBus }
    end
  end
  return list
end

-- A GBA RAM page (256 bytes) nearly wholly rewritten — BULK_WORDS of its 64
-- words or more — BULK_FRAMES frames running is a buffer being filled every
-- frame (a framebuffer, a tile being drawn): its writes are dropped while
-- that goes on, and logged again from the frame after it stops. A menu that
-- redraws its string every frame rewrites a few words, and stays. onBulk
-- (kind, page) hears of each ban ("B") and lifting ("U"), which take effect
-- from the next frame: the probe server drops the same writes.
local BULK_WORDS, BULK_FRAMES = 50, 3

-- Every RAM write the profile's hooks see: fn(pc, address, value[, memory,
-- offset]), the address as the writer's bus has it. A write outside the main
-- CPU's own RAM also carries the memory and offset the emulator puts it at —
-- a save RAM's mirrors, a bank register's choice, a coprocessor's view.
local function hookWrites(fn, onBulk)
  local bulk = CFG.console == "gba"
  local words, streak, banned = {}, {}, {}
  if bulk then
    emu.addEventCallback(function()
      for page, set in pairs(words) do
        if set.n >= BULK_WORDS then
          streak[page] = (streak[page] or 0) + 1
          if streak[page] >= BULK_FRAMES and not banned[page] then
            banned[page] = true
            if onBulk then onBulk("B", page) end
          end
        else
          streak[page] = nil
        end
      end
      for page in pairs(banned) do
        local set = words[page]
        if not set or set.n < BULK_WORDS then
          banned[page], streak[page] = nil, nil
          if onBulk then onBulk("U", page) end
        end
      end
      for page in pairs(streak) do if not words[page] then streak[page] = nil end end
      words = {}
    end, emu.eventType.endFrame)
  end
  for _, h in ipairs(writeHooks()) do
    local cpuName = h.cpu
    local cpu, pcOf = emu.cpuType[cpuName], PCS[cpuName]
    -- The main CPU's own RAM is spelled by its bus on the SNES and the NES
    -- (their formulas are exact) and on the GBA; everything else, banked or
    -- mirrored, is placed by the emulator.
    local plain = (CFG.console == "snes" or CFG.console == "nes") and cpuName == CFG.cpu
      and h.ram == CFG.rams[1]
    local convert = CFG.console ~= "gba" and not plain
    local rel = convert and MEM(REL[cpuName])
    emu.addMemoryCallback(function(a, v)
      if hooksOff then return end
      if bulk then
        local page = a >> 8
        local set = words[page]
        if not set then set = { n = 0 }; words[page] = set end
        local w = (a >> 2) & 0x3F
        if not set[w] then set[w] = true; set.n = set.n + 1 end
        if banned[page] then return end
      end
      local pc = pcOf()
      if convert then
        local c = emu.convertAddress(a, rel, cpu)
        if c then fn(pc, a, v, MEM_NAMES[c.memType], c.address); return end
      end
      fn(pc, a, v)
    end, emu.callbackType.write, h.lo, h.hi, cpu, h.mt)
  end
end

"""Capturing text from a running game.

mapchar runs the ROM in an emulator; the user pauses and types the text they
see, and each capture is traced — replayed headless and probed by changing ROM
bytes — to where the string lives, what points to it and what every code does.
The finished captures together propose blocks, pointer tables and table
entries. Qt-free: :mod:`mapchar.ui.capture` is the only UI.

- :mod:`.chains` — relative search over code sequences, per typed word;
- :mod:`.bitlayout` — how a bit-packed stream is cut into codes;
- :mod:`.consoles` — the per-console facts;
- :mod:`.emulator` — launching the emulator with a script (Mesen 2), whose
  scripts are in ``scripts/``;
- :mod:`.protocol` — the line protocol, and the steps long work is made of;
- :mod:`.evidence` — the moment, its replay and the evidence it records;
- :mod:`.occurrence` — finding the typed text in the evidence;
- :mod:`.probe` — the probe server's client;
- :mod:`.trace` — the rules each result is decided by;
- :mod:`.session` — the captures, the recorder and the queue;
- :mod:`.tablesweep` — a pointer table's strings shown in the game;
- :mod:`.combine` and :mod:`.proposals` — what the captures say together.
"""

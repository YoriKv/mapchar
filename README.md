# mapChar

**mapChar** is a cross-platform text viewer and editor for romhacking and research.
It finds the strings in a ROM, decodes them through table files, edits translations
beside the original, writes them back in place, and exchanges dumps with
Cartographer, Atlas and abcde.

If you run into issues or have questions. Submit a github issue or reach out to me (Epi)
on Discord through the https://romhack.ing/ Discord server.

mapChar is built on Python + Qt (PySide6) and runs on Windows, macOS, and Linux.

| | | |
|:-:|:-:|:-:|
| [![Exploring a ROM](screenshots/screen_01.png)](screenshots/screen_01.png) | [![Blocks & pointer tables](screenshots/screen_02.png)](screenshots/screen_02.png) | [![Decoded text](screenshots/screen_03.png)](screenshots/screen_03.png) |
| Exploring a ROM | Blocks & pointer tables | Decoded text |
| [![Translating strings](screenshots/screen_04.png)](screenshots/screen_04.png) | [![Tables & charsets](screenshots/screen_05.png)](screenshots/screen_05.png) | [![Table editor](screenshots/screen_06.png)](screenshots/screen_06.png) |
| Translating strings | Tables & charsets | Table editor |

## Features

- **Table files** - the native grammar plus the romjuice, Cartographer, Atlas and
  abcde dialects, loaded and shown converted. Text, end, code, switch and return
  entries, bit-width keys, and a table editor that needs no knowledge of the
  grammar.
- **Charsets** - ASCII, Latin-1, Windows-1252, JIS X 0201, Shift-JIS, EUC-JP,
  EUC-KR, Big5, GBK, UTF-8 and UTF-16, usable as tables of their own or as the
  base a table overrides.
- **Blocks** - a region read as a list of strings under one configuration:
  source, string rules, skip ranges, realignment. The unit of dumping, editing
  and writing.
- **Pointers** - LoROM, HiROM, GB, GBA, linear and generic banked mappings, with
  pointer discovery that works out size, endianness, mapping and offset from the
  strings themselves, and rewrites every pointer on write.
- **Finding text** - relative search with case gaps, wildcards and kana runs
  that seeds a table from a hit, and a scan that scores the file for
  text-likeness and offers the regions it finds as blocks.
- **Editing** - translations edited beside the original with code completion,
  live byte counts against the room available, filters, find and replace across
  a project, and undo across everything.
- **Writing back** - packed or slotted layout, fill bytes, bounds, and one undo
  step per file that puts the bytes back.
- **Compression** - LZSS family, SNES LZ1/LZ2, Kosinski, PRS, RLE and Konami RLE,
  PackBits, bit-packed text and ROM-table Huffman, all of them compressing back.
- **Containers** - iNES and SNES copier headers, N64 byte orders, `.smd` and SNES
  interleave, and joined chips.
- **Preview** - strings rendered through a font sheet into a text box, showing
  how they lay out on screen, with wrapping that fits a translation to the box.
- **Dump, export and import** - a native script format, TSV, CSV and PO for
  translators, and Cartographer and Atlas command files both ways.
- **Plugin system** - containers, compression, charsets and mappings as TOML
  presets or Python code, per machine or carried with a project.
- **Projects** - session can be saved as a `.mapchar` file and picked up later.

## Getting Started

### Install

Grab the build for your platform from the [Releases page](https://github.com/YoriKv/mapchar/releases), unpack and run, no installer.

### First steps

1. **Open a file** - File -> Open, or drag a ROM/binary onto the window.
2. **Find the text** - scroll the raw view, type in an offset, or use Search ->
   Relative Search / Scan to find where the strings are.
3. **Get a table** - load a `.tbl` file, build one from a relative search hit, or
   pick one of the built-in charsets.
4. **Make a block** - File -> New Block over the strings, set how they are
   bounded (end token, fixed length, Pascal prefix) and where their pointers are.
5. **Translate** - edit translations in the Strings view, watching the byte
   counts, or dump a script and import it back.
6. **Write/Save** - write the strings back to the original file. Save your
   project session to resume later.

Help -> Shortcuts (`F1`) to view a list of keyboard shortcuts.

## Thank You

Thanks to the following projects that I used as reference for this tool, and for
the accumulated community knowledge this project represents. No code was copied
or used from these projects directly.

- **Cartographer**
- **Atlas**
- **abcde**
- **romjuice**

## AI Use Disclaimer

This tool was created with the help of an AI coding agent. All of the code and
some of the tooltips are AI generated, but the design and other aspects of this
project are my own.

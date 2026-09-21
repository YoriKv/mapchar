from __future__ import annotations

import os

from helpers import ABC_TABLE, relayout, table_set
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    RangeSource,
    Status,
    bits_digest,
)
from mapchar.core.context import PipelineContext
from mapchar.core.document import Document
from mapchar.core.font import TextBox
from mapchar.core.table import Table, TableEntry, TokenKind
from mapchar.pipeline.extract import extract
from mapchar.project.entry import Entry, EntryKind, StringState, moved, tree_order
from mapchar.project.glossary import GlossaryTerm, matching_terms
from mapchar.project.missing_files import missing_paths, relocate_path, retarget_files
from mapchar.project.projectfile import (
    PROJECT_VERSION,
    entries_from_payload,
    entries_payload,
    load_project,
    project_dict,
    save_project,
)
from mapchar.project.tables import (
    adopt_table,
    capture_overlay,
    fold_overlay,
    overlay_of,
)
from mapchar.project.workspace import Workspace


def test_workspace_children_and_close():
    ws = Workspace()
    f = ws.open_file("/tmp/a.nes")
    assert ws.open_file("/tmp/a.nes") is f
    b = ws.add(Entry(EntryKind.BLOCK, "b", "/tmp/a.nes", parent=f))
    t = ws.add(Entry(EntryKind.TABLE, "t", "/tmp/t.tbl"))
    assert ws.entries == [f, b, t]
    g = ws.open_file("/tmp/b.nes")
    b2 = ws.add(Entry(EntryKind.BLOCK, "b2", "/tmp/a.nes", parent=f))
    assert ws.entries == [f, b, b2, t, g]
    ws.set_current(b)
    removed = ws.close(f)
    assert set(removed) == {f, b, b2} and ws.entries == [t, g]
    assert ws.current is g  # the same group, not the table that follows
    ws.stamp(g)
    assert g.dirty
    ws.mark_saved(g)
    assert not g.dirty


WAS = bits_digest("0")
"""The digest of some other bytes: what an original's is once its string is
edited."""


def test_project_roundtrip(tmp_path):
    rom = tmp_path / "rom.bin"
    rom.write_bytes(bytes.fromhex("41 00 42 00"))
    ws = Workspace()
    f = ws.open_file(str(rom), container_id="raw")
    cfg = BlockConfig(RangeSource(0, 4), EndToken(), "main")
    b = ws.add(Entry(EntryKind.BLOCK, "Dialogue", str(rom), parent=f, config=cfg))
    ts = table_set(ABC_TABLE, "main")
    ex = extract(rom.read_bytes(), cfg, ts)
    b.doc = Document(rom.read_bytes(), PipelineContext(), True, strings=ex.strings)
    # The second string's bytes were other bytes when its original was taken.
    b.doc.strings[1].original = "C[end]"
    b.doc.strings[1].original_digest = WAS
    b.doc.strings[1].refresh_status()
    b.doc.strings[1].notes = "n"
    b.box = TextBox(chars_per_line=18, lines_per_page=3)
    ws.add(Entry(EntryKind.BOOKMARK, "bm", str(rom), parent=f, bookmark_offset=2))
    ws.add(Entry(EntryKind.TABLE, "t", str(tmp_path / "t.tbl"), dialect="abcde"))
    ws.set_current(b)
    ws.glossary = [GlossaryTerm("Slime", "Gluant", "the blue one"), GlossaryTerm("HP")]
    proj = tmp_path / "p.mapchar"
    save_project(str(proj), ws.entries, ws.current, ws.glossary)
    text = proj.read_text()
    assert '"path": "rom.bin"' in text and "strings" in text
    loaded = load_project(str(proj))
    kinds = [e.kind for e in loaded.entries]
    assert kinds == [
        EntryKind.FILE,
        EntryKind.BLOCK,
        EntryKind.BOOKMARK,
        EntryKind.TABLE,
    ]
    lb = loaded.entries[1]
    assert lb.parent is loaded.entries[0] and lb.config == cfg
    assert loaded.current is lb
    # Every string's original travels, whether or not its bytes still say it.
    a_bytes = bits_digest(format(0x4100, "016b"))
    assert lb.pending_strings[0] == StringState("A[end]", digest=a_bytes)
    assert lb.pending_strings[1] == StringState("C[end]", Status.EDITED, "n", None, WAS)
    assert loaded.entries[2].bookmark_offset == 2
    assert loaded.entries[3].dialect == "abcde"
    assert os.path.isabs(lb.path)
    assert lb.box == TextBox(chars_per_line=18, lines_per_page=3)
    assert loaded.glossary == ws.glossary
    # An empty glossary writes nothing, and a project with none reads as empty.
    assert "glossary" not in project_dict(ws.entries, None, None)
    assert loaded.glossary is not ws.glossary


def test_matching_terms_are_found_folded_and_longest_first():
    terms = [GlossaryTerm("HP"), GlossaryTerm("slime"), GlossaryTerm("King Slime")]
    found = matching_terms(terms, "The KING SLIME has 20 hp[end]")
    assert [t.term for t in found] == ["King Slime", "slime", "HP"]
    assert matching_terms(terms, "nothing here") == []


def test_project_tolerates_broken_entries(tmp_path):
    proj = tmp_path / "p.mapchar"
    proj.write_text(
        '{"version": 99, "current": 0, "entries": ['
        '{"kind": "file", "name": "x", "path": "x.bin"}, {"kind": "nonsense"}, '
        '{"kind": "block", "name": "b", "parent": 7, "unknown": 1}]}'
    )
    loaded = load_project(str(proj))
    assert [e.name for e in loaded.entries] == ["x"]
    assert loaded.current is loaded.entries[0]
    assert any("newer" in w for w in loaded.warnings)
    assert any("dropped" in w for w in loaded.warnings)


def test_case_recovery(tmp_path):
    (tmp_path / "Rom.BIN").write_bytes(b"x")
    ws_entries = [Entry(EntryKind.FILE, "r", str(tmp_path / "rom.bin"))]
    d = project_dict(ws_entries, None, str(tmp_path))
    proj = tmp_path / "p.mapchar"
    import json

    proj.write_text(json.dumps(d))
    loaded = load_project(str(proj))
    assert os.path.basename(loaded.entries[0].path) == "Rom.BIN"


def test_unopened_blocks_keep_their_strings_over_a_save(tmp_path):
    """A block never activated still has its string state written back.

    The loader parks them on the entry, and only the first extraction moves them
    into a document — so a save that read documents alone dropped the state of
    every block the session never looked at.
    """
    rom = tmp_path / "rom.bin"
    rom.write_bytes(bytes.fromhex("41 00 42 00"))
    ws = Workspace()
    f = ws.open_file(str(rom))
    for name, offsets in (("one", (0, 2)), ("two", (2, 4))):
        ws.add(
            Entry(
                EntryKind.BLOCK,
                name,
                str(rom),
                parent=f,
                config=BlockConfig(RangeSource(*offsets), EndToken(), "main"),
            )
        )
    ws.entries[1].pending_strings = {0: StringState("X[end]", Status.EDITED, "why")}
    ws.entries[2].pending_strings = {0: StringState("Y[end]", Status.REVIEW)}
    proj = tmp_path / "p.mapchar"
    save_project(str(proj), ws.entries, None)

    # Loaded, nothing activated, saved again: still both blocks' state.
    loaded = load_project(str(proj))
    assert [e.pending_strings for e in loaded.entries[1:]] == [
        {0: StringState("X[end]", Status.EDITED, "why")},
        {0: StringState("Y[end]", Status.REVIEW, "")},
    ]
    again = tmp_path / "q.mapchar"
    save_project(str(again), loaded.entries, None)
    back = load_project(str(again))
    assert back.entries[1].pending_strings[0].original == "X[end]"
    assert back.entries[1].pending_strings[0].notes == "why"
    assert back.entries[2].pending_strings[0].status is Status.REVIEW


def test_document_strings_win_over_pending_and_a_drop_stashes_them(tmp_path):
    rom = tmp_path / "rom.bin"
    rom.write_bytes(bytes.fromhex("41 00 42 00"))
    ws = Workspace()
    f = ws.open_file(str(rom))
    cfg = BlockConfig(RangeSource(0, 4), EndToken(), "main")
    b = ws.add(Entry(EntryKind.BLOCK, "b", str(rom), parent=f, config=cfg))
    ex = extract(rom.read_bytes(), cfg, table_set(ABC_TABLE, "main"))
    b.doc = Document(rom.read_bytes(), PipelineContext(), True, strings=ex.strings)
    b.doc.strings[0].original = "live"
    b.doc.strings[0].original_digest = WAS
    b.doc.strings[0].refresh_status()
    b.pending_strings = {0: StringState("stale")}
    assert project_dict([f, b], None, str(tmp_path))["entries"][1]["strings"] == [
        {"i": 0, "o": "live", "h": f"{WAS:08X}", "s": "edited"},
        {"i": 1, "o": "B[end]", "h": f"{bits_digest(format(0x4200, '016b')):08X}"},
    ]
    # Dropping the document keeps what only it held.
    ws.drop_document(b)
    assert b.doc is None
    assert b.pending_strings[0] == StringState("live", Status.EDITED, digest=WAS)


def test_invalidate_path_spares_the_saver_and_the_dirty(tmp_path):
    rom = tmp_path / "rom.bin"
    rom.write_bytes(b"abcd")
    ws = Workspace()
    saver = ws.open_file(str(rom))
    borrower = ws.add(Entry(EntryKind.FILE, "borrowed", str(rom)))
    edited = ws.add(Entry(EntryKind.FILE, "edited", str(rom)))
    elsewhere = ws.add(Entry(EntryKind.FILE, "other", str(tmp_path / "other.bin")))
    for e in (saver, borrower, edited, elsewhere):
        e.doc = Document(b"abcd", PipelineContext(), True)
    ws.stamp(edited)
    ws.invalidate_path(str(rom), keep=saver)
    assert borrower.doc is None
    assert saver.doc is not None and edited.doc is not None
    assert elsewhere.doc is not None


def test_close_takes_the_next_entry_then_the_previous():
    ws = Workspace()
    a, b, c = (ws.add(Entry(EntryKind.FILE, n, f"/tmp/{n}")) for n in "abc")
    ws.add(Entry(EntryKind.BOOKMARK, "bm", "/tmp/c", parent=c))
    ws.set_current(b)
    ws.close(b)
    assert ws.current is c  # the row that took its place, not the first one
    ws.set_current(c)
    ws.close(c)
    assert ws.current is a  # nothing follows, so the one before it


def test_close_keeps_to_the_group_then_falls_back_to_string_data():
    ws = Workspace()
    f = ws.add(Entry(EntryKind.FILE, "f", "/tmp/f"))
    b = ws.add(Entry(EntryKind.BLOCK, "b", "/tmp/f", parent=f))
    t1, t2 = (ws.add(Entry(EntryKind.TABLE, n, f"/tmp/{n}")) for n in ("t1", "t2"))
    ws.set_current(b)
    ws.close(b)
    assert ws.current is f  # a table follows, but the file is the same group
    ws.set_current(t1)
    ws.close(t1)
    assert ws.current is t2
    ws.close(t2)
    assert ws.current is f  # no table left, so String Data


def test_replace_resets_empty_then_resets_full_and_sets_current_last():
    ws = Workspace()
    ws.set_current(ws.add(Entry(EntryKind.FILE, "old", "/tmp/old")))
    log: list[str] = []
    ws.on_reset.append(lambda: log.append(f"reset:{len(ws.entries)}"))
    ws.on_added.append(lambda e: log.append(f"added:{e.name}"))
    ws.on_current_changed.append(
        lambda e: log.append(f"current:{e.name if e else None}:{len(ws.entries)}")
    )
    fresh = [
        Entry(EntryKind.FILE, "one", "/tmp/1"),
        Entry(EntryKind.FILE, "two", "/tmp/2"),
    ]
    ws.replace(fresh, fresh[1])
    assert log == [
        "current:None:1",
        "reset:0",
        "reset:2",
        "current:two:2",
    ]


def test_dirty_changed_fires_only_on_the_transition():
    ws = Workspace()
    e = ws.add(Entry(EntryKind.FILE, "a", "/tmp/a"))
    seen: list[Entry] = []
    ws.on_dirty_changed.append(seen.append)
    ws.stamp(e)
    ws.stamp(e)  # already dirty: nothing changed
    assert len(seen) == 1
    ws.mark_saved(e)
    ws.mark_saved(e)
    assert len(seen) == 2


def test_missing_paths_and_relocate_follow_every_reference(tmp_path):
    here = tmp_path / "rom.bin"
    here.write_bytes(b"x")
    gone = tmp_path / "gone.bin"
    moved = tmp_path / "sub" / "renamed.bin"
    moved.parent.mkdir()
    moved.write_bytes(b"y")
    ws = Workspace()
    f = ws.open_file(str(gone))
    block = ws.add(Entry(EntryKind.BLOCK, "b", str(gone), parent=f))
    mark = ws.add(Entry(EntryKind.BOOKMARK, "bm", str(gone), parent=f))
    pair = ws.add(
        Entry(EntryKind.FILE, "named by hand", str(here), extra_paths=(str(gone),))
    )
    # One row per distinct missing file, however many entries name it.
    assert missing_paths(ws) == [str(gone)]
    touched = relocate_path(ws, str(gone), str(moved))
    assert set(touched) == {f, block, mark, pair}
    assert [e.path for e in (f, block, mark)] == [str(moved)] * 3
    assert f.name == "renamed.bin"  # it was named after the file
    assert pair.name == "named by hand" and pair.extra_paths == (str(moved),)
    assert missing_paths(ws) == []


def test_load_tolerates_a_missing_entry_list_and_a_boolean_current(tmp_path):
    proj = tmp_path / "p.mapchar"
    proj.write_text('{"version": 3, "current": true}')
    loaded = load_project(str(proj))
    assert loaded.entries == [] and loaded.current is None
    assert loaded.migrated_from is None and loaded.version == 3


def test_load_walks_migrations_forward(tmp_path, monkeypatch):
    """The step walk itself, over a scaffold that is empty at version 1."""
    from mapchar.project import projectfile

    def rename(data):
        for raw in data.get("entries", []):
            if "old_name" in raw:
                raw.setdefault("name", raw["old_name"])
                del raw["old_name"]
        return data

    monkeypatch.setattr(projectfile, "PROJECT_VERSION", 2)
    monkeypatch.setattr(projectfile, "_MIGRATIONS", {1: rename})
    proj = tmp_path / "p.mapchar"
    proj.write_text(
        '{"version": 1, "entries": [{"kind": "file", "old_name": "rom.bin"}]}'
    )
    loaded = projectfile.load_project(str(proj))
    assert [e.name for e in loaded.entries] == ["rom.bin"]
    assert loaded.migrated_from == 1 and loaded.version == 2
    assert not loaded.warnings  # an upgrade is a status line, never a warning
    # Already current: nothing to walk, and nothing to report.
    proj.write_text('{"version": 2, "entries": [{"kind": "file", "name": "r"}]}')
    assert projectfile.load_project(str(proj)).migrated_from is None


def test_a_project_file_with_a_byte_order_mark_still_loads(tmp_path):
    import json

    (tmp_path / "rom.bin").write_bytes(b"x")
    entries = [
        Entry(EntryKind.FILE, "ロム", str(tmp_path / "rom.bin")),
        Entry(EntryKind.TABLE, "かな", str(tmp_path / "kana.tbl")),
    ]
    d = project_dict(entries, None, str(tmp_path))
    proj = tmp_path / "p.mapchar"
    proj.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8-sig")
    loaded = load_project(str(proj))
    assert [e.name for e in loaded.entries] == ["ロム", "かな"]


def test_a_font_entry_from_an_older_project_is_dropped_by_name(tmp_path):
    """Glyph-sheet fonts are gone; a project that still has one loads without
    it, and says so rather than naming a kind nobody recognises."""
    import json

    proj = tmp_path / "p.mapchar"
    proj.write_text(
        json.dumps(
            {
                "version": PROJECT_VERSION,
                "entries": [
                    {"kind": "file", "name": "rom", "path": "rom.bin"},
                    {"kind": "font", "name": "font.png", "path": "font.png"},
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded = load_project(str(proj))
    assert [e.name for e in loaded.entries] == ["rom"]
    assert any("glyph-sheet fonts are gone" in w for w in loaded.warnings)


def test_retarget_files_moves_a_file_its_children_and_its_name():
    """A container edit's file list: the children go with it, keyed by the path
    that is about to change, and a row named after its file follows."""
    ws = Workspace()
    f = ws.open_file("/tmp/chip1.bin")
    block = ws.add(Entry(EntryKind.BLOCK, "b", "/tmp/chip1.bin", parent=f))
    mark = ws.add(Entry(EntryKind.BOOKMARK, "bm", "/tmp/chip1.bin", parent=f))
    elsewhere = ws.add(Entry(EntryKind.FILE, "other", "/tmp/other.bin"))
    touched = retarget_files(ws, f, ("/tmp/first.bin", "/tmp/second.bin"))
    assert touched == [f, block, mark] and elsewhere.path == "/tmp/other.bin"
    for e in (f, block, mark):
        assert e.path == "/tmp/first.bin" and e.extra_paths == ("/tmp/second.bin",)
    assert f.name == "first.bin"  # it was named after its file
    assert block.name == "b"  # a name of its own is not a file name
    # A row the user named keeps that name, and a kind that is not a file, and an
    # empty list, are not this function's business.
    f.name = "my rom"
    retarget_files(ws, f, ("/tmp/third.bin",))
    assert f.name == "my rom"
    assert retarget_files(ws, block, ("/tmp/x.bin",)) == []
    assert retarget_files(ws, f, ()) == []


# -- in-app table edits ------------------------------------------------------


def _abc_table(text: str = "A") -> Table:
    table = Table("main")
    table.add(TableEntry("01000001", TokenKind.TEXT, text))
    table.add(TableEntry("00000000", TokenKind.END, "[end]"))
    return table


def test_an_overlay_is_what_the_table_says_over_what_the_file_said():
    live = _abc_table("Z")
    live.add(TableEntry("01000010", TokenKind.TEXT, "B"))
    live.remove("00000000")
    assert overlay_of(_abc_table(), live) == {
        "01000001": "41=Z",
        "01000010": "42=B",
        "00000000": None,
    }
    # A table with no file is carried whole.
    assert overlay_of(None, _abc_table()) == {
        "01000001": "41=A",
        "00000000": "/00=[end]",
    }
    assert overlay_of(_abc_table(), _abc_table()) == {}


def test_a_table_entry_carries_its_edits_over_a_save_and_lays_them_back(tmp_path):
    """The promise of the overlay: the table file on disk is untouched, and the
    project puts the edits back over it when it is read again."""
    entry = Entry(EntryKind.TABLE, "main.tbl", str(tmp_path / "main.tbl"))
    adopt_table(entry, _abc_table())
    assert entry.table_overlay == {}
    entry.table.add(TableEntry("01000010", TokenKind.TEXT, "B"))
    entry.table.remove("00000000")
    capture_overlay(entry)
    assert entry.table_overlay == {"01000010": "42=B", "00000000": None}

    proj = tmp_path / "p.mapchar"
    save_project(str(proj), [entry], None)
    assert '"overlay"' in proj.read_text()
    back = load_project(str(proj)).entries[0]
    assert back.table_overlay == entry.table_overlay
    # Reading the file again lays them straight back on: the file still says A
    # and [end], the entry says A and B.
    adopt_table(back, _abc_table())
    assert sorted(back.table.entries) == ["01000001", "01000010"]
    assert back.table_overlay == entry.table_overlay  # unspent until Save As File


def test_a_table_with_no_file_is_carried_whole_and_read_back(tmp_path):
    entry = Entry(EntryKind.TABLE, "new.tbl", None, dialect="native")
    entry.table = _abc_table()
    capture_overlay(entry)
    proj = tmp_path / "p.mapchar"
    save_project(str(proj), [entry], None)
    # Nothing to read: the load makes the table from its name and the overlay.
    back = load_project(str(proj)).entries[0]
    assert back.table.id == "main" and back.file_table is None
    assert back.table.entries["01000001"].text == "A"


def test_a_renamed_table_keeps_its_id_when_its_file_is_missing(tmp_path):
    """The id is the project's, not the table's: a save made before the file
    was ever read — because it is not there — must still write it down."""
    entry = Entry(EntryKind.TABLE, "main.tbl", str(tmp_path / "gone.tbl"))
    entry.table_id = "renamed"
    entry.missing = True
    proj = tmp_path / "p.mapchar"
    save_project(str(proj), [entry], None)
    assert load_project(str(proj)).entries[0].table_id == "renamed"


def test_folding_the_overlay_empties_it(tmp_path):
    entry = Entry(EntryKind.TABLE, "main.tbl", str(tmp_path / "main.tbl"))
    adopt_table(entry, _abc_table())
    entry.table.add(TableEntry("01000010", TokenKind.TEXT, "B"))
    capture_overlay(entry)
    fold_overlay(entry)  # what Save As File leaves behind
    assert entry.table_overlay == {}
    assert entry.file_table.entries == entry.table.entries


def test_a_line_the_overlay_can_no_longer_parse_is_skipped(tmp_path):
    entry = Entry(EntryKind.TABLE, "main.tbl", str(tmp_path / "main.tbl"))
    entry.table_overlay = {"01000010": "nonsense!!"}
    adopt_table(entry, _abc_table())
    assert sorted(entry.table.entries) == ["00000000", "01000001"]


def test_a_bookmark_can_never_be_the_restored_current_entry(tmp_path):
    proj = tmp_path / "p.mapchar"
    proj.write_text(
        '{"version": 1, "current": 1, "entries": ['
        '{"kind": "file", "name": "r"}, '
        '{"kind": "bookmark", "name": "bm", "parent": 0, "offset": 4}]}'
    )
    loaded = load_project(str(proj))
    assert loaded.current is None and len(loaded.entries) == 2


def test_a_files_preview_scheme_is_saved_loaded_and_saved_again(tmp_path):
    """Three answers the Compression picker can hold, over two saves.

    Automatic is the default and is written as nothing at all; ``""`` is the
    preview switched off, which is a choice; an id no plugin provides is still
    what the file was set to, kept so a plugin that comes back arms it again.
    """
    ws = Workspace()
    names = ("auto", "off", "gone")
    for name, scheme in zip(names, (None, "", "gone_scheme"), strict=True):
        rom = tmp_path / f"{name}.bin"
        rom.write_bytes(b"\xff" * 4)
        ws.open_file(str(rom)).session.preview_scheme = scheme
    proj = tmp_path / "p.mapchar"
    save_project(str(proj), ws.entries, None, [])
    first = proj.read_text()
    assert '"preview_scheme": ""' in first
    assert '"preview_scheme": "gone_scheme"' in first

    loaded = load_project(str(proj)).entries
    assert [e.session.preview_scheme for e in loaded] == [None, "", "gone_scheme"]
    # And back out unchanged: a round trip neither invents a pick for the file
    # that has none nor drops the one nothing can read.
    save_project(str(proj), loaded, None, [])
    assert proj.read_text() == first


def test_a_renamed_plugin_id_is_forwarded_as_the_project_loads(tmp_path, monkeypatch):
    """Every id a project names goes through the alias table on the way in."""
    import json

    from mapchar.plugins.aliases import RENAMED

    monkeypatch.setitem(RENAMED, "old_flat", "raw")
    monkeypatch.setitem(RENAMED, "old_lz", "rle1")
    monkeypatch.setitem(RENAMED, "old_banked", "lorom")
    proj = tmp_path / "p.mapchar"
    proj.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [
                    {
                        "kind": "file",
                        "name": "r",
                        "container_id": "old_flat",
                        "compression_id": "old_lz",
                    },
                    {
                        "kind": "block",
                        "name": "b",
                        "parent": 0,
                        "config": "source=pointers start=$0 stop=$8 size=2 "
                        "stride=2 mapping=old_banked table=main",
                    },
                ],
            }
        )
    )
    f, b = load_project(str(proj)).entries
    assert (f.container_id, f.compression_id) == ("raw", "rle1")
    assert b.config.source.mapping_id == "lorom"  # the one stored inside the config


def test_repeated_block_names_are_numbered_on_load(tmp_path):
    """A dump, a translator file and an Atlas script name a string by its
    block, so two rows called the same could not be told apart on the way in."""
    rom = tmp_path / "rom.bin"
    rom.write_bytes(bytes.fromhex("41 00 42 00"))
    ws = Workspace()
    f = ws.open_file(str(rom))
    for _ in range(2):
        ws.add(
            Entry(
                EntryKind.BLOCK,
                "Script",
                str(rom),
                parent=f,
                config=BlockConfig(RangeSource(0, 4), EndToken(), "main"),
            )
        )
    ws.add(Entry(EntryKind.BOOKMARK, "Script", str(rom), parent=f))
    proj = tmp_path / "p.mapchar"
    save_project(str(proj), ws.entries, None)
    loaded = load_project(str(proj))
    assert [e.name for e in loaded.entries] == [
        "rom.bin",
        "Script",
        "Script (2)",
        "Script (3)",
    ]
    assert any("renamed Script (2)" in w for w in loaded.warnings)


# -- folders -------------------------------------------------------------------


def _foldered(rom: str) -> tuple[Workspace, dict[str, Entry]]:
    """A file holding a folder with a block, a bookmark and a folder with a
    block in it, plus a block and a bookmark straight under the file."""
    ws = Workspace()
    f = ws.open_file(rom)
    cfg = BlockConfig(RangeSource(0, 4), EndToken(), "main")

    def row(kind, name, folder=None, **fields):
        return ws.add(Entry(kind, name, rom, parent=f, folder=folder, **fields))

    loose = row(EntryKind.BLOCK, "loose", config=cfg)
    outer = row(EntryKind.FOLDER, "Outer")
    inner = row(EntryKind.FOLDER, "Inner", outer)
    deep = row(EntryKind.BLOCK, "deep", inner, config=cfg)
    near = row(EntryKind.BLOCK, "near", outer, config=cfg)
    mark = row(EntryKind.BOOKMARK, "mark", outer, bookmark_offset=2)
    top = row(EntryKind.BOOKMARK, "top")
    rows = dict(f=f, loose=loose, outer=outer, inner=inner, deep=deep, near=near)
    return ws, rows | dict(mark=mark, top=top)


def test_a_row_is_added_after_everything_its_folder_holds():
    ws, r = _foldered("/tmp/a.nes")
    names = [e.name for e in ws.entries]
    assert names == ["a.nes", "loose", "Outer", "Inner", "deep", "near", "mark", "top"]
    assert ws.contents(r["f"]) == [r["loose"], r["outer"], r["top"]]
    assert ws.contents(r["outer"]) == [r["inner"], r["near"], r["mark"]]
    assert ws.descendants(r["outer"]) == [r["inner"], r["deep"], r["near"], r["mark"]]
    assert ws.blocks_of(r["outer"]) == [r["deep"], r["near"]]
    assert len(ws.blocks_of(r["f"])) == 3


def test_closing_a_folder_takes_what_it_holds_and_leaves_the_rest():
    ws, r = _foldered("/tmp/a.nes")
    ws.set_current(r["deep"])
    removed = ws.close(r["outer"])
    assert set(removed) == {r["outer"], r["inner"], r["deep"], r["near"], r["mark"]}
    assert ws.entries == [r["f"], r["loose"], r["top"]]
    assert ws.current is r["loose"]  # never the folder, nor a bookmark


def test_moving_rows_lifts_what_they_hold_in_one_pass():
    ws, r = _foldered("/tmp/a.nes")
    order = moved(ws.entries, [r["outer"]], r["f"], r["loose"])
    assert [e.name for e in order] == [
        "a.nes", "Outer", "Inner", "deep", "near", "mark", "loose", "top",
    ]  # fmt: skip
    # No row to land in front of: last among what the container holds.
    r["top"].folder = r["inner"]
    order = moved(ws.entries, [r["top"]], r["inner"], None)
    assert [e.name for e in order][3:6] == ["Inner", "deep", "top"]


def test_the_tree_order_puts_each_row_after_its_holder():
    ws, r = _foldered("/tmp/a.nes")
    shuffled = list(reversed(ws.entries))
    assert tree_order(shuffled)[0] is r["f"]
    assert tree_order(ws.entries) == ws.entries
    # A loop of folders reaches no root, and its rows still come out.
    r["inner"].folder = r["near"]
    r["near"].kind = EntryKind.FOLDER
    r["near"].folder = r["inner"]
    assert set(tree_order(ws.entries)) == set(ws.entries)


def test_folders_round_trip_with_what_they_hold(tmp_path):
    rom = tmp_path / "rom.bin"
    rom.write_bytes(bytes.fromhex("41 00 42 00"))
    ws, r = _foldered(str(rom))
    proj = tmp_path / "p.mapchar"
    save_project(str(proj), ws.entries, None)
    raw = project_dict(ws.entries, None, None)["entries"]
    assert raw[2] == {"kind": "folder", "name": "Outer", "path": str(rom), "parent": 0}
    assert raw[3]["folder"] == 2 and "folder" not in raw[1]
    loaded = load_project(str(proj))
    assert [e.name for e in loaded.entries] == [e.name for e in ws.entries]
    f, loose, outer, inner, deep, near, mark, top = loaded.entries
    assert outer.kind is EntryKind.FOLDER and outer.parent is f
    assert (inner.folder, deep.folder, near.folder, mark.folder) == (
        outer,
        inner,
        outer,
        outer,
    )
    assert loose.folder is None and top.folder is None
    assert deep.parent is f  # a folder never changes a row's file
    assert not loaded.warnings


def test_a_folder_name_never_numbers_a_block_on_load(tmp_path):
    rom = tmp_path / "rom.bin"
    rom.write_bytes(bytes.fromhex("41 00 42 00"))
    ws = Workspace()
    f = ws.open_file(str(rom))
    folder = ws.add(Entry(EntryKind.FOLDER, "Names", str(rom), parent=f))
    cfg = BlockConfig(RangeSource(0, 4), EndToken(), "main")
    ws.add(
        Entry(EntryKind.BLOCK, "Names", str(rom), parent=f, folder=folder, config=cfg)
    )
    proj = tmp_path / "p.mapchar"
    save_project(str(proj), ws.entries, None)
    assert [e.name for e in load_project(str(proj)).entries] == [
        "rom.bin",
        "Names",
        "Names",
    ]


def test_a_broken_folder_reference_leaves_the_row_under_its_file(tmp_path):
    proj = tmp_path / "p.mapchar"
    proj.write_text(
        """{"version": 1, "entries": [
        {"kind": "file", "name": "a", "path": "a.bin"},
        {"kind": "file", "name": "b", "path": "b.bin"},
        {"kind": "folder", "name": "F", "path": "b.bin", "parent": 1},
        {"kind": "bookmark", "name": "wrong file", "path": "a.bin", "parent": 0,
         "folder": 2},
        {"kind": "bookmark", "name": "no folder", "path": "a.bin", "parent": 0,
         "folder": 9},
        {"kind": "bookmark", "name": "not a folder", "path": "a.bin", "parent": 0,
         "folder": 3},
        {"kind": "folder", "name": "gone", "path": "a.bin", "parent": 99},
        {"kind": "bookmark", "name": "in gone", "path": "a.bin", "parent": 0,
         "folder": 6},
        {"kind": "bookmark", "name": "in F", "path": "b.bin", "parent": 1,
         "folder": 2}
        ]}"""
    )
    loaded = load_project(str(proj))
    by_name = {e.name: e for e in loaded.entries}
    assert "gone" not in by_name  # its file is missing, so it is dropped
    for name in ("wrong file", "no folder", "not a folder", "in gone"):
        assert by_name[name].folder is None
    assert by_name["in F"].folder is by_name["F"]
    # Laid out with every row after what holds it.
    assert [e.name for e in loaded.entries] == [
        "a", "wrong file", "no folder", "not a folder", "in gone", "b", "F", "in F",
    ]  # fmt: skip


def test_the_payload_keeps_a_folder_with_what_was_copied_with_it():
    ws, r = _foldered("/tmp/a.nes")
    group = [r["outer"], *ws.descendants(r["outer"])]
    copied = entries_from_payload(entries_payload(group))
    outer, inner, deep, near, mark = copied
    assert outer.folder is None and outer.kind is EntryKind.FOLDER
    assert (inner.folder, deep.folder, near.folder, mark.folder) == (
        outer,
        inner,
        outer,
        outer,
    )
    # Copied alone, a row arrives loose for the paste to place.
    (alone,) = entries_from_payload(entries_payload([r["deep"]]))
    assert alone.folder is None


def test_a_version_1_block_whose_fixed_strings_stop_at_an_end_is_marked(tmp_path):
    """Version 2 hides a fixed string's end token, so the originals version 1
    saved for such a block are marked to be respelled when it is first read —
    and stay marked through a save that comes before that."""
    import json

    proj = tmp_path / "p.mapchar"
    fixed = "source=range start=$0 stop=$C type=fixed:6:stop table=main"
    proj.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [
                    {"kind": "file", "name": "r"},
                    {
                        "kind": "block",
                        "name": "names",
                        "parent": 0,
                        "config": fixed,
                        "strings": [{"i": 0, "o": "AB[end]"}],
                    },
                    {
                        "kind": "block",
                        "name": "text",
                        "parent": 0,
                        "config": "source=range start=$0 stop=$C type=end table=main",
                        "strings": [{"i": 0, "o": "AB[end]"}],
                    },
                ],
            }
        )
    )
    loaded = load_project(str(proj))
    assert loaded.migrated_from == 1 and loaded.version == 3
    names, text = loaded.entries[1:]
    assert names.fixed_ends_shown and not text.fixed_ends_shown
    saved = project_dict(loaded.entries, None, str(tmp_path))["entries"]
    assert saved[1]["fixed_ends_shown"] and "fixed_ends_shown" not in saved[2]
    proj.write_text(json.dumps(saved and {"version": 3, "entries": saved}))
    assert load_project(str(proj)).entries[1].fixed_ends_shown


# --- What tells an edited string from an untouched one ----------------------

DTE_TABLE = "@table main\n41=A\n42=B\n80=AB\n/00=[end]\n"
"""A table with a pair code, so text has more than one spelling in bytes."""


def test_a_string_re_encoded_to_other_bytes_still_says_the_original(registry):
    """Status goes by the bytes, but text that says the original again is
    untouched however it is spelled: a pair code encodes ``AB`` in one byte,
    so putting the original text back gives bytes the original never had."""
    rom = bytes.fromhex("41 42 00")
    cfg = BlockConfig(RangeSource(0, 3), EndToken(), "main")
    ts = table_set(DTE_TABLE, "main")
    before = extract(rom, cfg, ts, registry).strings
    assert before[0].original == "AB[end]" and not before[0].edited
    res, out = relayout(rom, cfg, ts, {0: "AB[end]"}, registry)
    assert res.ok, res.problems
    assert out == bytes.fromhex("80 00 FF")
    rec = extract(out, cfg, ts, registry).strings[0]
    rec.original, rec.original_digest = before[0].original, before[0].original_digest
    rec.refresh_status()
    assert rec.current_text() == "AB[end]" and rec.digest != rec.original_digest
    assert not rec.edited and rec.status is Status.UNTOUCHED
    # The other way round is what the checksum is for: a block read in the
    # table its translation is written in says other text over the same bytes.
    rec.original = "something else"
    rec.original_digest = rec.digest
    rec.refresh_status()
    assert not rec.edited and rec.status is Status.UNTOUCHED


def test_a_bit_level_string_is_told_by_the_bits_it_holds(registry):
    """A string that stops mid-byte shares that byte with the next, so only
    its own bits say whether it is still the original's."""
    ts = table_set("@table main\n%00001=A\n%00010=B\n/%00000=[end]\n", "main")
    rom = bytes.fromhex("08 04 00 80 40")
    cfg = BlockConfig(RangeSource(0, 5), EndToken(), "main")
    strings = extract(rom, cfg, ts, registry).strings
    assert [s.current_text() for s in strings] == ["A[end]", "B[end]"] * 2
    assert strings[1].start_bit % 8 and strings[2].start_bit % 8
    # Same bits, other bytes and other bit offsets: the same checksum.
    assert strings[0].digest == strings[2].digest == bits_digest("0000100000")
    assert strings[1].digest != strings[0].digest
    assert not any(s.edited for s in strings)
    # The string a neighbour's bits were taken from is another string.
    strings[1].original, strings[1].original_digest = "A[end]", strings[0].digest
    assert strings[1].edited
    strings[1].original = strings[1].current_text()
    assert not strings[1].edited


def test_a_string_record_with_a_broken_checksum_still_loads(tmp_path):
    """``h`` is the checksum of the bytes an original was taken from; one a
    build cannot read leaves the string going by its text, not a broken load.
    """
    proj = tmp_path / "p.mapchar"
    proj.write_text(
        f'{{"version": {PROJECT_VERSION}, "entries": ['
        '{"kind": "file", "name": "x", "path": "x.bin"}, '
        '{"kind": "block", "name": "b", "path": "x.bin", "parent": 0, '
        '"strings": [{"i": 0, "o": "A[end]", "h": "zz"}, '
        '{"i": 1, "o": "B[end]", "h": 42}, '
        '{"i": 2, "o": "C[end]", "h": ["nonsense"]}, '
        '{"i": 3, "o": "D[end]", "h": "0000FFFF"}]}]}'
    )
    loaded = load_project(str(proj))
    saved = loaded.entries[1].pending_strings
    assert [saved[i].digest for i in range(4)] == [None, None, None, 0xFFFF]


def test_a_block_s_remembered_room_is_saved_and_read_back(tmp_path):
    """The room a shortened string gave up is state, not configuration: it
    lives beside the block's record rather than in its ``@block`` line, and a
    block that was never opened keeps it over a save like everything else it
    carries."""
    rom = tmp_path / "rom.bin"
    rom.write_bytes(bytes.fromhex("41 00 42 00"))
    ws = Workspace()
    f = ws.open_file(str(rom))
    cfg = BlockConfig(RangeSource(0, 4), EndToken(), "main")
    kept = ws.add(Entry(EntryKind.BLOCK, "kept", str(rom), parent=f, config=cfg))
    ws.add(Entry(EntryKind.BLOCK, "plain", str(rom), parent=f, config=cfg))
    kept.room = 0x16
    records = project_dict(ws.entries, None, None)["entries"]
    assert records[1]["room"] == 0x16
    assert "room" not in records[2] and "room" not in records[1]["config"]

    proj = tmp_path / "p.mapchar"
    save_project(str(proj), ws.entries, None)
    loaded = load_project(str(proj))
    assert [e.room for e in loaded.entries[1:]] == [0x16, None]
    # Never opened, and saved again with the room it came in with.
    again = tmp_path / "q.mapchar"
    save_project(str(again), loaded.entries, None)
    assert load_project(str(again)).entries[1].room == 0x16


def test_a_block_s_room_that_does_not_read_as_an_address_loads_as_none(tmp_path):
    proj = tmp_path / "p.mapchar"
    proj.write_text(
        f'{{"version": {PROJECT_VERSION}, "entries": ['
        '{"kind": "file", "name": "x", "path": "x.bin"}, '
        '{"kind": "block", "name": "b", "path": "x.bin", "parent": 0, '
        '"room": "nonsense"}]}'
    )
    assert load_project(str(proj)).entries[1].room is None


def test_a_version_2_block_that_reads_several_strings_a_pointer_is_marked(
    tmp_path, registry
):
    """Version 3 gives every string of a pointer's run a row, so the strings
    version 2 saved for such a block are whole runs: marked on load, and cut
    apart over the strings the block reads as now."""
    import json

    from mapchar.core.block import Status
    from mapchar.project.entry import unjoined_states
    from mapchar.project.formats.blockspec import parse_config

    spec = "source=list addresses=$0,$2 size=2 type=end table=main spp=2"
    proj = tmp_path / "p.mapchar"
    proj.write_text(
        json.dumps(
            {
                "version": 2,
                "entries": [
                    {"kind": "file", "name": "r"},
                    {
                        "kind": "block",
                        "name": "menu",
                        "parent": 0,
                        "config": spec,
                        "strings": [
                            {"i": 0, "o": "A[end]B[end]"},
                            {"i": 1, "o": "AA[end]\nB[end]", "s": "done", "n": "hm"},
                        ],
                    },
                    {
                        "kind": "block",
                        "name": "text",
                        "parent": 0,
                        "config": spec.replace(" spp=2", ""),
                        "strings": [{"i": 0, "o": "A[end]"}],
                    },
                ],
            }
        )
    )
    loaded = load_project(str(proj))
    assert loaded.migrated_from == 2 and loaded.version == 3
    menu, text = loaded.entries[1:]
    assert menu.runs_joined and not text.runs_joined
    saved = project_dict(loaded.entries, None, str(tmp_path))["entries"]
    assert saved[1]["runs_joined"] and "runs_joined" not in saved[2]

    ts = table_set(ABC_TABLE, "main")
    data = bytes.fromhex("04 00 08 00 41 00 42 00 43 00 42 00")
    config = parse_config(spec)
    strings = extract(data, config, ts, registry).strings
    states = unjoined_states(menu.pending_strings, strings, config, ts)
    assert [states[i].original for i in range(4)] == [
        "A[end]",
        "B[end]",
        "AA[end]\n",
        "B[end]",
    ]
    assert [states[i].status for i in (1, 2, 3)] == [
        Status.UNTOUCHED,
        Status.DONE,
        Status.DONE,
    ]
    assert [states[i].notes for i in (2, 3)] == ["hm", ""]

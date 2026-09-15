from __future__ import annotations

import os

from helpers import ABC_TABLE, table_set
from mapchar.core.block import BlockConfig, EndToken, RangeSource, Status
from mapchar.core.context import PipelineContext
from mapchar.core.document import Document
from mapchar.core.font import Font
from mapchar.core.table import Entry as TableEntry
from mapchar.core.table import Table, TokenKind
from mapchar.pipeline.extract import extract
from mapchar.project.projectfile import load_project, project_dict, save_project
from mapchar.project.tables import (
    adopt_table,
    capture_overlay,
    fold_overlay,
    overlay_of,
)
from mapchar.project.workspace import (
    Entry,
    EntryKind,
    StringState,
    Workspace,
    missing_paths,
    relocate_path,
    retarget_files,
)


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
    b.doc.strings[1].translation = "C[end]"
    b.doc.strings[1].status = Status.EDITED
    b.doc.strings[1].notes = "n"
    ws.add(Entry(EntryKind.BOOKMARK, "bm", str(rom), parent=f, bookmark_offset=2))
    ws.add(Entry(EntryKind.TABLE, "t", str(tmp_path / "t.tbl"), dialect="abcde"))
    ws.set_current(b)
    proj = tmp_path / "p.mapchar"
    save_project(str(proj), ws.entries, ws.current)
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
    assert loaded.strings[1][1].translation == "C[end]"
    assert loaded.strings[1][1].status is Status.EDITED
    assert loaded.entries[2].bookmark_offset == 2
    assert loaded.entries[3].dialect == "abcde"
    assert os.path.isabs(lb.path)


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
    """A block never activated still has its translations written back.

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
    assert back.strings[1][0].translation == "X[end]"
    assert back.strings[1][0].notes == "why"
    assert back.strings[2][0].status is Status.REVIEW


def test_document_strings_win_over_pending_and_a_drop_stashes_them(tmp_path):
    rom = tmp_path / "rom.bin"
    rom.write_bytes(bytes.fromhex("41 00 42 00"))
    ws = Workspace()
    f = ws.open_file(str(rom))
    cfg = BlockConfig(RangeSource(0, 4), EndToken(), "main")
    b = ws.add(Entry(EntryKind.BLOCK, "b", str(rom), parent=f, config=cfg))
    ex = extract(rom.read_bytes(), cfg, table_set(ABC_TABLE, "main"))
    b.doc = Document(rom.read_bytes(), PipelineContext(), True, strings=ex.strings)
    b.doc.strings[0].translation = "live"
    b.pending_strings = {0: StringState("stale")}
    assert project_dict([f, b], None, str(tmp_path))["entries"][1]["strings"] == [
        {"i": 0, "t": "live"}
    ]
    # Dropping the document keeps what only it held.
    ws.drop_document(b)
    assert b.doc is None and b.pending_strings == {0: StringState("live")}


def test_invalidate_path_spares_the_saver_and_the_dirty(tmp_path):
    rom = tmp_path / "rom.bin"
    rom.write_bytes(b"abcd")
    ws = Workspace()
    saver = ws.open_file(str(rom))
    borrower = ws.add(Entry(EntryKind.FONT, "sheet", str(rom)))
    edited = ws.add(Entry(EntryKind.FONT, "edited", str(rom)))
    elsewhere = ws.add(Entry(EntryKind.FONT, "other", str(tmp_path / "other.bin")))
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


def test_replace_resets_empty_then_adds_each_and_sets_current_last():
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
        "added:one",
        "added:two",
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
    sheet = ws.add(Entry(EntryKind.FONT, "gone.bin", str(gone)))
    sheet.font = Font(str(gone))
    # One row per distinct missing file, however many entries name it.
    assert missing_paths(ws) == [str(gone)]
    touched = relocate_path(ws, str(gone), str(moved))
    assert set(touched) == {f, block, mark, pair, sheet}
    assert sheet.font.path == str(moved)  # the sheet's own record follows too
    assert [e.path for e in (f, block, mark)] == [str(moved)] * 3
    assert f.name == "renamed.bin"  # it was named after the file
    assert pair.name == "named by hand" and pair.extra_paths == (str(moved),)
    assert missing_paths(ws) == []


def test_load_tolerates_a_missing_entry_list_and_a_boolean_current(tmp_path):
    proj = tmp_path / "p.mapchar"
    proj.write_text('{"version": 1, "current": true}')
    loaded = load_project(str(proj))
    assert loaded.entries == [] and loaded.current is None
    assert loaded.migrated_from is None and loaded.version == 1


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
        Entry(EntryKind.FONT, "かな", str(tmp_path / "font.png"), font=Font(None)),
    ]
    d = project_dict(entries, None, str(tmp_path))
    proj = tmp_path / "p.mapchar"
    proj.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8-sig")
    loaded = load_project(str(proj))
    assert [e.name for e in loaded.entries] == ["ロム", "かな"]


def test_a_font_alphabet_is_composed_when_the_project_is_read(tmp_path):
    import json
    import unicodedata

    chars = unicodedata.normalize("NFD", "あが")
    entries = [
        Entry(
            EntryKind.FONT,
            "f",
            str(tmp_path / "f.png"),
            font=Font(None, chars=chars, glyphs={chars[1:]: 3}),
        )
    ]
    proj = tmp_path / "p.mapchar"
    proj.write_text(
        json.dumps(project_dict(entries, None, str(tmp_path)), ensure_ascii=False),
        encoding="utf-8",
    )
    font = load_project(str(proj)).entries[0].font
    assert font.chars == "あが" and font.units == ("あ", "が")
    assert font.glyphs == {"が": 3}  # the override key is composed too


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

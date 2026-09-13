from __future__ import annotations

import os

from helpers import table_set
from mapchar.core.block import BlockConfig, EndToken, RangeSource, Status
from mapchar.core.context import PipelineContext
from mapchar.core.document import Document
from mapchar.pipeline.extract import extract
from mapchar.project.projectfile import load_project, project_dict, save_project
from mapchar.project.workspace import Entry, EntryKind, Workspace


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
    assert ws.current is t
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
    ts = table_set("@table main\n41=A\n42=B\n/00=[end]\n", "main")
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

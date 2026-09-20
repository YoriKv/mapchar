"""Text in and out of the window: the translator formats, the script and the
Cartographer and Atlas files, and the dialog that says what an import will do
before any of it lands."""

from __future__ import annotations

from pathlib import Path

from conftest import ROOT
from helpers import texts
from mapchar.core.block import RangeSource, Status
from window_helpers import ABCDE_TABLE, ab_ba_rom, add_block, open_rom_and_table


def test_import_export_and_find_replace(window, tmp_path):
    data = ab_ba_rom(0)
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "D", RangeSource(0, 6))
    window._on_translation_edited(0, "B[end]")
    tsv = tmp_path / "d.tsv"
    window.export_file(str(tsv), "tsv")
    assert "D/0\t$0\tAB[end]\tB[end]\tedited" in tsv.read_text()
    po = tmp_path / "d.po"
    window.export_file(str(po), "po")
    window._on_translation_edited(0, "")  # blank: the original again
    assert not block.doc.strings[0].edited
    window.import_file(str(po), "po", confirm=False)
    assert block.doc.strings[0].current_text() == "B[end]"
    window.undo_stack.undo()
    assert not block.doc.strings[0].edited
    # Script import through the native writer.
    from helpers import translated
    from mapchar.project.formats.script import DumpMode, write_script

    strings, _ = translated(data, block.config, window._table_set(), {1: "A[end]"})
    script = tmp_path / "s.txt"
    script.write_text(
        write_script([("D", block.config, strings)], DumpMode.TRANSLATIONS)
    )
    window.import_file(str(script), "script", confirm=False)
    assert block.doc.strings[1].current_text() == "A[end]"
    assert block.doc.strings[1].status is Status.EDITED
    # Replace all over the strings' text.
    window._fr_replace_all("A", "B", True)
    assert block.doc.strings[1].current_text() == "B[end]"
    assert block.doc.strings[0].current_text() == "BB[end]"
    # Hex panel overtype path.
    window.show()
    window.hex_dock.show()
    window._sync_hex_panel()
    assert "000000  42 42 00" in window.hex_panel.view.toPlainText()
    window.hex_panel.at.setText("2")
    window.hex_panel.bytes.setText("42 00")
    window.hex_panel._on_apply()
    assert file_entry.doc.data[:4] == bytes.fromhex("42 42 42 00")


def test_cartographer_and_atlas_import(window, tmp_path):
    rom = tmp_path / "c.bin"
    rom.write_bytes(bytes.fromhex("41 42 00 42 00") + b"\xff" * 8)
    (tmp_path / "main.tbl").write_text(ABCDE_TABLE)
    (tmp_path / "cmd.txt").write_text(
        "#BLOCK NAME: Intro\n#TYPE: NORMAL\n#METHOD: RAW\n#SCRIPT START: 0\n"
        "#SCRIPT STOP: $5\n#TABLE: main.tbl\n#COMMENTS: No\n#END BLOCK\n"
    )
    window.open_rom(str(rom))
    created = window.import_cartographer(str(tmp_path / "cmd.txt"))
    assert [e.name for e in created] == ["Intro"]
    block = created[0]
    assert block.config.table_id == "main"
    assert texts(block.doc.strings) == ["AB[end]", "B[end]"]
    (tmp_path / "atlas.txt").write_text(
        '#VAR(T, TABLE)\n#ADDTBL("main.tbl", T)\n#ACTIVETBL(T)\n#JMP($0, $4)\nBA[end]\n'
        "#JMP($3, $4)\nA[end]\n"
    )
    assert window.import_atlas(str(tmp_path / "atlas.txt")) == 2
    assert block.doc.strings[0].current_text() == "BA[end]"
    assert block.doc.strings[1].current_text() == "A[end]"
    assert window._string_at(3) is block.doc.strings[1]


def test_cartographer_import_strips_the_header(window, tmp_path):
    from mapchar.plugins.builtins.containers import NES_MAGIC

    header = NES_MAGIC + bytes([1, 0, 0, 0]) + b"\x00" * 8
    rom = tmp_path / "h.nes"
    rom.write_bytes(header + bytes.fromhex("41 42 00 42 00") + b"\xff" * 8)
    (tmp_path / "main.tbl").write_text(ABCDE_TABLE)
    (tmp_path / "cmd.txt").write_text(
        "#BLOCK NAME: Intro\n#TYPE: NORMAL\n#METHOD: RAW\n#SCRIPT START: $10\n"
        "#SCRIPT STOP: $15\n#TABLE: main.tbl\n#COMMENTS: No\n#END BLOCK\n"
    )
    window.open_rom(str(rom))
    block = window.import_cartographer(str(tmp_path / "cmd.txt"))[0]
    assert block.config.source == RangeSource(0, 5)
    assert texts(block.doc.strings) == ["AB[end]", "B[end]"]


def test_a_shift_jis_script_is_read_and_says_so(window, tmp_path):
    path = tmp_path / "commands.txt"
    path.write_bytes("#BLOCK ソ\n".encode("cp932"))
    text, notices = window._read_text(str(path))
    assert text is not None and "ソ" in text
    assert notices == ["commands.txt is not UTF-8; read as cp932"]


def test_a_utf8_script_is_read_without_a_notice(window, tmp_path):
    path = tmp_path / "commands.txt"
    path.write_text("#BLOCK ソ\n", encoding="utf-8")
    assert window._read_text(str(path)) == ("#BLOCK ソ\n", [])


def test_an_unreadable_script_reports_nothing_read(window, tmp_path):
    assert window._read_text(str(tmp_path / "missing.txt")) == (None, [])


def test_importing_is_live_on_a_file_because_it_creates_blocks(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, b"AB\x00")
    window._activate_entry(entry)
    assert window.import_action.isEnabled()
    assert window.export_action.isEnabled()


# --- an import says what it will do, and waits -------------------------------


def _tsv(path: Path, *rows: str) -> Path:
    path.write_text(
        "id\taddress\toriginal\ttranslation\tstatus\tnotes\n" + "".join(rows),
        encoding="utf-8",
    )
    return path


def test_an_import_is_confirmed_before_anything_lands(window, tmp_path, monkeypatch):
    """The dialog is shown the plan, not the result: cancelling leaves the
    strings as they were, and the same file imports once it is accepted."""
    data = ab_ba_rom(0)
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "D", RangeSource(0, 6))
    tsv = _tsv(tmp_path / "d.tsv", "D/0\t$0\tAB[end]\tBB[end]\tedited\t\n")

    seen: list = []
    monkeypatch.setattr(
        "mapchar.ui.dialogs.ImportDialog.exec",
        lambda self: seen.append(self._summary_for(False)) or 0,  # Rejected
    )
    window.import_file(str(tsv), "delimited")
    assert not block.doc.strings[0].edited
    summary = seen[0]
    assert summary.kind == "Translator table"
    assert [(b.name, b.strings) for b in summary.blocks] == [("D", 1)]

    monkeypatch.setattr("mapchar.ui.dialogs.ImportDialog.exec", lambda self: 1)
    window.import_file(str(tsv), "delimited")
    assert block.doc.strings[0].current_text() == "BB[end]"


def test_a_dropped_translator_file_is_confirmed_like_any_other_import(
    window, tmp_path, monkeypatch
):
    """A drop's kind is a guess from a suffix, so the drop is the path that
    most needs to say what it is about to do."""
    data = ab_ba_rom(0)
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "D", RangeSource(0, 6))
    tsv = _tsv(tmp_path / "d.tsv", "D/0\t$0\tAB[end]\tBB[end]\tedited\t\n")
    shown: list[str] = []
    monkeypatch.setattr(
        "mapchar.ui.dialogs.ImportDialog.exec",
        lambda self: shown.append(self.windowTitle()) or 1,
    )
    window._open_dropped(str(tsv), "delimited")
    assert shown == ["Import d.tsv"]
    assert block.doc.strings[0].current_text() == "BB[end]"


def test_force_on_the_dialog_takes_back_what_drifted(window, tmp_path, monkeypatch):
    """The only way to an original the project has moved past, and the reason
    the dialog re-plans rather than filtering what it already drew."""
    data = ab_ba_rom(0)
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "D", RangeSource(0, 6))
    tsv = _tsv(tmp_path / "d.tsv", "D/0\t$0\tmoved on[end]\tBB[end]\tedited\t\n")

    plans: list = []

    def accept_forced(self):
        plans.append(self._summary_for(False))
        self.force.setChecked(True)
        plans.append(self._summary_for(True))
        return 1

    monkeypatch.setattr("mapchar.ui.dialogs.ImportDialog.exec", accept_forced)
    window.import_file(str(tsv), "delimited")
    assert plans[0].skipped == ["D/0: original changed"] and not plans[0].blocks
    assert not plans[1].skipped and plans[1].blocks
    assert block.doc.strings[0].current_text() == "BB[end]"


def test_a_script_import_creates_its_block_and_lands_its_strings(
    window, tmp_path, monkeypatch
):
    """One pass, not two: the block the script carries is created and then the
    script is planned again over it, which is what the summary promised."""
    from helpers import translated
    from mapchar.project.formats.script import DumpMode, write_script

    data = ab_ba_rom(0)
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "D", RangeSource(0, 6))
    cfg = block.config
    strings, _ = translated(data, cfg, window._table_set(), {0: "BB[end]"})
    script = tmp_path / "s.txt"
    script.write_text(write_script([("Fresh", cfg, strings)], DumpMode.TRANSLATIONS))

    monkeypatch.setattr("mapchar.ui.dialogs.ImportDialog.exec", lambda self: 1)
    window.import_file(str(script), "script")
    made = next(e for e in window.workspace.entries if e.name == "Fresh")
    assert made.doc.strings[0].current_text() == "BB[end]"
    # And the whole of it undoes at once, block and text together.
    window.undo_stack.undo()
    assert not any(e.name == "Fresh" for e in window.workspace.entries)


def test_the_dump_tool_writes_a_project_s_blocks_as_a_script(window, tmp_path):
    """Dump is a development tool now, not a feature: it has no menu entry, and
    this is what keeps it working for the fixture comparisons."""
    import importlib.util

    data = ab_ba_rom(0)
    file_entry = open_rom_and_table(window, tmp_path, data)
    add_block(window, file_entry, "b", RangeSource(0, 6))
    project = tmp_path / "p.mapchar"
    assert window._write_project(str(project))

    spec = importlib.util.spec_from_file_location(
        "dump_script",
        ROOT / "tools" / "dump_script.py",
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    out = tmp_path / "dump.txt"
    assert tool.main([str(project), "-o", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "@mapchar script 1" in text
    assert '@block "b"' in text
    assert "@string 1 at $3-$6\nBA[end]\n" in text


def test_the_block_bar_s_export_button_shows_the_file_menu_s_export_menu(
    window, tmp_path
):
    """One QMenu, two places it is shown from, so neither can offer a format
    the other does not."""
    data = bytes.fromhex("41 42 00")
    file_entry = open_rom_and_table(window, tmp_path, data)
    add_block(window, file_entry, "b", RangeSource(0, 3))
    assert window.block_export.menu() is window.export_menu
    rows = [a.text().replace("&", "") for a in window.export_menu.actions() if a.text()]
    assert rows == [
        "TSV…",
        "CSV…",
        "PO…",
        "Cartographer Command File…",
        "Atlas Script…",
    ]

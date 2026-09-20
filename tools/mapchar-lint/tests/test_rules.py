"""What each rule fires on, and — as often — what it does not.

The negative cases carry most of the weight here. A linter over a format this
tolerant is only useful while it is quiet on correct files: a file's reading of
`start=$0 stop=$0`, a table named by a charset, a banked mapping built on
demand, a list source's addresses in the fixed bank. Each has a test below so
it stays legal.
"""

from __future__ import annotations

import json

from mapchar_lint.cli import main
from mapchar_lint.known import KnownIds, load_snapshot
from mapchar_lint.linter import lint

LIST_BLOCK = (
    "source=list addresses=$2DB,$310 size=2 endian=little mapping=linear "
    "offset=16384 bank=0 type=end table=main spp=2 mode=packed"
)
RECORDS = (
    "source=range start=$8533 stop=$857B type=pascal:1 table=main "
    "skips=$8533>$8535,$853D>$853F"
)


def config(**words) -> str:
    """The clean block's line with words replaced (None removes one)."""
    base = {
        "source": "pointers",
        "start": "$100",
        "stop": "$120",
        "size": "2",
        "stride": "2",
        "endian": "little",
        "mapping": "linear",
        "offset": "0",
        "bank": "0",
        "type": "end",
        "table": "main",
    }
    base.update(words)
    return " ".join(f"{k}={v}" for k, v in base.items() if v is not None)


# -- the document ----------------------------------------------------------
def test_a_clean_project_is_silent(project, doc, files):
    assert project(doc(), files) == []


def test_unreadable_file_is_fatal(tmp_path, ids):
    report = lint(str(tmp_path / "nope.mapchar"), ids)
    assert report.fatal and [d.code for d in report.diagnostics] == ["F001"]


def test_malformed_json_is_fatal(tmp_path, ids):
    path = tmp_path / "bad.mapchar"
    path.write_text('{"entries": [', encoding="utf-8")
    report = lint(str(path), ids)
    assert report.fatal and [d.code for d in report.diagnostics] == ["F002"]


def test_not_utf8_is_fatal(tmp_path, ids):
    path = tmp_path / "bad.mapchar"
    path.write_bytes(b'{"entries": ["\xff"]}')
    assert [d.code for d in lint(str(path), ids).diagnostics] == ["F002"]


def test_the_document_must_be_an_object_with_an_entries_array(tmp_path, ids):
    path = tmp_path / "bad.mapchar"
    path.write_text("[]", encoding="utf-8")
    assert [d.code for d in lint(str(path), ids).diagnostics] == ["F003"]
    path.write_text('{"version": 2, "entries": {}}', encoding="utf-8")
    assert [d.code for d in lint(str(path), ids).diagnostics] == ["F004"]


def test_a_byte_order_mark_reads_and_is_noted(tmp_path, ids, doc, files):
    for name, size in files.items():
        target = tmp_path / name
        if isinstance(size, int):
            target.write_bytes(b"\x00" * size)
        else:
            target.write_text(size, encoding="utf-8")
    path = tmp_path / "bom.mapchar"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(doc()).encode())
    assert [d.code for d in lint(str(path), ids).diagnostics] == ["I105"]


def test_version(project, doc, files):
    d = doc()
    del d["version"]
    assert "W101" in project(d, files)
    assert "E102" in project(doc(version="2"), files)
    assert "W103" in project(doc(version=9), files)
    assert "I104" in project(doc(version=1), files)


def test_current(project, doc, files):
    assert "E111" in project(doc(current=9), files)
    assert "E113" in project(doc(current=True), files)
    mark = {"kind": "bookmark", "name": "b", "path": "rom.nes", "parent": 0}
    assert "W112" in project(doc(current=3, extra=[mark]), files)
    broken = {"kind": "sliced", "name": "x"}
    assert "E111" in project(doc(current=3, extra=[broken]), files)


def test_unknown_top_level_key(project, doc, files):
    assert "E114" in project(doc(glosary=[]), files)


def test_glossary(project, doc, files):
    assert "E121" in project(doc(glossary={}), files)
    codes = project(doc(glossary=[{"t": "Herb", "r": 1, "x": ""}, {"r": "?"}]), files)
    assert {"W124", "E123", "E122"} <= set(codes)
    assert project(doc(glossary=[{"t": "Herb", "r": "Kräuter", "n": ""}]), files) == []


# -- what drops an entry -------------------------------------------------------
def test_entry_shapes_that_drop_it(project, doc, files):
    cases = {
        "E201": ["not an entry"],
        "E202": [{"kind": "sliced", "name": "x"}],
        "E203": [{"kind": "font", "name": "x"}],
    }
    for code, extra in cases.items():
        assert code in project(doc(extra=extra), files), code
    assert "E202" in project(doc(extra=[{"name": "no kind"}]), files)


def test_values_the_reader_raises_on(project, doc, files):
    cases = {
        "E204": {"path": 7},
        "E205": {"extra_paths": [1]},
        "E206": {"session": ["x"]},
        "E209": {"compression_id": 3},
        "E210": {"session": {"config": 5}},
    }
    for code, update in cases.items():
        assert code in project(doc(file=update), files), code


def test_integers_the_reader_cannot_read_drop_the_entry(project, doc, files):
    assert "E207" in project(doc(file={"session": {"offset": "$10"}}), files)
    assert "E207" in project(doc(block={"box": {"width": "wide"}}), files)
    mark = {"kind": "bookmark", "name": "b", "path": "rom.nes", "parent": 0}
    assert "E207" in project(doc(extra=[{**mark, "offset": None}]), files)


def test_integers_the_reader_coerces_are_warned(project, doc, files):
    mark = {"kind": "bookmark", "name": "b", "path": "rom.nes", "parent": 0}
    codes = project(doc(extra=[{**mark, "offset": 3.7}]), files)
    assert "W213" in codes and "E207" not in codes
    assert "W213" in project(doc(file={"session": {"offset": "16"}}), files)


def test_strings_and_box_shapes_that_drop_a_block(project, doc, files):
    assert "E211" in project(doc(block={"strings": {"0": "x"}}), files)
    assert "E211" in project(doc(block={"strings": ["x"]}), files)
    assert "E211" in project(doc(block={"strings": [{"i": None}]}), files)
    assert "E212" in project(doc(block={"box": {"origin": 0}}), files)
    assert "E212" in project(doc(block={"box": {"effects": [1]}}), files)


def test_a_fatal_config_drops_the_block_and_is_reported_once(project, doc, files):
    codes = project(doc(block={"config": config(source="table")}), files)
    assert codes.count("E604") == 1
    assert "E111" in codes  # current named the block


# -- keys ------------------------------------------------------------------
def test_unknown_and_misplaced_keys(project, doc, files):
    assert "E214" in project(doc(block={"confg": "x"}), files)
    assert "W215" in project(doc(file={"offset": 3}), files)
    assert "W216" in project(doc(block={"name": 12}), files)
    assert "E217" in project(doc(file={"path": None}), files)
    assert "E218" in project(doc(file={"extra_paths": "b.bin"}), files)


def test_parent_and_folder_must_be_integers(project, doc, files):
    assert "E219" in project(doc(block={"parent": "0"}), files)
    assert "E219" in project(doc(block={"parent": True}), files)


def test_session(project, doc, files):
    assert "E261" in project(doc(file={"session": {"offest": 3}}), files)
    assert "I262" in project(doc(block={"session": {"config": config()}}), files)
    assert "W263" in project(doc(file={"session": {"view": "hex"}}), files)
    assert "W264" in project(doc(file={"session": {"resolve_pointers": 1}}), files)
    clean = {"session": {"view": "strings", "offset": 32768, "table_id": "main"}}
    assert project(doc(file=clean), files) == []


# -- the configuration line ------------------------------------------------
def test_config_lines_that_drop_the_block(project, doc, files):
    cases = {
        "E602": 5,
        "E603": config() + " stray",
        "E604": config(source="table"),
        "E605": config(size=None),
        "E606": config(start="$ZZ"),
        "E607": config(type="fixed"),
        "E608": config(mode="packd"),
        "E609": config(fill="$"),
        "E626": RECORDS + " header=-1",
    }
    for code, line in cases.items():
        assert code in project(doc(block={"config": line}), files), code


def test_config_words_that_are_ignored_or_misread(project, doc, files):
    cases = {
        "E610": config(tabel="main"),
        "E613": config(endian="Big"),
        "E617": config(size="0"),
        "E618": config(stride="0"),
        "W618": config(stride="1"),
        "W619": config(start="$120", stop="$100"),
        "E620": config(table=None),
        "W621": config(spp="0"),
        "W622": config(skips="$110>$110"),
        "W623": config(type="fixed:0"),
        "E624": config(type="fixed:4:stpo"),
    }
    for code, line in cases.items():
        assert code in project(doc(block={"config": line}), files), code
    list_line = LIST_BLOCK + " stride=2"
    assert "W611" in project(doc(block={"config": list_line}), files)
    assert "W612" in project(doc(block={"config": config() + " size=3"}), files)
    range_next = "source=range start=$100 stop=$120 type=next table=main"
    assert "W625" in project(doc(block={"config": range_next}), files)
    # A header is a range's; on any other source it is ignored and dropped.
    assert "W611" in project(doc(block={"config": config(header="2")}), files)
    assert "E626" in project(doc(block={"config": RECORDS + " header=300"}), files)
    # Skip ranges and a header both make the text non-contiguous, so the
    # block is written slotted whatever mode= says.
    headed = "source=range start=$8533 stop=$857B type=pascal:1 table=main header=2"
    for line in (RECORDS + " mode=packed", headed + " mode=packed"):
        assert "W627" in project(doc(block={"config": line}), files), line


def test_config_lines_the_samples_use_are_clean(project, doc, files):
    rom = {**files, "rom.nes": 0x40000}
    for line in (
        LIST_BLOCK,
        RECORDS,
        "source=range start=$811B stop=$8126 type=fixed:11 table=main",
        "source=nested start=$1000 stop=$1010 size=4 stride=8 endian=little "
        "mapping=linear offset=0 bank=0 null=$0 inner_size=2 inner_endian=little "
        "inner_null=$0 type=end table=main",
        "source=range start=$100 stop=$200 type=fixed:18:stop table=main "
        "fill=$FFFF show_end=end",
        "source=pointers start=$100 stop=$110 size=2 stride=2 endian=little "
        "mapping=linear offset=0 bank=0 type=pascal:1:tokens:big table=main "
        "realign=2:0 bound=$9000 mode=slotted lines=16 line_label=br",
    ):
        assert project(doc(block={"config": line}), rom) == [], line


def test_a_files_reading_is_only_a_setting(project, doc, files):
    empty = "source=range start=$0 stop=$0 type=end table=ascii"
    assert project(doc(file={"session": {"config": empty}}), files) == []
    no_table = "source=range start=$0 stop=$0 type=end"
    assert project(doc(file={"session": {"config": no_table}}), files) == []
    report = project.report(
        doc(file={"session": {"config": "source=table start=0"}}), files
    )
    assert [(d.code, d.severity.value) for d in report.diagnostics] == [
        ("E604", "warning")
    ]


# -- files on disk -----------------------------------------------------------
def test_missing_and_odd_files(project, doc, files, tmp_path):
    assert "E301" in project(doc(), {"main.tbl": files["main.tbl"]})
    (tmp_path / "dir.nes").mkdir()
    assert "E302" in project(doc(file={"path": "dir.nes"}), files)
    assert "W303" in project(doc(file={"path": "empty.nes"}), {**files, "empty.nes": 0})
    assert "E301" in project(doc(table={"path": "gone.tbl"}), files)


def test_paths_resolve_case_insensitively(project, doc, files):
    assert project(doc(file={"path": "ROM.NES"}), files) == []


def test_one_file_opened_twice(project, doc, files):
    twin = {"kind": "file", "name": "again", "path": "rom.nes"}
    assert "W305" in project(doc(extra=[twin]), files)


def test_addresses_past_the_end_of_the_file(project, doc, files):
    assert "E311" in project(doc(block={"config": config(stop="$10001")}), files)
    assert "E311" in project(doc(block={"config": config(bound="$20000")}), files)
    at_end = config(start="$FFF0", stop="$10000")
    assert project(doc(block={"config": at_end}), files) == []
    mark = {"kind": "bookmark", "name": "b", "path": "rom.nes", "parent": 0}
    assert "E312" in project(doc(extra=[{**mark, "offset": 0x20000}]), files)


def test_addresses_measure_the_joined_region(project, doc, files):
    joined = {**files, "hi.nes": 0x10000}
    line = config(start="$18000", stop="$18010")
    codes = project(
        doc(file={"extra_paths": ["hi.nes"]}, block={"config": line}), joined
    )
    assert codes == []


def test_compressed_data_is_not_measured(project, doc, files):
    line = config(start="$80000", stop="$80010")
    assert (
        project(doc(file={"compression_id": "lz2"}, block={"config": line}), files)
        == []
    )
    block = {"compression_id": "lz2", "slice_offset": 0x20000, "config": line}
    assert "E313" in project(doc(block=block), files)


def test_no_files_skips_the_disk(project, doc):
    assert "E301" not in project(doc(), {}, check_files=False)


# -- plugin ids ------------------------------------------------------------
def test_unknown_ids(project, doc, files):
    assert "E401" in project(doc(file={"container_id": "snes2"}), files)
    assert "E402" in project(doc(block={"compression_id": "lz9"}), files)
    assert "E403" in project(doc(block={"config": config(mapping="lorom2")}), files)
    assert "E404" in project(doc(table={"dialect": "thingy"}), files)


def test_an_id_that_is_another_stages_says_so(project, doc, files):
    report = project.report(doc(file={"container_id": "lz2"}), files)
    [finding] = [d for d in report.diagnostics if d.code == "E401"]
    assert "compression" in finding.message


def test_the_snapshot_says_the_weaker_thing(project, doc, files, ids):
    weak = KnownIds(**{**ids.__dict__, "authoritative": False, "source": "snapshot"})
    codes = project(doc(file={"container_id": "snes2"}), files, known=weak)
    assert "W401" in codes and "E401" not in codes


def test_renamed_and_default_ids(project, doc, files):
    assert "I406" in project(doc(file={"compression_id": "old-lz"}), files)
    assert "I407" in project(doc(file={"container_id": "raw"}), files)


def test_banked_mappings_are_built_on_demand(project, doc, files):
    line = config(mapping="banked:8000:4000")
    assert project(doc(block={"config": line}), files) == []


def test_pointer_size_the_mapping_does_not_offer(project, doc, files):
    line = config(mapping="gb", size="4", stride="4")
    assert "W408" in project(doc(block={"config": line}), files)


def test_the_projects_plugin_folder_provides_ids(project, doc, files):
    preset = {"plugins/compression/mine.toml": 'id = "my-lz"\nengine = "lzss"\n'}
    block = {"compression_id": "my-lz"}
    assert project(doc(block=block), {**files, **preset}) == []
    code = {"plugins/compression/mine.py": "# a code plugin\n"}
    codes = project(doc(block={"compression_id": "their-lz"}), {**files, **code})
    assert "W402" in codes and "E402" not in codes


# -- references between entries ----------------------------------------------
def test_parents(project, doc, files):
    assert "E501" in project(doc(block={"parent": None}), files)
    assert "E502" in project(doc(block={"parent": 9}), files)
    broken = {"kind": "sliced", "name": "x"}
    assert "E502" in project(doc(block={"parent": 3}, extra=[broken]), files)
    assert "E503" in project(doc(block={"parent": 2}), files)


def test_folders(project, doc, files):
    folder = {"kind": "folder", "name": "Menus", "path": "rom.nes", "parent": 0}
    assert project(doc(block={"folder": 3}, extra=[folder]), files) == []
    assert "W511" in project(doc(block={"folder": 9}), files)
    assert "W512" in project(doc(block={"folder": 2}), files)
    other = {"kind": "file", "name": "b", "path": "b.nes"}
    theirs = {**folder, "parent": 3}
    codes = project(
        doc(block={"folder": 4}, extra=[other, theirs]), {**files, "b.nes": 4}
    )
    assert "W513" in codes
    loop = [{**folder, "folder": 4}, {**folder, "name": "Inner", "folder": 3}]
    assert "W514" in project(doc(extra=loop), files)


def test_names_blocks_are_found_by(project, doc, files):
    twin = {
        "kind": "block",
        "name": "Dialogue",
        "path": "rom.nes",
        "parent": 0,
        "config": config(),
    }
    assert "W521" in project(doc(extra=[twin]), files)


def test_table_references(project, doc, files):
    assert "E524" in project(doc(block={"config": config(table="script")}), files)
    assert project(doc(block={"config": config(table="ascii")}), files) == []
    missing = {"main.tbl": files["main.tbl"], **{"rom.nes": files["rom.nes"]}}
    gone = doc(table={"path": "gone.tbl"}, block={"config": config(table="gone")})
    codes = project(gone, missing)
    assert "W524" in codes and "E524" not in codes


def test_table_ids_come_from_the_file(project, doc, files):
    native_no_id = "@mapchar table 1\n41=A\n"
    stem = {"rom.nes": files["rom.nes"], "main.tbl": native_no_id}
    assert project(doc(), stem) == []
    abcde = "@main\n41=A\n@names\n42=B\n"
    two = {"rom.nes": files["rom.nes"], "main.tbl": abcde}
    names_block = {
        "kind": "block",
        "name": "Names",
        "path": "rom.nes",
        "parent": 0,
        "config": config(table="names"),
    }
    assert project(doc(extra=[names_block]), two) == []
    renamed = doc(table={"table": "script"}, block={"config": config(table="script")})
    assert project(renamed, files) == []


def test_tables_named_alike(project, doc, files):
    again = {"kind": "table", "name": "copy.tbl", "path": "copy.tbl"}
    codes = project(doc(extra=[again]), {**files, "copy.tbl": files["main.tbl"]})
    assert "W522" in codes
    assert "E523" in project(doc(table={"table": "two words"}), files)


def test_session_tables_and_includes(project, doc, files):
    assert "W525" in project(doc(file={"session": {"table_id": "nope"}}), files)
    assert "E526" in project(doc(table={"includes": ["codes"]}), files)


# -- a block's records ---------------------------------------------------------
def test_block_without_config(project, doc, files):
    assert "W601" in project(doc(block={"config": None}), files)


def test_string_records(project, doc, files):
    records = [
        {"i": 0, "o": "Hi[end]"},
        {"o": "no index"},
        {"i": "x"},
        {"i": 1, "s": "finished"},
        {"i": 0, "o": "again"},
        {"i": 2, "orig": "x"},
        {"i": 3, "t": "Hola[end]"},
    ]
    codes = project(doc(block={"strings": records}), files)
    assert {"E641", "E642", "W643", "E646", "I644"} <= set(codes)
    clean = [{"i": 0, "o": "Hi[end]", "s": "done", "n": "checked"}]
    assert project(doc(block={"strings": clean}), files) == []
    assert "I645" in project(doc(block={"fixed_ends_shown": True}), files)


def test_text_box(project, doc, files):
    assert "E647" in project(doc(block={"box": 3}), files)
    assert "E648" in project(doc(block={"box": {"wdith": 3}}), files)
    box = {"effects": {"line": ["newline", 0], "page": ["flip", 0]}}
    assert "W649" in project(doc(block={"box": box}), files)
    good = {
        "width": 160,
        "height": 48,
        "line_height": 16,
        "letter_spacing": 0,
        "lines_per_page": 3,
        "chars_per_line": 0,
        "origin": [0, 0],
        "effects": {"line": ["newline", 0]},
    }
    assert project(doc(block={"box": good}), files) == []


def test_room(project, doc, files):
    assert project(doc(block={"room": 0x120}), files) == []
    assert "W652" in project(doc(block={"room": "soon"}), files)
    bounded = {
        "room": 0x120,
        "config": "source=pointers start=$100 stop=$120 size=2 stride=2 "
        "endian=little mapping=linear offset=0 bank=0 type=end table=main "
        "bound=$200",
    }
    # A bound set after a shortening leaves both, and that is no mistake.
    assert project(doc(block=bounded), files) == []
    ranged = {
        "room": 0x120,
        "config": "source=range start=$100 stop=$120 type=end table=main",
    }
    assert "W653" in project(doc(block=ranged), files)


def test_slot_keys(project, doc, files):
    assert "W650" in project(doc(block={"slice_offset": 4}), files)
    compressed = {"compression_id": "lz2", "spare_room": "leave"}
    assert "W651" in project(doc(block=compressed), files)


# -- a table's records ---------------------------------------------------------
def test_table_overlay_and_includes(project, doc, files):
    assert "E701" in project(doc(table={"overlay": ["41=A"]}), files)
    codes = project(doc(table={"overlay": {"41": "41=A", "01000010": 7}}), files)
    assert {"E702", "W703"} <= set(codes)
    assert "E704" in project(doc(table={"includes": "codes"}), files)
    selfish = {"table": "main", "includes": ["main"]}
    assert "E705" in project(doc(table=selfish), files)
    fileless = {"kind": "table", "name": "kanji", "table": "kanji"}
    assert "W706" in project(doc(extra=[fileless]), files)
    overlay = {**fileless, "overlay": {"01000011": "43=C"}}
    assert project(doc(extra=[overlay]), files) == []


# -- the command line ----------------------------------------------------------
def test_exit_status(tmp_path, doc, files, capsys):
    for name, body in files.items():
        if isinstance(body, int):
            (tmp_path / name).write_bytes(b"\x00" * body)
        else:
            (tmp_path / name).write_text(body, encoding="utf-8")
    good = tmp_path / "good.mapchar"
    good.write_text(json.dumps(doc()), encoding="utf-8")
    bad = tmp_path / "bad.mapchar"
    bad.write_text(json.dumps(doc(current=9)), encoding="utf-8")
    assert main([str(good), "--no-color"]) == 0
    assert main([str(bad), "--no-color"]) == 1
    assert main([str(bad), "--ignore", "E111", "--no-color"]) == 0
    assert main([str(tmp_path / "missing.mapchar")]) == 2
    assert main([str(tmp_path), "--no-color"]) == 1  # both files, one bad
    capsys.readouterr()
    main([str(bad), "--format", "json"])
    body = json.loads(capsys.readouterr().out)
    [finding] = body["reports"][0]["diagnostics"]
    assert finding["code"] == "E111" and finding["pointer"] == "/current"


def test_the_shipped_snapshot_loads():
    ids = load_snapshot()
    assert ids.usable and "linear" in ids.plugins["mappings"]
    assert ids.project_version >= 2

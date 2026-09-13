"""The window's side of the plugin folders: what a project brings with it, what
a refresh re-reads, and what it refuses to throw away.
"""

from __future__ import annotations

import pytest

from mapchar.core.block import RangeSource
from mapchar.plugins.base import Stage
from mapchar.plugins.discovery import TrustStore, discover, plugin_roots
from mapchar.plugins.registry import default_registry
from window_helpers import add_block, make_window, open_rom_and_table

THEIRS = (
    "from mapchar.plugins.base import PluginInfo, Stage\n"
    "class C:\n"
    "    info = PluginInfo('theirs', 'Theirs', Stage.COMPRESSION)\n"
    "    def decompress(self, data, ctx): return data\n"
    "def register(r): r.register(C())\n"
)


@pytest.fixture
def window(qtbot, monkeypatch):
    return make_window(qtbot, monkeypatch)


def wire_reload(window, asked: list[str]):
    """Give ``window`` the reload callback ``app.main`` hands it, over no user
    folder — so what the registry gains is the project's own doing."""

    def reload_plugins(project_dir):
        registry = default_registry()
        result = discover(
            registry,
            plugin_roots(None, project_dir),
            TrustStore(None),
            lambda path, digest: asked.append(path) or True,
        )
        return registry, result.issues

    window._reload_plugins = reload_plugins
    return reload_plugins


def test_a_projects_plugin_folder_loads_and_unloads_with_it(window, tmp_path):
    """The ``plugins/`` folder beside a project file belongs to that project: it
    is scanned as the project opens — through the same trust gate as the user's
    own — and dropped again when the next project replaces it."""
    folder = tmp_path / "plugins" / "compression"
    folder.mkdir(parents=True)
    (folder / "theirs.py").write_text(THEIRS)
    asked: list[str] = []
    wire_reload(window, asked)

    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    assert window.registry.plugin(Stage.COMPRESSION, "theirs") is None

    assert window.open_project(str(proj))
    assert window.registry.plugin(Stage.COMPRESSION, "theirs") is not None
    assert asked == [str(folder / "theirs.py")]

    window._new_project()
    assert window.registry.plugin(Stage.COMPRESSION, "theirs") is None


def test_refreshing_plugins_re_reads_the_clean_and_keeps_the_edited(window, tmp_path):
    """F5 is not a revert. A document holding unsaved edits keeps what it has —
    and so does its file, which the block settles its bytes through."""
    wire_reload(window, [])
    file_entry = open_rom_and_table(window, tmp_path, b"AB\x00BA\x00")
    edited = add_block(window, file_entry, "b", RangeSource(0, 3))
    clean = add_block(window, file_entry, "c", RangeSource(3, 6))
    window._activate_entry(edited)
    window._on_translation_edited(0, "B[end]")
    assert edited.dirty
    clean_doc = clean.doc

    window._refresh_plugins()

    assert edited.dirty and edited.doc.strings[0].translation == "B[end]"
    assert file_entry.doc is not None  # the parent the edits settle through
    assert clean.doc is not clean_doc  # re-read through the new registry
    assert "unsaved edits kept" in window.statusBar().currentMessage()


def test_a_failed_project_plugin_is_reported_when_the_project_opens(
    window, tmp_path, monkeypatch
):
    """A project's folder is scanned as it opens, so what did not load is said
    then — the same dialog a startup failure raises."""
    folder = tmp_path / "plugins" / "compression"
    folder.mkdir(parents=True)
    (folder / "broken.py").write_text("raise RuntimeError('boom')\n")
    wire_reload(window, [])
    shown: list[str] = []
    window._plugin_issues = []

    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    # The report is a TextDialog, which the modal-answering fixture does not
    # cover: record what it was handed and never run its event loop.
    monkeypatch.setattr(
        "mapchar.ui.main_window.plugins.TextDialog",
        lambda title, body, parent=None: type(
            "Recorded", (), {"exec": lambda self: shown.append(body) or 0}
        )(),
    )
    assert window.open_project(str(proj))
    assert any("RuntimeError: boom" in body for body in shown)
    assert [i.declined for i in window._plugin_issues] == [False]

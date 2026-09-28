"""``tools/mapchar-lint`` ships a copy of the built-in ids; keep it honest.

The linter is a standalone package with no dependency on mapchar — it has to
run where mapchar is not installed — so it resolves plugin ids against a
generated snapshot of the built-in registry. That copy may exist; it may not go
stale, because a stale one reports a working project's ids as unknown and a
retired id as fine. When this fails::

    uv run python tools/mapchar-lint/generate_snapshot.py

The linter's own rules are tested in ``tools/mapchar-lint/tests``; what is
tested here is that it agrees with this build, including that the sample
projects this build writes lint without an error.
"""

from __future__ import annotations

import json
import os
import shutil

import pytest
from mapchar_lint.known import load_snapshot
from mapchar_lint.linter import lint
from mapchar_lint.snapshot import registry_body

from conftest import ROOT, TEST_DATA, local_tool

LINT = ROOT / "tools" / "mapchar-lint"
SNAPSHOT = LINT / "src" / "mapchar_lint" / "data" / "registry.json"
REGENERATE = "stale — run `uv run python tools/mapchar-lint/generate_snapshot.py`"


def test_the_snapshot_matches_the_built_in_registry():
    """Both directions matter: an id the snapshot lacks is reported as unknown
    on a project that works, and one it keeps after a rename hides the rename."""
    shipped = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    live = registry_body()
    for key in ("project_version", "plugins", "mapping_sizes", "renamed", "dialects"):
        assert shipped[key] == live[key], f"{key} {REGENERATE}"


def test_the_sample_projects_lint_without_errors(qtbot, monkeypatch, tmp_path):
    """Each sample whose ROM is in ``test-data/``, built into a scratch folder
    as ``make_sample_projects.py`` builds it."""
    from mapchar.ui import dialogs
    from window_helpers import make_window

    monkeypatch.setattr(dialogs.TextDialog, "exec", lambda self: 0)
    samples = local_tool("make_sample_projects")
    ids = load_snapshot()
    built = 0
    for game in [*samples.GAMES, *samples.DERIVED]:
        rom = TEST_DATA / game / samples.rom_name(game)
        tables = samples.GAMES.get(game, (None, None))[1]
        if not rom.is_file() or (tables and not os.path.isdir(tables)):
            continue
        folder = tmp_path / game
        folder.mkdir()
        shutil.copy(rom, folder)
        path = samples.build(make_window(qtbot, monkeypatch), game, str(folder))
        report = lint(path, ids)
        errors = [d for d in report.diagnostics if d.severity.value == "error"]
        assert not report.fatal and not errors, (game, errors)
        built += 1
    if not built:
        pytest.skip("no sample ROM in test-data/")

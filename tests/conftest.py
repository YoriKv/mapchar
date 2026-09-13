"""Shared pytest setup: headless Qt, automatic ``qt`` marking, a fresh plugin
registry, and where the abcde checkout the comparison tests need lives."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest

from mapchar.plugins.registry import default_registry

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_QT_HINT = re.compile(r"PySide6|qtbot|MainWindow|window_helpers")

ROOT = Path(__file__).resolve().parent.parent
"""The repository root."""

ABCDE = ROOT.parent / "abcde"
"""abcde's own checkout, beside this repository."""

needs_abcde = pytest.mark.skipif(
    shutil.which("perl") is None or not (ABCDE / "abcde.pl").exists(),
    reason="abcde not available",
)
"""Skip a test that runs abcde itself."""


def pytest_collection_modifyitems(config, items):
    """Mark a module ``qt`` when its source mentions Qt, so ``-m 'not qt'``
    runs the model layer alone without hand-maintained markers."""
    cache: dict[Path, bool] = {}
    for item in items:
        path = Path(str(item.fspath))
        if path not in cache:
            try:
                cache[path] = bool(_QT_HINT.search(path.read_text(encoding="utf-8")))
            except OSError:
                cache[path] = False
        if cache[path]:
            item.add_marker(pytest.mark.qt)


@pytest.fixture
def registry():
    """A default plugin registry, one per test so a test may register into it."""
    return default_registry()


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Keep QSettings out of the real user profile."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    yield

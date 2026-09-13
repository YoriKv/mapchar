"""Shared pytest setup: headless Qt, automatic ``qt`` marking."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_QT_HINT = re.compile(r"PySide6|qtbot|MainWindow")


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


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Keep QSettings out of the real user profile."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    yield

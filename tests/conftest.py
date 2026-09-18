"""Shared pytest setup: headless Qt, automatic ``qt`` marking, a fresh plugin
registry, and closed widgets deleted as each test ends."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

from mapchar.plugins.registry import default_registry

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_QT_HINT = re.compile(r"PySide6|qtbot|MainWindow|window_helpers")

ROOT = Path(__file__).resolve().parent.parent
"""The repository root."""


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
def unattended_dialogs(monkeypatch):
    """Answer the modal boxes a test never arranged for, rather than block.

    Every one of them runs an event loop of its own, so one box nobody answers
    hangs a headless run for good — including the ones raised at teardown, when
    closing the window asks about unsaved work. The default answer is the one
    that takes no action (Cancel, No), never a silent yes; a test that wants an
    answer patches it for itself, which lands after this. Reported warnings are
    collected instead of shown, and the list is what this fixture hands back.

    ``QDialog`` itself is answered too, not only the message boxes: the app's own
    modals (the block, container and dump dialogs, the text report) each run a
    loop of their own, and one raised by a path a test did not expect to reach
    hangs the run exactly the same way. Rejected is the answer that takes no
    action. ``QMessageBox``'s patch is installed after and wins for boxes, since
    it is set on the subclass.

    Guarded on Qt already being imported, so the headless suites stay Qt-free.
    """
    qtwidgets = sys.modules.get("PySide6.QtWidgets")
    if qtwidgets is None:
        return None
    dialog = qtwidgets.QDialog
    monkeypatch.setattr(dialog, "exec", lambda self: dialog.DialogCode.Rejected)
    monkeypatch.setattr(dialog, "open", lambda self: None)
    box = qtwidgets.QMessageBox
    reported: list[str] = []
    monkeypatch.setattr(box, "exec", lambda self: box.StandardButton.Cancel)
    monkeypatch.setattr(box, "question", lambda *a, **k: box.StandardButton.No)
    monkeypatch.setattr(
        box,
        "warning",
        lambda _parent, _title, message="", *a, **k: reported.append(message),
    )
    monkeypatch.setattr(box, "about", lambda *a, **k: None)
    return reported


@pytest.fixture(autouse=True)
def closed_widgets_are_deleted():
    """Delete the widgets a test closed, rather than keep them for the run.

    pytest-qt closes what a test registered and asks Qt to delete it later, but
    later never comes: a deferred delete is only carried out by a running event
    loop, and a test run has none — ``processEvents`` passes them over. Every
    window of every test would otherwise stay alive to the end of the run, with
    its timers, its signal connections and whatever it installed on the
    application, and the suite slows with each one. pytest-qt has closed the
    widgets by the time a fixture is torn down, so this is where they go.

    Guarded on Qt already being imported, so the headless suites stay Qt-free.
    """
    yield
    qtcore = sys.modules.get("PySide6.QtCore")
    if qtcore is None or qtcore.QCoreApplication.instance() is None:
        return
    qtcore.QCoreApplication.sendPostedEvents(None, qtcore.QEvent.Type.DeferredDelete)


@pytest.fixture(scope="session", autouse=True)
def settings_root(tmp_path_factory, request):
    """Point ``QSettings`` at a folder of this run's own, once, up front.

    Environment variables cannot do this: Qt resolves the settings location the
    first time a ``QSettings`` is built and keeps it for the life of the process,
    so a per-test ``XDG_CONFIG_HOME`` arrives too late and every window in the
    suite reads and writes the developer's real profile. ``setPath`` is the
    supported way to move it, and it only binds objects built afterwards — hence
    session scope, before the first window exists.

    Every format and scope is redirected, not just the one
    :func:`mapchar.ui.settings` asks for: the two-argument ``QSettings``
    constructor resolves under ``NativeFormat`` whatever ``setDefaultFormat``
    says, so pinning the INI format alone leaves the real profile in play. Then
    the redirection is *checked*, because a platform where it did not take (the
    Windows registry, which ``setPath`` cannot move) has to fail the run rather
    than write the developer's settings.

    Qt is imported here rather than at module scope, and only when the run
    actually holds a ``qt`` test, so ``-m 'not qt'`` stays headless.
    """
    if not any(item.get_closest_marker("qt") for item in request.session.items):
        yield
        return
    from PySide6.QtCore import QCoreApplication, QSettings

    from mapchar.ui import settings

    root = tmp_path_factory.mktemp("qsettings")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    for fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
            QSettings.setPath(fmt, scope, str(root))
    # For anything that builds a bare ``QSettings()`` off the application names.
    QCoreApplication.setOrganizationName("mapchar-tests")
    QCoreApplication.setApplicationName("mapchar-tests")
    where = settings().fileName()
    assert where.startswith(str(root)), f"QSettings still resolves to {where}"
    yield


@pytest.fixture(autouse=True)
def isolated_settings(settings_root, tmp_path, monkeypatch):
    """Hand each test an empty settings store, and the same one to nothing else.

    The store is emptied either side of the test, so a preference one test
    picks — an address format, a dock layout, a preview font — is not what the
    next test's window comes up in. Anything that caches a preference rather
    than reading it each time is dropped with it. The two environment variables
    stay for the paths Qt reads live rather than caching: the plugin folder and
    the trust store under ``AppData``.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    if sys.modules.get("PySide6.QtCore") is None:
        yield
        return
    from mapchar.ui import settings
    from mapchar.ui.preview_font import forget_preview_font

    settings().clear()
    forget_preview_font()
    yield
    settings().clear()
    forget_preview_font()

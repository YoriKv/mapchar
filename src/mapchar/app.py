"""Entry point. Qt is imported here and nowhere outside ``mapchar.ui``."""

from __future__ import annotations

import os
import sys


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtCore import QStandardPaths
    from PySide6.QtGui import QIcon, QPixmap
    from PySide6.QtWidgets import QApplication, QMessageBox

    from mapchar import resources
    from mapchar.plugins.discovery import (
        TrustStore,
        discover,
        plugin_roots,
        seed_examples,
    )
    from mapchar.plugins.registry import default_registry
    from mapchar.ui import settings
    from mapchar.ui.main_window import MainWindow
    from mapchar.ui.theme import apply_theme

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("mapchar")
    app.setOrganizationName("mapchar")
    apply_theme(app, str(settings().value("theme", "light")))
    # The live window and taskbar icon on every platform; the packaged Windows
    # and macOS apps also embed packaging/mapchar.ico / .icns at build time.
    icon = QPixmap()
    icon.loadFromData(resources.read_bytes("icons", "app.png"))
    app.setWindowIcon(QIcon(icon))

    app_data = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    plugin_dir = os.path.join(app_data, "plugins")
    try:
        seed_examples(plugin_dir)
    except OSError:
        pass
    trust = TrustStore(os.path.join(app_data, "trusted-plugins.json"))

    def confirm(path: str, digest: str, from_project: bool = False) -> bool:
        """Ask whether to run a not-yet-approved code plugin. Default: No.

        ``from_project`` marks a plugin that came with the project being opened
        rather than from the user's own folder — the same gate, but it says so,
        because a project is something you can be *sent* and its author is not
        necessarily the person answering this dialog.
        """
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("mapchar — load code plugin?")
        box.setText(
            "A code plugin that came with this project wants to load and will "
            "run with mapchar's privileges."
            if from_project
            else "A code plugin wants to load and will run with mapchar's privileges."
        )
        box.setInformativeText(
            f"{path}\n\nSHA-256: {digest}…\n\nOnly load plugins you trust. Load it?"
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def reload_plugins(project_dir: str | None):
        registry = default_registry()
        roots = plugin_roots(plugin_dir, project_dir)
        project_root = os.path.join(project_dir, "plugins") if project_dir else None

        def ask(path: str, digest: str) -> bool:
            # The separator is part of the test: a sibling folder whose name
            # merely starts with the project's own is not inside it.
            inside = os.path.join(os.path.abspath(project_root or ""), "")

            return confirm(
                path,
                digest,
                from_project=project_root is not None
                and os.path.abspath(path).startswith(inside),
            )

        result = discover(registry, roots, trust, ask)
        return registry, result.issues

    registry, issues = reload_plugins(None)
    window = MainWindow(
        registry,
        reload_plugins=reload_plugins,
        plugin_dir=plugin_dir,
        plugin_issues=issues,
    )
    window.show()
    for arg in app.arguments()[1:]:
        if arg.lower().endswith(".mapchar"):
            window.open_project(arg)
        elif arg.lower().endswith(".tbl"):
            window.open_table(arg)
        else:
            window.open_rom(arg)
    return app.exec()

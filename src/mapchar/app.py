"""Entry point. Qt is imported here and nowhere outside ``mapchar.ui``."""

from __future__ import annotations

import os
import sys


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtCore import QSettings, QStandardPaths
    from PySide6.QtWidgets import QApplication, QMessageBox

    from mapchar.plugins.discovery import (
        TrustStore,
        discover,
        plugin_roots,
        seed_examples,
    )
    from mapchar.plugins.registry import default_registry
    from mapchar.ui.main_window import MainWindow
    from mapchar.ui.theme import apply_theme

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("mapchar")
    app.setOrganizationName("mapchar")
    settings = QSettings("mapchar", "mapchar")
    apply_theme(app, str(settings.value("theme", "light")))

    app_data = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    plugin_dir = os.path.join(app_data, "plugins")
    try:
        seed_examples(plugin_dir)
    except OSError:
        pass
    trust = TrustStore(os.path.join(app_data, "trusted-plugins.json"))

    def confirm(path: str, digest: str) -> bool:
        answer = QMessageBox.question(
            None,
            "Trust plugin?",
            f"Run the code plugin\n{path}\n(SHA-256 {digest}…)?\n\n"
            "Only trust plugins you have read.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def reload_plugins(project_dir: str | None):
        registry = default_registry()
        result = discover(
            registry, plugin_roots(plugin_dir, project_dir), trust, confirm
        )
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

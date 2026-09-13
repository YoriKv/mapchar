"""Entry point. Qt is imported here and nowhere outside ``mapchar.ui``."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from mapchar.ui.main_window import MainWindow
    from mapchar.ui.theme import apply_theme

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("mapchar")
    app.setOrganizationName("mapchar")
    settings = QSettings("mapchar", "mapchar")
    apply_theme(app, str(settings.value("theme", "light")))
    window = MainWindow()
    window.show()
    for arg in app.arguments()[1:]:
        if arg.lower().endswith(".mapchar"):
            window.open_project(arg)
        elif arg.lower().endswith(".tbl"):
            window.open_table(arg)
        else:
            window.open_rom(arg)
    return app.exec()

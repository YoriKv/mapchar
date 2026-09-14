"""mapchar.ui: the PySide6 application, and what every part of it shares."""

from __future__ import annotations

from PySide6.QtCore import QSettings

BYTES_PER_ROW = 16
"""Bytes per row in every byte view."""
DUMP_WINDOW_BYTES = 4096
"""How many bytes the Hex panel's dump and the decompressed preview show from
their offset. The central view's own tabs take no such number: each shows the
window its box has room for (:mod:`mapchar.ui.main_window.refresh`)."""

_ORGANISATION = "mapchar"
_APPLICATION = "mapchar"


def settings() -> QSettings:
    """The app's stored settings, under one organisation and application."""
    return QSettings(_ORGANISATION, _APPLICATION)


__all__ = ["BYTES_PER_ROW", "DUMP_WINDOW_BYTES", "settings"]

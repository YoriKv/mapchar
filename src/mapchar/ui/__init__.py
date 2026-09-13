"""mapchar.ui: the PySide6 application, and what every part of it shares."""

from __future__ import annotations

from PySide6.QtCore import QSettings

BYTES_PER_ROW = 16
"""Bytes per row in every byte view."""
TEXT_WINDOW_BYTES = 4096
"""How many bytes a text or hex window decodes from its offset."""

_ORGANISATION = "mapchar"
_APPLICATION = "mapchar"


def settings() -> QSettings:
    """The app's stored settings, under one organisation and application."""
    return QSettings(_ORGANISATION, _APPLICATION)


__all__ = ["BYTES_PER_ROW", "TEXT_WINDOW_BYTES", "settings"]

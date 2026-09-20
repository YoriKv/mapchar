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


_TRUE = (True, 1, "true", "True", "TRUE", "1", "yes", "on")


def as_bool(value: object, default: bool = False) -> bool:
    """A stored value read as a switch. QSettings hands one back as it was
    stored — a bool on one platform, the string it was written as on another —
    so every switch is read through here rather than each caller guessing
    which."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return value in _TRUE


def setting_bool(key: str, default: bool = False) -> bool:
    """The switch stored under ``key``."""
    return as_bool(settings().value(key, default), default)


def set_setting_bool(key: str, on: bool) -> None:
    """Store a switch, spelled as :func:`setting_bool` reads it."""
    settings().setValue(key, "true" if on else "false")


def setting_int(
    key: str, default: int, low: int | None = None, high: int | None = None
) -> int:
    """The number stored under ``key``, held between ``low`` and ``high``.

    QSettings hands a number back as it was stored — a string on one platform,
    an int on another — and a store written by another build may hold something
    else again, so anything that will not read as a number lands on the default
    rather than on an exception at startup. The bounds are the caller's, since
    a stored number outside them is as unusable as none at all.
    """
    try:
        value = int(str(settings().value(key, default)))
    except (TypeError, ValueError):
        value = default
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


__all__ = [
    "BYTES_PER_ROW",
    "DUMP_WINDOW_BYTES",
    "as_bool",
    "set_setting_bool",
    "setting_bool",
    "setting_int",
    "settings",
]

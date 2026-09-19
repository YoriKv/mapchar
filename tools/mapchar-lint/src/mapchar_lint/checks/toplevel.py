"""Checks on the document itself: its version, what it opens on, the glossary,
and keys nothing reads."""

from __future__ import annotations

import difflib

from mapchar_lint.context import Context
from mapchar_lint.schema import GLOSSARY_KEYS, KINDS_WITH_VIEW, TOP_KEYS


def check(ctx: Context) -> None:
    data = ctx.doc.data
    _version(ctx, data)
    _current(ctx, data)
    _glossary(ctx, data.get("glossary"))
    for key in data:
        if key not in TOP_KEYS:
            close = difflib.get_close_matches(key, TOP_KEYS, n=1)
            ctx.error(
                "E114",
                f"`{key}` is not a project key"
                + (f" — did you mean `{close[0]}`?" if close else ""),
                pointer=f"/{key}",
                detail="The reader passes over it and the next save drops it.",
            )
    if ctx.doc.bom:
        ctx.info(
            "I105",
            "the file starts with a byte-order mark",
            detail="mapchar reads past it and never writes one back.",
        )


def _version(ctx: Context, data: dict) -> None:
    known = ctx.ids.project_version
    if "version" not in data:
        ctx.warn(
            "W101",
            "the project has no version",
            detail="It reads as version 1 and every migration since runs over it. "
            f"A file written for version {known} should say so.",
        )
        return
    version = data["version"]
    if isinstance(version, bool) or not isinstance(version, int):
        ctx.error(
            "E102",
            f"version is {version!r}, not a whole number",
            pointer="/version",
            detail="It reads as version 1 and every migration since runs over it.",
        )
    elif version > known:
        ctx.warn(
            "W103",
            f"version {version} is newer than this build's {known}",
            pointer="/version",
            detail="mapchar opens it with a notice, reading only what it knows; "
            "saving writes it back at its own version.",
        )
    elif version < known:
        ctx.info(
            "I104",
            f"version {version} is older than {known}",
            pointer="/version",
            detail="The migrations walk it forward as it loads, and the next save "
            "writes the current version.",
        )


def _current(ctx: Context, data: dict) -> None:
    if "current" not in data:
        return
    current = data["current"]
    if isinstance(current, bool) or not isinstance(current, int):
        ctx.error(
            "E113",
            f"current is {current!r}, not an index into entries",
            pointer="/current",
            detail="Ignored: the project opens with nothing selected.",
        )
        return
    if not 0 <= current < len(ctx.entries):
        ctx.error(
            "E111",
            f"current is {current}, but there are {len(ctx.entries)} entries",
            pointer="/current",
            detail="Ignored: the project opens with nothing selected.",
        )
        return
    view = ctx.entries[current]
    if view.dropped:
        ctx.error(
            "E111",
            f"current names entries[{current}], which does not load",
            pointer="/current",
            entry=view,
            detail="The project opens with nothing selected.",
        )
    elif view.kind not in KINDS_WITH_VIEW:
        ctx.warn(
            "W112",
            f"current names a {view.kind}, which has nothing to show",
            pointer="/current",
            entry=view,
            detail="The project opens with nothing selected.",
        )


def _glossary(ctx: Context, glossary: object) -> None:
    if glossary is None:
        return
    if not isinstance(glossary, list):
        ctx.error(
            "E121",
            "the glossary is not a list of terms",
            pointer="/glossary",
            detail="The whole glossary is ignored, and the next save writes none.",
        )
        return
    for at, item in enumerate(glossary):
        pointer = f"/glossary/{at}"
        if not isinstance(item, dict) or not isinstance(item.get("t"), str):
            ctx.error(
                "E122",
                "a glossary term needs `t`, the term, as a string",
                pointer=pointer,
                detail="The term is left out of the glossary.",
            )
            continue
        for key, value in item.items():
            if key not in GLOSSARY_KEYS:
                ctx.error(
                    "E123",
                    f"`{key}` is not a glossary key (t, r, n)",
                    pointer=f"{pointer}/{key}",
                    detail="Ignored, and dropped by the next save.",
                )
            elif key != "t" and value is not None and not isinstance(value, str):
                ctx.warn(
                    "W124",
                    f"`{key}` is {value!r}, not a string",
                    pointer=f"{pointer}/{key}",
                    detail=f"It reads as {str(value or '')!r}.",
                )

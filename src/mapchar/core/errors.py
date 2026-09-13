"""Exception types shared by every layer.

The pipeline **hard-stops** at the first stage that cannot proceed and says
which stage, which direction and why: it never degrades, guesses, or writes
partial output. :class:`PipelineError` carries exactly that, and
:class:`Stage` names the extension points it can happen at — here rather than
in ``plugins/base.py`` so the error type does not have to reach up into the
plugin layer to name where it came from.
"""

from __future__ import annotations

from enum import Enum


class Stage(Enum):
    """The pipeline's extension points.

    A stage is **one plugin covering both directions**: a container is the read
    and the write of one on-disk wrapper, a compression scheme is its
    decompress and compress halves. Which direction was running is not lost by
    that — it is :attr:`PipelineError.action`.

    The value is also the user plugin subfolder that accepts the stage.
    """

    CONTAINER = "containers"
    COMPRESSION = "compression"
    CHARSET = "charsets"
    MAPPING = "mappings"

    @property
    def folder(self) -> str:
        """The user plugin subfolder that accepts this stage."""
        return self.value


class MapcharError(Exception):
    """Base of every error mapchar raises on purpose."""


class PipelineError(MapcharError):
    """A stage could not proceed; the run halts and reports this.

    ``action`` is the direction within the stage — ``read``/``write`` for a
    container, ``decompress``/``compress`` for a compression scheme — because a
    stage spans both and "the container failed" reads very differently from
    "the container failed **while saving**". ``plugin`` names whose code was
    running, which is what turns a report into something a user can act on.

    ``pathway`` labels *which* run failed, for the writes that run several: one
    Write All lays out every dirty block over one file, and a refusal naming
    the block is the difference between a fixable message and a puzzle.
    """

    def __init__(
        self,
        stage: Stage,
        action: str,
        plugin: str,
        message: str,
        pathway: str = "",
    ):
        self.stage = stage
        self.action = action
        self.plugin = plugin
        self.pathway = pathway
        self.message = message
        label = f"{stage.name.lower()}:{action}" if action else stage.name.lower()
        if pathway:
            label = f"{pathway}/{label}"
        detail = f"{plugin}: {message}" if plugin else message
        super().__init__(f"[{label}] {detail}")


class LocatedError(MapcharError):
    """An error in a file, formatted ``path:line: message``.

    ``placeholder`` stands in for a file that was not named.
    """

    placeholder = "<file>"

    def __init__(self, message: str, path: str | None = None, line: int | None = None):
        self.message = message
        self.path = path
        self.line = line
        super().__init__(self.format())

    def format(self) -> str:
        where = self.path or self.placeholder
        if self.line is not None:
            where = f"{where}:{self.line}"
        return f"{where}: {self.message}"


class TableError(LocatedError):
    """A table file could not be loaded. Carries the file and line."""

    placeholder = "<table>"


class ScriptError(LocatedError):
    """A script file could not be parsed."""

    placeholder = "<script>"


class EncodeError(MapcharError):
    """Text could not be encoded, or did not decode back to itself."""

    def __init__(self, message: str, position: int | None = None, context: str = ""):
        self.position = position
        self.context = context
        super().__init__(message)

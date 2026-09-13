"""Package data, read through :mod:`importlib.resources` so it resolves the same
in a source checkout and in a frozen build."""

from importlib.resources import files


def resource(*parts: str):
    """A Traversable for ``mapchar/resources/<parts...>``.

    For data read as a tree rather than as a known file — the plugin examples
    are seeded by walking their folders, so the caller needs the nodes.
    """
    node = files(__name__)
    for part in parts:
        node = node.joinpath(part)
    return node


def read_bytes(*parts: str) -> bytes:
    """The bytes of ``mapchar/resources/<parts...>``."""
    return resource(*parts).read_bytes()


def read_text(*parts: str, encoding: str = "utf-8") -> str:
    """The text of ``mapchar/resources/<parts...>``."""
    return resource(*parts).read_text(encoding=encoding)

"""Package data, read through :mod:`importlib.resources` so it resolves the same
in a source checkout and in a frozen build."""

from importlib.resources import files


def read_bytes(*parts: str) -> bytes:
    """The bytes of ``mapchar/resources/<parts...>``."""
    node = files(__name__)
    for part in parts:
        node = node.joinpath(part)
    return node.read_bytes()

"""The reference material seeded into the user's plugin folder."""

from __future__ import annotations

import os

from mapchar.plugins.discovery import FOLDERS

# The plugin folder's own documentation, seeded beside the typed subfolders.
# Not a plugin, and ``.md`` is not a suffix discovery loads, so it sits there
# inertly.
PLUGIN_README = "README.md"


def seed_examples(user_dir: str) -> None:
    """Refresh the shipped reference material in the plugin root.

    The examples are ``_``-prefixed so discovery ignores them: living
    documentation a user copies, dropping the underscore, to activate.
    :data:`PLUGIN_README` is seeded alongside them.

    **A stale copy is replaced**, matched by filename, so the examples describe
    the version actually running rather than whichever one first created the
    folder. That cannot take a user's work with it: what they edit is the
    activated copy under a different name. Files whose contents
    already match are left alone, so an unchanged folder is not rewritten on
    every launch.

    Failures are swallowed — reference material is not worth blocking startup
    over. The ``.py`` examples ship as ``.py.txt`` because frozen builds exclude
    ``.py`` data files; the suffix is dropped here.
    """
    from mapchar import resources

    root = os.path.abspath(user_dir)
    try:
        os.makedirs(root, exist_ok=True)
    except OSError:
        return
    _seed_file(resources.resource("data", "plugin-examples", PLUGIN_README), root)
    for folder in FOLDERS:
        dest = os.path.join(root, folder)
        try:
            os.makedirs(dest, exist_ok=True)
            entries = list(
                resources.resource("data", "plugin-examples", folder).iterdir()
            )
        except OSError:
            continue
        for entry in entries:
            _seed_file(entry, dest)


def _seed_file(entry, dest_dir: str) -> None:
    """Write one shipped file into ``dest_dir`` unless it is already identical."""
    dest = os.path.join(dest_dir, entry.name.removesuffix(".txt"))
    try:
        shipped = entry.read_text(encoding="utf-8")
    except (OSError, FileNotFoundError):
        return
    try:
        if os.path.exists(dest):
            with open(dest, encoding="utf-8") as f:
                if f.read() == shipped:
                    return
        with open(dest, "w", encoding="utf-8", newline="\n") as f:
            f.write(shipped)
    except OSError:
        pass

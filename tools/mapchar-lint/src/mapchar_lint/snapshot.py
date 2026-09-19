"""The registry body the linter checks ids against, read from a live mapchar.

The one module here that imports mapchar, and only when asked: by
``generate_snapshot.py`` to write ``data/registry.json``, and by ``--live``.
Everything else reads the snapshot, so the linter runs where mapchar is not.
"""

from __future__ import annotations

import os


def registry_body(user_plugins: bool = False) -> dict:
    """The built-in registry — and with ``user_plugins``, the user's preset
    folders too — as the snapshot's JSON body. Raises ``ImportError`` when
    mapchar is not importable.

    Code plugins are never run to learn their ids: a linter executing whatever
    a plugin folder holds would be a way to run code by asking for a lint. A
    stage whose user folder holds one is listed under ``opaque`` instead.
    """
    from mapchar.core.errors import Stage
    from mapchar.plugins.aliases import RENAMED
    from mapchar.plugins.registry import default_registry
    from mapchar.project.formats.legacy import DIALECTS
    from mapchar.project.projectfile import PROJECT_VERSION

    registry = default_registry()
    opaque: list[str] = []
    if user_plugins:
        opaque = _discover_user_presets(registry)
    body = {
        "project_version": PROJECT_VERSION,
        "plugins": {
            stage.value: sorted(p.info.id for p in registry.plugins(stage))
            for stage in Stage
        },
        # The pointer sizes each mapping offers the Reading bar: a size outside
        # them reads, but is one no control in the app can show or set.
        "mapping_sizes": {
            p.info.id: list(p.sizes) for p in registry.plugins(Stage.MAPPING)
        },
        # Carried so the linter can tell "an id this build never had" from "an
        # id that has been renamed since" — the second is a working project
        # that stops depending on the table as soon as it is re-saved.
        "renamed": dict(RENAMED),
        "dialects": list(DIALECTS),
    }
    if user_plugins:
        body["opaque"] = opaque
    return body


def _discover_user_presets(registry) -> list[str]:
    """Load the user's preset folders into ``registry``; return the stages
    whose folders hold code plugins, which are declined rather than run."""
    from mapchar.plugins.discovery import FOLDERS, discover, plugin_roots
    from mapchar.project.tables import read_table_file

    # The environment variable's folders and the user's own, as the app loads
    # them; plugin_roots reads the variable itself.
    roots = plugin_roots(_user_plugin_dir(), None)
    discover(
        registry,
        roots,
        confirm=lambda *_args: False,
        table_reader=lambda path: read_table_file(path).table,
    )
    opaque = set()
    for root, _category in roots:
        for folder, stage in FOLDERS.items():
            path = os.path.join(root, folder)
            if os.path.isdir(path) and any(
                name.endswith(".py") and not name.startswith("_")
                for name in os.listdir(path)
            ):
                opaque.add(stage.value)
    return sorted(opaque)


def _user_plugin_dir() -> str | None:
    """The folder the app loads the user's plugins from, when Qt can say where
    it is; None otherwise, which leaves the environment variable's roots."""
    try:
        from PySide6.QtCore import QCoreApplication, QStandardPaths
    except ImportError:
        return None
    QCoreApplication.setApplicationName("mapchar")
    QCoreApplication.setOrganizationName("mapchar")
    app_data = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    return os.path.join(app_data, "plugins") if app_data else None

"""User and project plugin folders: discovery, presets and issues.

The trust gate over a code plugin is :mod:`mapchar.plugins.trust`, and the
reference material seeded into the folder is :mod:`mapchar.plugins.examples`.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

from mapchar.core.table import Table
from mapchar.plugins.base import REQUIRED_METHODS, Stage
from mapchar.plugins.builtins.mappings import Banked
from mapchar.plugins.registry import Registry, RegistryError
from mapchar.plugins.trust import TrustStore, digest_of, is_approved

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

FOLDERS: dict[str, Stage] = {
    "containers": Stage.CONTAINER,
    "compression": Stage.COMPRESSION,
    "charsets": Stage.CHARSET,
    "mappings": Stage.MAPPING,
}
ENV_VAR = "MAPCHAR_PLUGIN_PATH"


@dataclass
class PluginLoadIssue:
    """One plugin file that did not load. Collected, never raised, so one bad
    file cannot stop the app or the other plugins from starting.

    ``declined`` separates **a choice from a breakage**: a code plugin the user
    refused at the trust prompt did exactly what they asked, and reporting it as
    a failure would put a "plugins failed to load" modal in front of them at
    every launch and every refresh for as long as the answer stands. It is still
    an issue — something in the folder is not running — so it is collected here
    and told apart where it is shown, not dropped.
    """

    path: str
    message: str
    declined: bool = False

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


@dataclass
class DiscoveryResult:
    loaded: list[str] = field(default_factory=list)
    issues: list[PluginLoadIssue] = field(default_factory=list)


class ScopedRegistry:
    """What a code plugin's ``register(registry)`` receives.

    Checks the stage against the folder and the required methods, relabels
    the category, and records an issue instead of raising.
    """

    def __init__(
        self, registry: Registry, stage: Stage, category: str, path: str, issues
    ):
        self._registry = registry
        self._stage = stage
        self._category = category
        self._path = path
        self._issues = issues
        self.registered: list[str] = []

    def register(self, plugin) -> None:
        info = getattr(plugin, "info", None)
        if info is None or info.stage is not self._stage:
            self._issues.append(
                PluginLoadIssue(
                    self._path, f"plugin is not a {self._stage.name.lower()} plugin"
                )
            )
            return
        for method in REQUIRED_METHODS[self._stage]:
            if not callable(getattr(plugin, method, None)):
                self._issues.append(
                    PluginLoadIssue(self._path, f"{info.id} lacks {method}()")
                )
                return
        from dataclasses import replace

        try:
            # The heading is presentation, so a plugin that refuses the write
            # (``__slots__``, a read-only descriptor, a property) is registered
            # as it is rather than dropped — the plugin is the point.
            plugin.info = replace(info, category=self._category)
        except (AttributeError, TypeError):
            pass
        try:
            self._registry.register(plugin)
        except RegistryError as exc:
            self._issues.append(PluginLoadIssue(self._path, str(exc)))
            return
        self.registered.append(info.id)


def plugin_roots(
    user_dir: str | None, project_dir: str | None
) -> list[tuple[str, str]]:
    """``(folder, category)`` in load order: env var, user folder, project."""
    roots: list[tuple[str, str]] = []
    for extra in filter(None, os.environ.get(ENV_VAR, "").split(os.pathsep)):
        roots.append((extra, "Your plugins"))
    if user_dir:
        roots.append((user_dir, "Your plugins"))
    if project_dir:
        roots.append((os.path.join(project_dir, "plugins"), "Project plugins"))
    return roots


def discover(
    registry: Registry,
    roots: list[tuple[str, str]],
    trust: TrustStore | None = None,
    confirm: Callable[[str, str], bool] | None = None,
    table_reader: Callable[[str], Table] | None = None,
) -> DiscoveryResult:
    """Load presets and code plugins from every root's typed subfolders.

    ``table_reader`` reads one ``.tbl`` charset file into a table. It is passed
    in rather than imported: reading a table file is ``project``'s job, and
    ``plugins`` sits under it. Without one, a ``.tbl`` in ``charsets/`` is
    reported as an issue rather than loaded.
    """
    result = DiscoveryResult()
    for root, category in roots:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if (
                os.path.isfile(path)
                and not name.startswith("_")
                and name.endswith((".toml", ".py", ".tbl"))
            ):
                # Only a file that *looks* like a plugin: the folder also holds
                # the seeded README, and the point is to catch a plugin that
                # will never load, not to police what else a user keeps here.
                result.issues.append(
                    PluginLoadIssue(path, "loose file; plugins go in a typed subfolder")
                )
            if not os.path.isdir(path):
                continue
            stage = FOLDERS.get(name)
            if stage is None:
                continue
            for file in sorted(os.listdir(path)):
                if file.startswith("_"):
                    continue
                full = os.path.join(path, file)
                if file.endswith(".toml"):
                    _load_preset(registry, stage, category, full, result)
                elif file.endswith(".py"):
                    _load_code(registry, stage, category, full, result, trust, confirm)
                elif file.endswith(".tbl") and stage is Stage.CHARSET:
                    _load_charset_table(registry, category, full, result, table_reader)
    return result


def _load_preset(registry, stage, category, path, result) -> None:
    """Adapt one TOML preset into a plugin and register it.

    The **whole** load is guarded, not just the parts known to raise: a preset
    is a hand-edited file, every engine reads its own parameters, and a bad
    value anywhere in one of them must come back as an issue against that file
    rather than a traceback out of discovery and through ``app.main``. One bad
    TOML in the plugin folder cannot be the reason the app does not start.
    """
    try:
        with open(path, "rb") as f:
            # tomllib insists on UTF-8 with no byte-order mark; a Windows
            # editor's mark is stripped rather than made an issue.
            data = tomllib.loads(f.read().decode("utf-8-sig"))
        result.loaded.append(_build_preset(registry, stage, category, path, data))
    except Exception as exc:  # noqa: BLE001 - report, never abort startup
        result.issues.append(PluginLoadIssue(path, f"preset load failed: {exc}"))


def _build_preset(registry, stage, category, path, data) -> str:
    """The registered id, or raise with what is wrong with this preset."""
    _check_declared_stage(data, stage)
    engine = str(data.get("engine", ""))
    pid = str(data.get("id", os.path.splitext(os.path.basename(path))[0]))
    name = str(data.get("name", pid))
    params = data.get("params", {}) or {}
    plugin = None
    if stage is Stage.MAPPING and engine == "banked":
        if not {"bank_size", "bank_base"} <= set(params):
            raise ValueError("banked needs params bank_size and bank_base")
        plugin = Banked(int(params["bank_size"]), int(params["bank_base"]), pid)
    elif stage is Stage.COMPRESSION and engine == "huffman":
        from mapchar.plugins.builtins.compression import HuffmanTable

        plugin = HuffmanTable(params, pid, name)
    elif stage is Stage.COMPRESSION and engine == "lzss":
        from mapchar.plugins.builtins.compression import Lzss

        plugin = Lzss(params, pid, name)
    elif stage is Stage.COMPRESSION and engine == "bitpack":
        from mapchar.plugins.builtins.compression import BitPack

        plugin = BitPack(int(params.get("width", 6)))
    if plugin is None:
        raise ValueError(f"unknown engine {engine!r} for {stage.value}")
    from dataclasses import replace

    plugin.info = replace(plugin.info, id=pid, name=name, category=category)
    registry.register(plugin)
    return pid


def _check_declared_stage(data: dict, stage: Stage) -> None:
    """Raise if the preset states a stage the folder it sits in contradicts.

    The folder is authoritative, so no preset has to declare a stage. Saying it
    anyway is tolerated while it agrees — a preset stays self-describing — but a
    conflicting one is an error rather than a silent move into another pathway,
    which would otherwise look like the file being ignored.
    """
    declared = data.get("stage")
    if declared is not None and str(declared) != stage.value:
        raise ValueError(
            f"stage {declared!r} conflicts with the folder's stage {stage.value!r} "
            "- drop the stage field; the folder determines it"
        )


def _load_charset_table(registry, category, path, result, table_reader) -> None:
    from mapchar.core.tokens import plain_text
    from mapchar.plugins.base import PluginInfo

    if table_reader is None:
        result.issues.append(
            PluginLoadIssue(path, "no table reader given; a .tbl charset needs one")
        )
        return
    try:
        table = table_reader(path)
    except Exception as exc:  # noqa: BLE001
        result.issues.append(PluginLoadIssue(path, str(exc)))
        return
    pid = os.path.splitext(os.path.basename(path))[0]
    entries = [
        (e.bits, plain_text(e.text))
        for e in table.entries.values()
        if e.kind.value == "text"
    ]

    class TableCharset:
        info = PluginInfo(pid, pid, Stage.CHARSET, category)

        def entries(self):
            return list(entries)

    try:
        registry.register(TableCharset())
        result.loaded.append(pid)
    except RegistryError as exc:
        result.issues.append(PluginLoadIssue(path, str(exc)))


def _load_code(registry, stage, category, path, result, trust, confirm) -> None:
    try:
        with open(path, "rb") as f:
            source = f.read()
    except OSError as exc:
        result.issues.append(PluginLoadIssue(path, str(exc)))
        return
    digest = digest_of(source)
    if not is_approved(path, digest, trust, confirm):
        result.issues.append(
            PluginLoadIssue(
                path,
                "not approved to run: the trust prompt for this code plugin was "
                "declined",
                declined=True,
            )
        )
        return
    module_name = f"mapchar_plugin_{digest[:16]}"
    spec = importlib.util.spec_from_loader(module_name, loader=None, origin=path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    module.__file__ = path
    try:
        exec(compile(source, path, "exec"), module.__dict__)
        register = getattr(module, "register", None)
        if not callable(register):
            result.issues.append(
                PluginLoadIssue(path, "no register(registry) function")
            )
            return
        scoped = ScopedRegistry(registry, stage, category, path, result.issues)
        register(scoped)
        result.loaded.extend(scoped.registered)
    except Exception as exc:  # noqa: BLE001 - a plugin must never crash the app
        result.issues.append(PluginLoadIssue(path, f"{type(exc).__name__}: {exc}"))
    finally:
        sys.modules.pop(module_name, None)

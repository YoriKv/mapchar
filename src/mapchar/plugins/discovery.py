"""User and project plugin folders: discovery, presets, trust and issues."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

from mapchar.core.mapping import Banked
from mapchar.plugins.base import REQUIRED_METHODS, Stage
from mapchar.plugins.registry import Registry, RegistryError

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

FOLDERS: dict[str, Stage] = {
    "containers": Stage.CONTAINER,
    "reshape": Stage.RESHAPE,
    "compression": Stage.COMPRESSION,
    "charsets": Stage.CHARSET,
    "mappings": Stage.MAPPING,
}
ENV_VAR = "MAPCHAR_PLUGIN_PATH"


@dataclass
class PluginLoadIssue:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


@dataclass
class DiscoveryResult:
    loaded: list[str] = field(default_factory=list)
    issues: list[PluginLoadIssue] = field(default_factory=list)


class TrustStore:
    """Approved SHA-256 digests of code plugins, in a JSON file."""

    def __init__(self, path: str | None):
        self.path = path
        self._digests: set[str] = set()
        self._session: set[str] = set()
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self._digests = set(json.load(f).get("trusted", []))
            except (OSError, ValueError):
                self._digests = set()

    def is_trusted(self, digest: str) -> bool:
        return digest in self._digests or digest in self._session

    def trust(self, digest: str, persist: bool = True) -> None:
        self._session.add(digest)
        if persist:
            self._digests.add(digest)
            self._save()

    def _save(self) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"trusted": sorted(self._digests)}, f, indent=2)


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

        plugin.info = replace(info, category=self._category)
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
) -> DiscoveryResult:
    """Load presets and code plugins from every root's typed subfolders."""
    result = DiscoveryResult()
    for root, category in roots:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if os.path.isfile(path) and not name.startswith("_"):
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
                    _load_charset_table(registry, category, full, result)
    return result


def _load_preset(registry, stage, category, path, result) -> None:
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, ValueError) as exc:
        result.issues.append(PluginLoadIssue(path, f"bad TOML: {exc}"))
        return
    engine = str(data.get("engine", ""))
    pid = str(data.get("id", os.path.splitext(os.path.basename(path))[0]))
    name = str(data.get("name", pid))
    params = data.get("params", {}) or {}
    plugin = None
    if stage is Stage.MAPPING and engine == "banked":
        try:
            plugin = Banked(int(params["bank_size"]), int(params["bank_base"]), pid)
        except (KeyError, ValueError) as exc:
            result.issues.append(
                PluginLoadIssue(path, f"banked needs bank_size and bank_base: {exc}")
            )
            return
    elif stage is Stage.COMPRESSION and engine == "huffman":
        from mapchar.plugins.builtins.compression import HuffmanTable

        plugin = HuffmanTable(params, pid, name)
    elif stage is Stage.COMPRESSION and engine == "bitpack":
        from mapchar.plugins.builtins.compression import BitPack

        plugin = BitPack(int(params.get("width", 6)))
        plugin.info = type(plugin.info)(pid, name, stage, category)
    if plugin is None:
        result.issues.append(
            PluginLoadIssue(path, f"unknown engine {engine!r} for {stage.value}")
        )
        return
    from dataclasses import replace

    plugin.info = replace(plugin.info, id=pid, name=name, category=category)
    try:
        registry.register(plugin)
        result.loaded.append(pid)
    except RegistryError as exc:
        result.issues.append(PluginLoadIssue(path, str(exc)))


def _load_charset_table(registry, category, path, result) -> None:
    from mapchar.core.tokens import plain_text
    from mapchar.plugins.base import PluginInfo
    from mapchar.project.formats.table_legacy import load_table_text

    try:
        with open(path, encoding="utf-8") as f:
            tf = load_table_text(f.read(), path)
    except Exception as exc:  # noqa: BLE001
        result.issues.append(PluginLoadIssue(path, str(exc)))
        return
    pid = os.path.splitext(os.path.basename(path))[0]
    entries = [
        (e.bits, plain_text(e.text))
        for t in tf.tables
        for e in t.entries.values()
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
    digest = hashlib.sha256(source).hexdigest()
    if trust is not None and not trust.is_trusted(digest):
        if confirm is None or not confirm(path, digest[:12]):
            result.issues.append(PluginLoadIssue(path, "not trusted; skipped"))
            return
        trust.trust(digest)
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


EXAMPLE_README = """mapchar plugins

Put plugins in the typed folders beside this file:
  containers/   .py
  reshape/      .py
  compression/  .py, .toml presets (engine = "huffman" or "bitpack")
  charsets/     .py, .tbl (a table file registers as a charset named after it)
  mappings/     .py, .toml presets (engine = "banked")

Files starting with _ are ignored. Code plugins ask for trust once per file
content. Refresh with F5.
"""

EXAMPLE_MAPPING = """# A banked mapping preset: 16 KiB banks at $8000 (NES-style).
id = "example_banked"
name = "Example banked mapping"
engine = "banked"

[params]
bank_size = 0x4000
bank_base = 0x8000
"""

EXAMPLE_RESHAPE = '''"""Example reshape plugin: swaps the two halves of the file."""

from mapchar.plugins.base import PluginInfo, Stage


class SwapHalves:
    info = PluginInfo("example_swap_halves", "Swap halves", Stage.RESHAPE)

    def reshape(self, data, ctx):
        half = len(data) // 2
        return data[half:] + data[:half]

    def unshape(self, data, ctx):
        half = len(data) - len(data) // 2
        return data[half:] + data[:half]


def register(registry):
    registry.register(SwapHalves())
'''


def seed_examples(user_dir: str) -> None:
    """Create the typed folders and the ``_``-prefixed examples once."""
    for folder in FOLDERS:
        os.makedirs(os.path.join(user_dir, folder), exist_ok=True)
    files = {
        "README.txt": EXAMPLE_README,
        os.path.join("mappings", "_example_banked.toml"): EXAMPLE_MAPPING,
        os.path.join("reshape", "_example_swap_halves.py.txt"): EXAMPLE_RESHAPE,
    }
    for rel, text in files.items():
        path = os.path.join(user_dir, rel)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)

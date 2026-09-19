"""Which plugin ids exist — from the running mapchar, or the shipped snapshot.

Two sources, and which one answered is reported, because they answer different
questions. A **live registry** knows the user's own plugins, so it can say an
id is genuinely missing. The **snapshot** knows only what mapchar ships, so an
id it has never heard of may be a typo or may be a plugin the author has
installed, and it says so rather than claiming the stronger finding.

Either way the ``plugins/`` folder beside the project is read first: a project
travels with its formats, so what that folder declares is as present as a
built-in — for that project.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from os import listdir
from os.path import abspath, dirname, isdir, join, splitext

_SNAPSHOT = join(dirname(__file__), "data", "registry.json")

#: ``mapchar.core.errors.Stage`` values, which are also the plugin subfolders.
STAGES = ("containers", "compression", "charsets", "mappings")

#: ``banked:<base hex>:<size hex>``, built on demand (``mappings.parse_banked``).
_BANKED = re.compile(r"^banked:[0-9A-Fa-f]+:[0-9A-Fa-f]+$")


@dataclass
class KnownIds:
    """The ids one source knows, keyed by stage."""

    #: ``stage -> {plugin ids}``.
    plugins: dict[str, set] = field(default_factory=dict)
    #: ``mapping id -> [pointer sizes it offers]``; a size outside them is one
    #: the Reading bar cannot show.
    mapping_sizes: dict[str, list] = field(default_factory=dict)
    #: ``retired id -> current id`` (``mapchar.plugins.aliases.RENAMED``).
    renamed: dict[str, str] = field(default_factory=dict)
    #: The table-file dialects (``legacy.DIALECTS``).
    dialects: tuple = ()
    project_version: int = 2
    #: ``"shipped snapshot"`` or ``"live registry"`` — quoted in the report so a
    #: reader knows how much an "unknown id" finding is worth.
    source: str = "snapshot"
    #: False when nothing could be loaded, which turns every id check off
    #: rather than reporting every id in the file as unknown.
    usable: bool = True
    #: Whether this source can see a user's own plugins. The snapshot cannot, so
    #: an unrecognised id is downgraded from "missing" to "not a built-in".
    authoritative: bool = False
    #: ``stage -> {ids}`` the project's ``plugins/`` folder declares.
    local: dict = field(default_factory=dict)
    #: Stages whose project folder holds code plugins, whose ids cannot be read
    #: without running them — an unknown id there may be one of theirs.
    opaque: set = field(default_factory=set)

    def current_id(self, plugin_id: str) -> str:
        """``plugin_id`` walked through the rename table, by its head: a
        parameterised id (``banked:8000:4000``) forwards on what precedes the
        first ``:``, as ``aliases.current_id`` does."""
        head, sep, params = plugin_id.partition(":")
        seen = {head}
        while (nxt := self.renamed.get(head)) is not None and nxt not in seen:
            head = nxt
            seen.add(nxt)
        return head + sep + params

    def has(self, stage: str, plugin_id: str) -> bool:
        """Whether ``stage`` has ``plugin_id``, or what it forwards to."""
        if stage == "mappings" and _BANKED.match(self.current_id(plugin_id)):
            return True
        bucket = self.plugins.get(stage, set()) | self.local.get(stage, set())
        return plugin_id in bucket or self.current_id(plugin_id) in bucket

    def is_renamed(self, plugin_id: str) -> bool:
        return self.current_id(plugin_id) != plugin_id

    def stage_of(self, plugin_id: str) -> str | None:
        """The stage ``plugin_id`` belongs to, or None if nothing has it — so
        "unknown id" can become "that is a *compression* id"."""
        for stage in STAGES:
            if self.has(stage, plugin_id):
                return stage
        return None

    def sizes_for(self, mapping_id: str) -> list | None:
        """The pointer sizes a mapping offers, or None where this source does
        not know — a banked id built on demand, or a project's own mapping."""
        current = self.current_id(mapping_id)
        if _BANKED.match(current):
            return [2, 3, 4]
        return self.mapping_sizes.get(mapping_id) or self.mapping_sizes.get(current)


def load_snapshot(path: str = _SNAPSHOT) -> KnownIds:
    """The ids that shipped with this linter."""
    try:
        with open(path, encoding="utf-8") as handle:
            body = json.load(handle)
    except (OSError, ValueError):
        # A missing or corrupt snapshot must not turn every id in the project
        # into a finding — it is the linter that is broken, not the project.
        return KnownIds(source="unavailable", usable=False)
    return KnownIds(
        plugins={stage: set(ids) for stage, ids in body.get("plugins", {}).items()},
        mapping_sizes=dict(body.get("mapping_sizes", {})),
        renamed=dict(body.get("renamed", {})),
        dialects=tuple(body.get("dialects", ())),
        project_version=body.get("project_version", 2),
        source="shipped snapshot",
    )


def load_live() -> KnownIds | None:
    """The running mapchar's registry, or None when it is not importable.

    Built-ins plus the user's plugin folder, which is what makes this source
    authoritative about a missing id where the snapshot is not.
    """
    try:
        from mapchar_lint.snapshot import registry_body
    except ImportError:  # pragma: no cover - the module ships with the package
        return None
    try:
        body = registry_body(user_plugins=True)
    except ImportError:
        return None
    return KnownIds(
        plugins={stage: set(ids) for stage, ids in body["plugins"].items()},
        mapping_sizes=dict(body["mapping_sizes"]),
        renamed=dict(body["renamed"]),
        dialects=tuple(body["dialects"]),
        project_version=body["project_version"],
        source="live registry",
        authoritative=True,
        opaque=set(body.get("opaque", ())),
    )


def for_project(ids: KnownIds, project_path: str) -> KnownIds:
    """``ids`` plus whatever the ``plugins/`` folder beside the project declares.

    A copy rather than a mutation: one run lints many files, and one project's
    formats must not vouch for the next project's ids.
    """
    root = join(dirname(abspath(project_path)), "plugins")
    if not isdir(root):
        return ids
    local: dict[str, set] = {}
    opaque: set = set()
    for stage in STAGES:
        folder = join(root, stage)
        if not isdir(folder):
            continue
        for name in sorted(listdir(folder)):
            if name.startswith("_"):
                continue
            stem, ext = splitext(name)
            if ext == ".toml":
                local.setdefault(stage, set()).add(_toml_id(join(folder, name), stem))
            elif ext == ".tbl" and stage == "charsets":
                local.setdefault(stage, set()).add(stem)
            elif ext == ".py":
                opaque.add(stage)
    if not local and not opaque:
        return ids
    return replace(ids, local=local, opaque=ids.opaque | opaque)


def _toml_id(path: str, default: str) -> str:
    """A preset's ``id``, or its file name — what ``discovery._build_preset``
    registers it under. Read with a pattern rather than a TOML parser so the
    linter keeps to the standard library on 3.10."""
    try:
        with open(path, encoding="utf-8-sig") as handle:
            for line in handle:
                m = re.match(r"""^\s*id\s*=\s*(["'])(.*?)\1\s*(#.*)?$""", line)
                if m:
                    return m.group(2)
                if line.lstrip().startswith("["):
                    break  # the id is a top-level key; a table has begun
    except OSError:
        pass
    return default


def known_ids(prefer_live: bool) -> KnownIds:
    """The id source to check against.

    ``prefer_live`` falls back to the snapshot rather than failing: asking for
    the live registry and not having mapchar installed should degrade the id
    checks, not the run.
    """
    if prefer_live:
        live = load_live()
        if live is not None:
            return live
    return load_snapshot()

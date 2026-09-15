"""Ids a plugin used to be called, and what it is called now.

A plugin id is a **compatibility surface**, not an implementation detail.
Things outside this code hold one: a saved project names the container, the
compression and the mapping every block was read through, and a user's own
preset TOML names the engine it supplies parameters for. Rename a plugin with
no forwarding address and those stop resolving — the project opens with its
stages degraded to pass-throughs, which reads as data loss even though the
bytes are untouched.

So a rename is a **two-part change**: the new id, and a row here. Nothing else
has to know, because every lookup goes through
:meth:`~mapchar.plugins.registry.Registry.plugin`, which consults this table
when a name misses, and a project's ids are walked through
:func:`current_id` as it loads.

**Aliases are permanent.** They cost one dict entry and a lookup that was
already failing; retiring one only breaks files that still work today. Add
rows, do not prune them.

Two rules keep the table honest, both checked by the tests:

- **No alias may name an id that exists.** A live plugin shadowing an alias
  means the alias never fires, and one string meaning two things at once is how
  a rename quietly half-lands.
- **Every target must resolve, and to a live id rather than to another retired
  one.** Rename something twice and the first row is re-pointed at the final
  name. A target that is itself a key here still resolves —
  :func:`current_id` walks the chain — but it reads as a pattern to copy, so
  the table stays **flat** and the walk is a backstop rather than the
  mechanism.
"""

from __future__ import annotations

from dataclasses import replace

from mapchar.core.block import BlockConfig

# old id -> the id that behaviour has now. Grouped by the change that retired
# the old name, newest last, because the reason is the only thing that makes a
# row reviewable. Empty while no shipped id has been renamed.
RENAMED: dict[str, str] = {}


def current_id(plugin_id: str) -> str:
    """The id ``plugin_id`` is known by now, following a chain of renames.

    Unknown ids come back unchanged: this answers "has this been renamed?", not
    "does this exist?". A plugin the registry genuinely hasn't got still has to
    degrade to a pass-through, and telling the two apart is the caller's job.

    **A parameterised id is forwarded by its head.** A banked mapping names its
    numbers inside the id (``banked:<base>:<size>``,
    :func:`~mapchar.plugins.builtins.mappings.parse_banked`), so an exact
    lookup could never match one — every set of numbers would need its own
    row. Everything before the first ``:`` is what is looked up and the rest
    rides along unchanged, so one row forwards every parameterisation. Keys
    here are therefore bare heads;
    a key with a ``:`` in it can never fire.

    The walk carries a seen-set rather than trusting a hand-edited table to
    terminate: a build between the wrong edit and the test that catches it
    should open files rather than hang on a lookup.
    """
    head, sep, params = plugin_id.partition(":")
    seen = {head}
    while (nxt := RENAMED.get(head)) is not None:
        if nxt in seen:
            # A cycle is a bug in the table, not in the file being opened, so
            # the file still opens — with the last id the walk reached.
            break
        head = nxt
        seen.add(nxt)
    return head + sep + params


def current_config_ids(config: BlockConfig) -> BlockConfig:
    """``config`` with its source's mapping id forwarded through the table.

    A block's mapping is the one plugin id a project stores inside its config
    string rather than beside it, so it needs the same forwarding the container
    and compression ids get.
    """
    source = config.source
    old = getattr(source, "mapping_id", None)
    if old is None:
        return config
    new = current_id(old)
    if new == old:
        return config
    return replace(config, source=replace(source, mapping_id=new))

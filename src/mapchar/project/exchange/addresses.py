"""Moving a block between the addresses a file uses and the ones it uses.

Cartographer and Atlas both address the **file**; a block addresses the
payload the container yields, which drops the file's header. So an import
subtracts the header and an export adds it back, in the one place both formats
reach for it.
"""

from __future__ import annotations

from dataclasses import replace

from mapchar.core.block import (
    BlockConfig,
    NestedPointerSource,
    PointerListSource,
    PointerSource,
    PointerTableSource,
    RangeSource,
)


def shift_config(config: BlockConfig, delta: int) -> BlockConfig:
    """The same block with every file address moved by ``delta`` bytes."""
    if not delta:
        return config
    src = config.source
    if isinstance(src, RangeSource | PointerTableSource | NestedPointerSource):
        src = replace(src, start=src.start + delta, stop=src.stop + delta)
    elif isinstance(src, PointerListSource):
        src = replace(src, addresses=tuple(a + delta for a in src.addresses))
    # A nested source's inner pointers count from a base its outer pointers
    # reach, which the outer offset moves with everything else.
    if isinstance(src, PointerSource) and src.mapping_id == "linear":
        src = replace(src, offset=src.offset + delta)
    return replace(
        config,
        source=src,
        bound=(config.bound + delta) if config.bound is not None else None,
        skips=tuple((a + delta, b + delta) for a, b in config.skips),
    )

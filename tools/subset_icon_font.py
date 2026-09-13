#!/usr/bin/env python3
"""Rebuild the bundled icon font from the upstream face, keeping only our glyphs.

mapChar ships Material Symbols Outlined cut down to the codepoints in
:class:`mapchar.ui.glyphs.Glyph` — a few tens of KB where the upstream variable font is
10.6 MB. That subset is a build artifact checked into the tree, so **a new
``Glyph`` member is not in the shipped font until this is re-run**; it will draw
as nothing until then (``tests/test_icon_font.py`` fails loudly when it does).

The codepoints come from the enum itself rather than a list kept alongside it,
which is the whole point: the two cannot disagree.

Usage::

    export UV_PROJECT_ENVIRONMENT=.venv-linux
    # https://github.com/google/material-design-icons -> variablefont/
    uv run --with fonttools tools/subset_icon_font.py \\
        ~/Downloads/MaterialSymbolsOutlined.ttf

The variable axes are deliberately **kept** (``wght`` above all — mapChar draws
the icons at 300, see ``ui/icon_font.py``), so this subsets the glyph set only
and never instantiates a static instance.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET = (
    REPO / "src" / "mapchar" / "resources" / "fonts" / "material-symbols-subset.ttf"
)


def codepoints() -> list[str]:
    """The codepoints mapChar draws, as the ``U+XXXX`` strings pyftsubset wants."""
    sys.path.insert(0, str(REPO / "src"))
    from mapchar.ui.glyphs import Glyph  # noqa: PLC0415 - needs the path set above

    return [f"U+{ord(glyph.value):04X}" for glyph in Glyph]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source", type=Path, help="the full upstream variable font (.ttf)"
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=TARGET, help=f"where to write ({TARGET})"
    )
    args = parser.parse_args()
    if not args.source.is_file():
        print(f"no such font: {args.source}", file=sys.stderr)
        return 1

    unicodes = codepoints()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fontTools.subset",
            str(args.source),
            f"--output-file={args.output}",
            f"--unicodes={','.join(u.removeprefix('U+') for u in unicodes)}",
            # Icons are drawn one codepoint at a time, never shaped as text, so
            # every layout feature in the face is dead weight. The ligatures that
            # let a web page write the icon's *name* go with them.
            "--layout-features=",
            "--no-hinting",
            "--desubroutinize",
            "--recalc-bounds",
        ],
        check=False,
    )
    if result.returncode != 0:
        return result.returncode
    size = args.output.stat().st_size
    print(f"{args.output.relative_to(REPO)}: {len(unicodes)} glyphs, {size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

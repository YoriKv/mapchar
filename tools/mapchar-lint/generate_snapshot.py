"""Regenerate ``src/mapchar_lint/data/registry.json`` from the live registry.

Run from the mapchar repo, in an environment where mapchar is importable::

    uv run python tools/mapchar-lint/generate_snapshot.py

The linter checks plugin ids against this file whenever mapchar is not
installed beside it, which is the case it is built for. The snapshot is
therefore allowed to be a copy — but not a *stale* copy, so
``tests/test_lint_snapshot.py`` in the mapchar suite fails the moment the
built-in registry and this file disagree. Regenerating is a chore, not a
judgement call: run it whenever the suite says to.

Only **built-ins** are captured. A user's own plugins are theirs and vary by
machine, which is what ``--live`` is for.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
SNAPSHOT = HERE / "src" / "mapchar_lint" / "data" / "registry.json"


def main() -> int:
    sys.path.insert(0, str(HERE / "src"))
    from mapchar_lint.snapshot import registry_body

    try:
        body = registry_body()
    except ImportError as exc:
        print(f"mapchar is not importable: {exc}", file=sys.stderr)
        print("Run this from the mapchar repo with its environment.", file=sys.stderr)
        return 2
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    with open(SNAPSHOT, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(body, handle, indent=2, sort_keys=True)
        handle.write("\n")
    count = sum(len(ids) for ids in body["plugins"].values())
    print(f"{SNAPSHOT}: {count} plugins")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

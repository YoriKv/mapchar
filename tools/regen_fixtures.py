"""Regenerate the abcde verification fixtures.

Writes the synthetic ROM, tables and Cartographer command file under
``tests/fixtures/abcde/`` and, when ``../abcde/abcde.pl`` and perl are
available, runs abcde over them and stores its dump as ``expected.txt``.
Run from anywhere: ``uv run python tools/regen_fixtures.py``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "abcde")
ABCDE = os.path.join(os.path.dirname(ROOT), "abcde", "abcde.pl")

ROM = (
    b"\x01\x02\x03\xff"  # foobarcat[end]
    b"\x02\x01\xfe\x03\xff"  # barfoo[line]cat[end]
    b"\xab\x01\x02\xab\x03\xff"  # switches into ItemNames
    b"\x01\x02\x03\x01\x02\x03\x01\x02"  # fixed strings at $F
    b"\xab\x02\xff"  # $17: switch then end
    b"\x01\xff\x00\x00\x02\xff\x00\x00"  # $1A: realigned strings (multiple 4)
    + b"\x00"
    * 64
)

TABLES = {
    "main.tbl": (
        "@main\n01=foo\n02=bar\n03=cat\n/FF=[end]\\n\nFE=[line]\\n\n"
        "!AB=<[Item Name:]>,<@ItemNames>:1\n"
    ),
    "items.tbl": "@ItemNames\n01=[Potion]\n02=[HolyHandGrenadeOfAntioch]\n03=[Sword]\n",
}

COMMANDS = """\
#SUB TABLE: items.tbl
#GAME NAME: Synthetic
#BLOCK NAME: Normal
#TYPE: NORMAL
#METHOD: RAW
#SCRIPT START: 0
#SCRIPT STOP: $F
#TABLE: main.tbl
#COMMENTS: No
#SHOW END ADDRESS: No
#END BLOCK
#BLOCK NAME: Fixed
#TYPE: FIXED_STRING
#STRING LENGTH: 5
#STRING END: Yes
#END CTRL: [end]
#METHOD: RAW
#SCRIPT START: $F
#SCRIPT STOP: $17
#TABLE: main.tbl
#COMMENTS: No
#SHOW END ADDRESS: No
#END BLOCK
#BLOCK NAME: Lines
#TYPE: FIXED_STRING && FIXED_LINE
#STRING LENGTH: 8
#STRING END: No
#LINE LENGTH: 3
#LINE END: Yes
#LINE CTRL: [line]
#METHOD: RAW
#SCRIPT START: $F
#SCRIPT STOP: $17
#TABLE: main.tbl
#COMMENTS: No
#SHOW END ADDRESS: No
#END BLOCK
#BLOCK NAME: Switch
#TYPE: NORMAL
#METHOD: RAW
#SCRIPT START: $17
#SCRIPT STOP: $1A
#TABLE: main.tbl
#COMMENTS: No
#SHOW END ADDRESS: No
#END BLOCK
#BLOCK NAME: Realigned
#TYPE: NORMAL
#METHOD: RAW
#SCRIPT START: $1A
#SCRIPT STOP: $22
#TABLE: main.tbl
#COMMENTS: No
#SHOW END ADDRESS: No
#END BLOCK
"""


def main() -> int:
    os.makedirs(FIXTURES, exist_ok=True)
    with open(os.path.join(FIXTURES, "rom.bin"), "wb") as f:
        f.write(ROM)
    for name, text in TABLES.items():
        with open(
            os.path.join(FIXTURES, name), "w", encoding="utf-8", newline="\n"
        ) as f:
            f.write(text)
    with open(
        os.path.join(FIXTURES, "cmd.txt"), "w", encoding="utf-8", newline="\n"
    ) as f:
        f.write(COMMANDS)
    perl = shutil.which("perl")
    if perl is None or not os.path.exists(ABCDE):
        print("abcde or perl not available; inputs written, expected.txt kept")
        return 0
    result = subprocess.run(
        [
            perl,
            ABCDE,
            "-cm",
            "abcde::Cartographer",
            "rom.bin",
            "cmd.txt",
            "expected",
            "-s",
        ],
        cwd=FIXTURES,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stdout, result.stderr, file=sys.stderr)
        return 1
    print("expected.txt regenerated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

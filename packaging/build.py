#!/usr/bin/env python3
"""Freeze mapChar into a standalone app with PyInstaller.

This is the project's single build recipe: ``.github/workflows/release.yml``
runs this same script on each runner, so a local build is exactly what a
release ships — there is no second copy of the flags to drift out of sync.

    python3 packaging/build.py             # -> dist/mapchar (or dist/mapchar.app)
    python3 packaging/build.py --archive   # ... plus the release archive

PyInstaller freezes the interpreter it runs under, so the build always targets
the OS it runs on and there is no cross-compiling: build the Windows app from
Windows, the Linux app from Linux/WSL, the macOS app from macOS.

The script imports only the stdlib and drives ``uv run --with pyinstaller``, so
it needs no environment of its own — any Python 3.9+ plus ``uv`` on PATH will
do, including a bare system interpreter.
"""

from __future__ import annotations

import argparse
import os
import platform
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import NoReturn

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST = REPO_ROOT / "dist"
BUILD = REPO_ROOT / "build"
VERSION_FILE = REPO_ROOT / "src" / "mapchar" / "__init__.py"
ENTRY_POINT = REPO_ROOT / "src" / "mapchar" / "__main__.py"
APP_NAME = "mapchar"

# PyInstaller stamps LSMinimumSystemVersion from the build Python's own
# deployment target (11.0 on arm64), but the app's real floor is Qt's: PySide6
# 6.10+ wheels are macosx_13_0. Stamping the honest minimum gets older systems a
# clear "requires macOS 13" dialog instead of a launch crash.
MACOS_MIN_VERSION = "13.0"


# ── Environment ──────────────────────────────────────────────────────────────
def build_env() -> dict:
    """The environment for ``uv``, with the WSL venv footgun defused.

    This checkout keeps two virtualenvs: ``.venv`` for Windows (the default
    name, so PyCharm auto-detects it) and ``.venv-linux`` for WSL. A bare ``uv
    run`` on the Linux side targets ``.venv`` and overwrites the Windows env
    with Linux binaries, so point uv at the Linux one unless the caller (or CI,
    where neither exists) has already chosen.
    """
    env = os.environ.copy()
    if sys.platform.startswith("linux") and "UV_PROJECT_ENVIRONMENT" not in env:
        if (REPO_ROOT / ".venv-linux").is_dir():
            env["UV_PROJECT_ENVIRONMENT"] = ".venv-linux"
    return env


def read_version() -> str:
    """``__version__`` from the package — the single source of truth."""
    match = re.search(
        r'^__version__\s*=\s*"([^"]+)"', VERSION_FILE.read_text(encoding="utf-8"), re.M
    )
    if not match:
        die(f"could not read __version__ from {VERSION_FILE}")
    return match.group(1)


# ── Target description ───────────────────────────────────────────────────────
def target_slug() -> str:
    """The name this platform's release asset is filed under."""
    if sys.platform == "win32":
        return "win"
    if sys.platform == "darwin":
        # uv's Python is single-arch, so a build is native-only; the two macOS
        # assets come from two runners, told apart by the machine they ran on.
        return "mac-arm64" if platform.machine() == "arm64" else "mac-intel"
    if sys.platform.startswith("linux"):
        return "linux"
    die(f"unsupported platform: {sys.platform}")


def icon_option() -> list:
    """``--icon`` arguments, if this platform has a build-time icon."""
    if sys.platform == "win32":
        # Embedded into the .exe and shown in Explorer/taskbar.
        return ["--icon", str(REPO_ROOT / "packaging" / "mapchar.ico")]
    if sys.platform == "darwin":
        # Baked into mapchar.app's Info.plist and shown in the Dock/Finder.
        return ["--icon", str(REPO_ROOT / "packaging" / "mapchar.icns")]
    # Linux has no build-time icon; the app sets its window icon at runtime.
    return []


def app_path() -> Path:
    """What PyInstaller leaves in ``dist/`` for this platform."""
    if sys.platform == "darwin":
        return DIST / f"{APP_NAME}.app"
    return DIST / APP_NAME


def launcher_path() -> Path:
    """The executable a user actually double-clicks / runs."""
    if sys.platform == "darwin":
        return app_path() / "Contents" / "MacOS" / APP_NAME
    if sys.platform == "win32":
        return app_path() / f"{APP_NAME}.exe"
    return app_path() / APP_NAME


# ── Shell helpers ────────────────────────────────────────────────────────────
def info(message: str = "") -> None:
    print(message, flush=True)


def die(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def show(cmd: list) -> str:
    parts = [str(part) for part in cmd]
    return subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)


def run(cmd: list, *, dry_run: bool) -> None:
    info(f"  $ {show(cmd)}")
    if dry_run:
        return
    result = subprocess.run([str(part) for part in cmd], cwd=REPO_ROOT, env=build_env())
    if result.returncode != 0:
        die(f"command failed with exit code {result.returncode}: {show(cmd)}")


# ── Build steps ──────────────────────────────────────────────────────────────
def pyinstaller_command() -> list:
    """The freeze command — identical on every platform bar the icon.

    ``--windowed`` suppresses the console window on Windows/macOS (a no-op on
    Linux). ``--collect-data`` is required because PyInstaller only follows
    imports: the package data under ``mapchar/resources`` would otherwise be
    left out and the frozen app would start without it. PySide6's bundled hooks
    pull in the Qt plugins.

    pyinstaller comes in ephemerally via ``--with``, so it need not live in the
    dev dependency group or uv.lock.
    """
    return [
        "uv",
        "run",
        "--with",
        "pyinstaller",
        "pyinstaller",
        "--name",
        APP_NAME,
        "--windowed",
        "--noconfirm",
        "--collect-data",
        "mapchar",
        *icon_option(),
        str(ENTRY_POINT),
    ]


def stamp_macos_minimum(*, dry_run: bool) -> None:
    """Write the honest LSMinimumSystemVersion into the bundle, then re-sign.

    Editing Info.plist invalidates the ad-hoc signature PyInstaller applied, and
    arm64 refuses to launch an unsigned binary — so the two always go together.
    """
    plist_path = app_path() / "Contents" / "Info.plist"
    info(f"  Info.plist: LSMinimumSystemVersion = {MACOS_MIN_VERSION}")
    if not dry_run:
        with plist_path.open("rb") as handle:
            plist = plistlib.load(handle)
        plist["LSMinimumSystemVersion"] = MACOS_MIN_VERSION
        with plist_path.open("wb") as handle:
            plistlib.dump(plist, handle)
    run(["codesign", "--force", "--deep", "--sign", "-", app_path()], dry_run=dry_run)


def make_archive(*, dry_run: bool) -> Path:
    """Pack the built app into this platform's release asset."""
    slug = target_slug()
    if sys.platform == "darwin":
        archive = DIST / f"{APP_NAME}-{slug}.zip"
        # ditto, not zipfile: a .app is full of symlinks and executable bits
        # that a plain zip writer silently flattens, leaving an unlaunchable
        # bundle. --keepParent keeps mapchar.app as the archive's top level.
        run(
            ["ditto", "-c", "-k", "--keepParent", app_path(), archive],
            dry_run=dry_run,
        )
        return archive

    if sys.platform == "win32":
        archive = DIST / f"{APP_NAME}-{slug}.zip"
        info(f"  zip {app_path().name}/* -> {archive.name}")
        if not dry_run:
            # Contents at the archive root, no wrapping folder.
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
                for path in sorted(app_path().rglob("*")):
                    if path.is_file():
                        bundle.write(path, path.relative_to(app_path()).as_posix())
        return archive

    archive = DIST / f"{APP_NAME}-{slug}.tar.gz"
    info(f"  tar {app_path().name}/ -> {archive.name}")
    if not dry_run:
        # tarfile keeps symlinks as symlinks and preserves the exec bit, both of
        # which the Qt libraries in the onedir output need.
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(app_path(), arcname=APP_NAME)
    return archive


def clean(*, dry_run: bool) -> None:
    for path in (BUILD, DIST, REPO_ROOT / f"{APP_NAME}.spec"):
        if not path.exists():
            continue
        info(f"  rm -rf {path.relative_to(REPO_ROOT)}")
        if not dry_run:
            shutil.rmtree(path) if path.is_dir() else path.unlink()


# ── Entry point ──────────────────────────────────────────────────────────────
def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build.py",
        description=(
            "Freeze mapChar into a standalone app with PyInstaller, the same way "
            "the release workflow does. Builds for the OS it runs on."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python3 packaging/build.py            build dist/mapchar\n"
            "  python3 packaging/build.py -a         ... and the release archive\n"
            "  python3 packaging/build.py -c -n      preview a from-scratch build\n"
        ),
    )
    parser.add_argument(
        "-a",
        "--archive",
        action="store_true",
        help="also pack the app into the release archive (zip / tar.gz) in dist/",
    )
    parser.add_argument(
        "-c",
        "--clean",
        action="store_true",
        help="delete build/, dist/ and mapchar.spec first, for a from-scratch build",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="show what would run; change nothing",
    )
    args = parser.parse_args(argv)

    if not shutil.which("uv"):
        die("uv not found in PATH — see https://docs.astral.sh/uv/ to install it")
    if not ENTRY_POINT.is_file():
        die(f"{ENTRY_POINT} not found; is this a full checkout?")

    slug = target_slug()
    env = build_env()
    info("Build plan:")
    info(f"  repo:     {REPO_ROOT}")
    info(f"  version:  {read_version()}")
    info(f"  target:   {slug} ({platform.platform()})")
    info(f"  output:   {app_path().relative_to(REPO_ROOT)}")
    if "UV_PROJECT_ENVIRONMENT" in env:
        info(f"  uv env:   {env['UV_PROJECT_ENVIRONMENT']}")
    info()

    if args.clean:
        info("Cleaning previous build output ...")
        clean(dry_run=args.dry_run)

    info("Freezing with PyInstaller ...")
    run(pyinstaller_command(), dry_run=args.dry_run)

    if sys.platform == "darwin":
        info("Stamping the minimum macOS version ...")
        stamp_macos_minimum(dry_run=args.dry_run)

    archive = None
    if args.archive:
        info("Packing the release archive ...")
        archive = make_archive(dry_run=args.dry_run)

    info()
    if args.dry_run:
        info("(dry run) nothing was built.")
        return 0
    info(f"Built {app_path().relative_to(REPO_ROOT)}")
    info(f"  run it: {launcher_path().relative_to(REPO_ROOT)}")
    if archive is not None:
        size_mb = archive.stat().st_size / 1_000_000
        info(f"  archive: {archive.relative_to(REPO_ROOT)} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

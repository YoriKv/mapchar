# Building and releasing

How mapChar is frozen into a standalone app and published as a GitHub release.
The same scripts run locally and in CI.

## Building: `packaging/build.py`

The single PyInstaller recipe. It imports only the stdlib and drives
`uv run --with pyinstaller`, so any Python 3.9+ with `uv` on `PATH` runs it.

```bash
python3 packaging/build.py             # dist/mapchar (dist/mapchar.app on macOS)
python3 packaging/build.py --archive   # plus the release archive
python3 packaging/build.py -c -n       # preview a from-scratch build
```

- **Native only.** PyInstaller freezes the interpreter it runs under: build the
  Windows app on Windows, the Linux app on Linux/WSL, the macOS app on macOS.
- **Freeze.** Onedir, `--windowed`, `--collect-data mapchar` so the package
  data under `src/mapchar/resources/` ships.
- **Icons.** `packaging/mapchar.ico` (Windows, 16–256 px) and
  `packaging/mapchar.icns` (macOS, 16–1024 px) are embedded at build time.
  Linux has no build-time icon; on every platform the running app sets its
  window icon from `src/mapchar/resources/icons/app.png`.
- **macOS.** The bundle's `LSMinimumSystemVersion` is stamped to 13.0 (the
  PySide6 wheel floor) and the app is ad-hoc re-signed.
- **Archives.** `--archive` writes `dist/mapchar-win.zip`,
  `dist/mapchar-linux.tar.gz`, `dist/mapchar-mac-arm64.zip` or
  `dist/mapchar-mac-intel.zip`.
- **WSL.** On Linux it sets `UV_PROJECT_ENVIRONMENT=.venv-linux` when that
  folder exists and the variable is unset.
- `build/`, `dist/` and `mapchar.spec` are gitignored.

## Cutting a release: `release.sh`

```bash
./release.sh                 # patch: x.y.Z -> x.y.(Z+1)
./release.sh minor           # x.Y.z -> x.(Y+1).0
./release.sh major -n        # preview
```

1. Bumps `__version__` in `src/mapchar/__init__.py`, stamps the
   `## vX.Y.Z - unreleased` heading in `CHANGELOG.md` with today's date, and
   commits both as `Release vX.Y.Z`.
2. Tags `vX.Y.Z` (annotated).
3. Pushes the branch, then the tag, to `origin`.

- **Write the notes first.** It refuses to run unless `CHANGELOG.md` has a
  `## vX.Y.Z` section for the new version.
- **Other refusals:** modified tracked files (`-f` overrides and still commits
  only the bump), a detached HEAD, a missing `origin` remote, or a tag that
  already exists locally or on `origin`.
- `-y` skips the confirmation prompt.

## The release workflow: `.github/workflows/release.yml`

- **Trigger.** A pushed `v*` tag, or a manual run (`workflow_dispatch`), which
  builds without publishing.
- **Build.** A matrix over `windows-latest`, `ubuntu-latest`, `macos-latest`
  (arm64) and `macos-15-intel` runs `uv sync --frozen` and
  `packaging/build.py --archive`, and uploads the archive. Linux first installs
  the Qt runtime libraries PyInstaller has to bundle.
- **Release.** On a tag, one job collects every archive and publishes a single
  GitHub release. Its body is the tag's `CHANGELOG.md` section followed by
  GitHub's generated notes.
- It runs no tests or lint; those stay local.

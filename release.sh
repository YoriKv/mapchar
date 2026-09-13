#!/usr/bin/env bash
#
# release.sh — cut a release for mapChar.
#
# Bumps the version, stamps the changelog, commits, tags, and pushes to the
# current remote (origin). The tag push triggers the "Release" GitHub Actions
# workflow (.github/workflows/release.yml), which builds the app for Windows,
# Linux, and macOS with PyInstaller and publishes a GitHub Release.
#
# What it does, in order:
#   1. Bump __version__ in src/mapchar/__init__.py (the single source of truth —
#      pyproject.toml reads it dynamically), stamp CHANGELOG.md's
#      "## vX.Y.Z - unreleased" heading with today's date, and commit both.
#      (Refuses to run if CHANGELOG.md has no section for the new version —
#      write the release notes first.)
#   2. Create an annotated "vX.Y.Z" tag on that commit.
#   3. Push the branch and the tag to origin. The tag push fires the build.
#
# Run it from anywhere in the repo — it relocates to the repo root itself.

set -euo pipefail

# ── Config / defaults ───────────────────────────────────────────────────────
REMOTE="origin"
VERSION_FILE="src/mapchar/__init__.py"
BUMP="patch"
FORCE=0
DRY_RUN=0
ASSUME_YES=0

SCRIPT_NAME="$(basename "$0")"

usage() {
  cat <<EOF
Usage: $SCRIPT_NAME [major|minor|patch] [options]

Cut a release: bump __version__ in $VERSION_FILE, stamp the matching
CHANGELOG.md section's "unreleased" marker with today's date, commit both, tag
vX.Y.Z, and push the branch + tag to "$REMOTE". The tag push triggers the
GitHub Actions release build.

Version bump (positional, default: patch):
  patch            x.y.Z  ->  x.y.(Z+1)   (default)
  minor            x.Y.z  ->  x.(Y+1).0
  major            X.y.z  ->  (X+1).0.0

Options:
  -f, --force      Skip the uncommitted-changes check. Only the version bump and
                   the CHANGELOG.md date stamp are committed; any other modified
                   files are left uncommitted and are NOT part of the release.
  -n, --dry-run    Show what would happen; change nothing.
  -y, --yes        Don't prompt for confirmation before committing/pushing.
  -h, --help       Show this help and exit.

Examples:
  $SCRIPT_NAME                  # patch release (e.g. 0.1.0 -> 0.1.1)
  $SCRIPT_NAME minor            # minor release (e.g. 0.1.1 -> 0.2.0)
  $SCRIPT_NAME major --dry-run  # preview a major release
EOF
}

# ── Logging helpers ─────────────────────────────────────────────────────────
info() { printf '%s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

# ── Argument parsing (order-independent) ─────────────────────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    major|minor|patch) BUMP="$1" ;;
    -f|--force)        FORCE=1 ;;
    -n|--dry-run)      DRY_RUN=1 ;;
    -y|--yes)          ASSUME_YES=1 ;;
    -h|--help)         usage; exit 0 ;;
    --)                shift; break ;;
    -*)                printf 'error: unknown option: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
    *)                 printf 'error: unexpected argument: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# ── Preconditions ───────────────────────────────────────────────────────────
command -v git >/dev/null 2>&1 || die "git not found in PATH"

# Relocate to the repo root so every path below is unambiguous.
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || die "not inside a git repository"
cd "$REPO_ROOT"
[ -f "$VERSION_FILE" ] || die "$VERSION_FILE not found at repo root ($REPO_ROOT)"

# Must be on a branch (we push it alongside the tag).
BRANCH="$(git symbolic-ref --short -q HEAD || true)"
[ -n "$BRANCH" ] || die "HEAD is detached; check out a branch before releasing"

git remote get-url "$REMOTE" >/dev/null 2>&1 || die "remote '$REMOTE' is not configured"

# Uncommitted-changes check. Only tracked-file modifications count (untracked
# files — editor/tool dirs, build output — never end up in the release, so they
# don't block it). --force bypasses the check but still commits ONLY the bump.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  if [ "$FORCE" -eq 1 ]; then
    warn "working tree has uncommitted changes; --force given, continuing."
    warn "only the version bump + CHANGELOG.md stamp will be committed — other changes stay uncommitted."
  else
    die "working tree has uncommitted changes. Commit/stash them, or pass --force to override."
  fi
fi

# ── Compute versions ────────────────────────────────────────────────────────
# __version__ = "X.Y.Z" — take the first quoted string on that line.
CURRENT_VERSION="$(sed -nE 's/^__version__[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$VERSION_FILE" | head -n1)"
[ -n "$CURRENT_VERSION" ] || die "could not read __version__ from $VERSION_FILE"

IFS='.' read -r MAJ MIN PAT <<EOF
$CURRENT_VERSION
EOF
case "$MAJ$MIN$PAT" in
  *[!0-9]*|"") die "unparseable version: '$CURRENT_VERSION' (expected X.Y.Z)" ;;
esac
case "$BUMP" in
  major) MAJ=$((MAJ + 1)); MIN=0; PAT=0 ;;
  minor) MIN=$((MIN + 1)); PAT=0 ;;
  patch) PAT=$((PAT + 1)) ;;
esac
NEW_VERSION="$MAJ.$MIN.$PAT"
TAG="v$NEW_VERSION"

# Tag must not already exist — locally or on the remote.
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  die "tag $TAG already exists locally"
fi
if remote_tag="$(git ls-remote --tags "$REMOTE" "refs/tags/$TAG" 2>/dev/null)"; then
  [ -z "$remote_tag" ] || die "tag $TAG already exists on '$REMOTE'"
fi

# ── Changelog section ───────────────────────────────────────────────────────
# The release body is CHANGELOG.md's "## v<version>" section (the workflow
# extracts it on tag push), so the section must exist before we cut the release.
# At release time the heading's "unreleased" marker (either casing) is stamped
# with today's date. An already-dated heading is left as-is.
[ -f CHANGELOG.md ] || die "CHANGELOG.md not found at repo root"
grep -qE "^## $TAG([^0-9.]|\$)" CHANGELOG.md \
  || die "CHANGELOG.md has no \"## $TAG\" section — write the release notes before releasing."
TODAY="$(date +%Y-%m-%d)"

# ── Plan summary ────────────────────────────────────────────────────────────
info "Release plan:"
info "  repo:      $REPO_ROOT"
info "  remote:    $REMOTE — branch $BRANCH"
info "  bump:      $BUMP"
info "  version:   $CURRENT_VERSION -> $NEW_VERSION"
info "  tag:       $TAG  (triggers the release build on push)"
info "  changelog: stamp \"## $TAG - unreleased\" -> \"## $TAG - $TODAY\""
info ""

if [ "$DRY_RUN" -eq 1 ]; then
  info "(dry run) nothing changed. Would:"
  info "  1. set __version__ in $VERSION_FILE to $NEW_VERSION, stamp CHANGELOG.md's"
  info "     \"## $TAG - unreleased\" heading to \"## $TAG - $TODAY\", and commit both on '$BRANCH'"
  info "  2. git tag -a $TAG -m \"Release $TAG\""
  info "  3. git push $REMOTE $BRANCH"
  info "  4. git push $REMOTE $TAG   # triggers the build"
  exit 0
fi

# ── Confirm (unless -y) ─────────────────────────────────────────────────────
if [ "$ASSUME_YES" -ne 1 ]; then
  printf 'Proceed with release %s? [y/N] ' "$TAG"
  reply=""
  read -r reply </dev/tty || reply=""
  case "$reply" in
    y|Y|yes|YES) ;;
    *) die "aborted." ;;
  esac
fi

# ── Execute ─────────────────────────────────────────────────────────────────
# Restore the touched files to HEAD (undo a partial bump/stamp).
rollback() { git checkout -q HEAD -- "$VERSION_FILE" CHANGELOG.md 2>/dev/null || true; }

info "Bumping $VERSION_FILE to $NEW_VERSION ..."
if ! sed -i -E "s/^(__version__[[:space:]]*=[[:space:]]*\")[^\"]+(\".*)/\1$NEW_VERSION\2/" "$VERSION_FILE"; then
  rollback
  die "failed to write the new version to $VERSION_FILE."
fi

info "Stamping CHANGELOG.md (\"## $TAG - unreleased\" -> \"## $TAG - $TODAY\") ..."
# Match either casing of the marker ("unreleased" / "Unreleased"); [Uu] is
# portable across GNU and BSD sed (unlike the GNU-only case-insensitive flag).
if ! sed -i -E "s/^## $TAG[[:space:]]*-[[:space:]]*[Uu]nreleased[[:space:]]*\$/## $TAG - $TODAY/" CHANGELOG.md; then
  rollback
  die "failed to stamp CHANGELOG.md."
fi

info "Committing version bump + changelog ..."
if ! git commit -q -m "Release $TAG" -- "$VERSION_FILE" CHANGELOG.md; then
  rollback
  die "git commit failed; reverted $VERSION_FILE and CHANGELOG.md."
fi

info "Tagging $TAG ..."
if ! git tag -a "$TAG" -m "Release $TAG"; then
  warn "git tag failed; the release commit is in place but untagged. Undo with: git reset --soft HEAD^"
  die "could not create tag $TAG."
fi

info "Pushing branch $BRANCH to $REMOTE ..."
if ! git push "$REMOTE" "$BRANCH"; then
  warn "branch push failed. Nothing has triggered a build yet. Retry with:"
  warn "  git push $REMOTE $BRANCH && git push $REMOTE $TAG"
  die "push of branch '$BRANCH' to '$REMOTE' failed."
fi

info "Pushing tag $TAG to $REMOTE (triggers the release build) ..."
if ! git push "$REMOTE" "$TAG"; then
  warn "the branch is pushed, but pushing tag $TAG failed — the build did NOT trigger. Retry with:"
  warn "  git push $REMOTE $TAG"
  die "tag push to '$REMOTE' failed."
fi

# Build the Actions/Releases URLs from the remote URL (supports ssh + https).
slug="$(git remote get-url "$REMOTE" | sed -E 's#^git@[^:]+:##; s#^https?://[^/]+/##; s#\.git$##')"

info ""
info "Released $TAG. GitHub Actions is building the apps and will publish the release."
if [ -n "$slug" ]; then
  info "  https://github.com/$slug/actions"
  info "  https://github.com/$slug/releases"
fi

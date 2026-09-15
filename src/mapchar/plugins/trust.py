"""Whether a code plugin may run: approved digests, and the gate over them."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable


class TrustStore:
    """Approved SHA-256 digests of code plugins, in a JSON file.

    Trust is keyed on the **content hash**, not the path: approving a plugin
    approves *that exact code*, so moving or renaming the file keeps trust and
    editing it does not. A corrupt or unreadable store starts empty rather than
    crashing — the worst case is re-prompting, never silently trusting.
    """

    def __init__(self, path: str | None):
        self.path = path
        self._digests: set[str] = set()
        # Paths approved during *this* run, so a plugin author can edit and
        # refresh a file they already said yes to without a prompt per save.
        # Empty at every launch, so changed code still prompts across runs.
        self._session_paths: set[str] = set()
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8-sig") as f:
                    self._digests = set(json.load(f).get("trusted", []))
            except (OSError, ValueError):
                self._digests = set()

    def is_trusted(self, digest: str) -> bool:
        return digest in self._digests

    def is_session_path(self, path: str) -> bool:
        """Whether this path was approved earlier in this run (the author loop)."""
        return path in self._session_paths

    def trust(self, digest: str, path: str | None = None, persist: bool = True) -> None:
        if path is not None:
            self._session_paths.add(path)
        if persist:
            self._digests.add(digest)
            self._save()

    def _save(self) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"trusted": sorted(self._digests)}, f, indent=2)


def digest_of(source: bytes) -> str:
    """The SHA-256 trust is keyed on: the exact bytes that would be executed."""
    return hashlib.sha256(source).hexdigest()


def is_approved(
    path: str,
    digest: str,
    trust: TrustStore | None,
    confirm: Callable[[str, str], bool] | None,
) -> bool:
    """Trusted already, or approved now (and then remembered). **Default deny.**

    No trust store and no confirm callback means nothing can say yes, so nothing
    runs: a gate that opens when its keeper is absent is not a gate, and the
    absent keeper is exactly the headless case — a test, a script, a build that
    never wired the prompt up.
    """
    if trust is not None and trust.is_trusted(digest):
        return True
    if trust is not None and trust.is_session_path(path):
        # The author loop: a path approved earlier this run reloads without a
        # prompt when its code changes. Across runs the new hash prompts again.
        trust.trust(digest, path)
        return True
    if confirm is not None and confirm(path, digest[:12]):
        if trust is not None:
            trust.trust(digest, path)
        return True
    return False

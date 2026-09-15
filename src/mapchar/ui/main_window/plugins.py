"""The plugin folders, their failures, and reloading them."""

from __future__ import annotations

import os

from mapchar.plugins.base import Stage
from mapchar.plugins.charsets import apply_charset
from mapchar.plugins.discovery import PLUGIN_README
from mapchar.project.workspace import Entry
from mapchar.ui.dialogs import TextDialog


class PluginsMixin:
    """The plugin folders, their failures, and reloading them.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _open_plugins_folder(self) -> None:
        if not self.plugin_dir:
            self._error("No plugin folder is configured.")
            return
        self._reveal(os.path.join(self.plugin_dir, PLUGIN_README))
        # Someone opening the folder is already asking where their plugin went,
        # which is the moment to say what did not load.
        self._alert_plugin_issues()

    def _alert_plugin_issues(self) -> None:
        """Say what in the plugins folder did not load — at startup, after a
        refresh, when a project's own folder is scanned, and from Open Plugins
        folder.

        Two surfaces, because there are two kinds of "did not load". A plugin
        that **broke** is news: the user meant it to run, it does not, and a
        dialog is the only thing they reliably read. A plugin they **declined**
        at the trust prompt did what they asked, and it declines again at every
        launch and every F5 for as long as the answer stands — so a modal there
        is the app arguing with a decision, forever. That one gets the status
        bar, which says the same thing to anyone wondering where their plugin
        went and nothing to anyone who is not.

        Declined files still appear in a failure dialog's list when there is one
        to show: the list is "what is not running", and leaving them out of it is
        how someone hunts a plugin that is sitting right there.
        """
        failed = [i for i in self._plugin_issues if not i.declined]
        declined = [i for i in self._plugin_issues if i.declined]
        if not failed:
            if declined:
                self.statusBar().showMessage(
                    f"{len(declined)} code plugin(s) not run — the trust prompt "
                    "was declined. File ▸ Refresh Plugins asks again.",
                    8000,
                )
            return
        TextDialog(
            "Plugin Issues",
            f"{len(failed)} plugin(s) failed to load. The rest of the app works "
            "normally; see the list below, or File ▸ Open Plugins Folder.\n\n"
            + "\n".join(f"• {i}" for i in [*failed, *declined]),
            self,
        ).exec()

    def _load_project_plugins(self, project_path: str | None) -> None:
        """Rebuild the registry for the project at ``project_path``.

        The ``plugins/`` folder beside a project file belongs to that project, so
        it is scanned as the project opens and dropped again when it closes —
        both ends go through here, and no entry is read through a registry from
        the project before. Called *before* the workspace is replaced: a restored
        entry may name a plugin only the project provides, and activating it
        reads immediately.

        Its code plugins pass the same trust gate as the user's own, so opening a
        project that carries one asks first.
        """
        if self._reload_plugins is None:
            return
        project_dir = os.path.dirname(project_path) if project_path else None
        self.registry, issues = self._reload_plugins(project_dir)
        self._plugin_issues = list(issues)
        self._registry_changed()
        self._alert_plugin_issues()

    def _refresh_plugins(self) -> None:
        if self._reload_plugins is None:
            self.statusBar().showMessage(
                "Plugins cannot be reloaded in this session", 4000
            )
            return
        project_dir = os.path.dirname(self.project_path) if self.project_path else None
        registry, issues = self._reload_plugins(project_dir)
        self.registry = registry
        self._plugin_issues = list(issues)
        self._registry_changed()
        kept = self._drop_clean_documents()
        for e in self.workspace.table_entries():
            # The file's own table as well as the live one: it is the baseline
            # the project's overlay is measured against, so a charset applied to
            # one and not the other would read as a user edit.
            for t in (e.table, e.file_table):
                if t is not None:
                    t.charset_applied = False
                    apply_charset(t, self.registry)
        if self._entry is not None:
            self._doc = self._load_document(self._entry)
            self._restore_session()
        self._refresh_view()
        message = "Plugins refreshed"
        if kept:
            message += f"; {kept} entry(ies) with unsaved edits kept as they are"
        self.statusBar().showMessage(message, 5000)
        self._alert_plugin_issues()

    def _registry_changed(self) -> None:
        """What lists the registry's plugins by name follows it: the charsets
        offered as tables, the pointer mappings."""
        self._reset_builtin_tables()
        self.reading_bar.set_mappings(self.registry.plugins(Stage.MAPPING))
        self._refresh_table_picks()

    def _drop_clean_documents(self) -> int:
        """Forget every cached document so the new registry re-reads it — except
        the ones holding unsaved edits. Returns how many were kept.

        A refresh is not a revert: translations, overtypes and status changes
        live only in the document, so dropping a dirty one throws work away while
        the entry still reads as edited. Those keep what they have, and re-read
        when the user next writes or closes them. A dirty entry's parents are
        kept too: a block settles its bytes through its file's buffer, so
        re-reading that from disk underneath it would strand the edits.
        """
        keep: set[int] = set()
        for entry in self.workspace.entries:
            if not entry.dirty:
                continue
            node: Entry | None = entry
            while node is not None:
                keep.add(id(node))
                node = node.parent
        for entry in self.workspace.entries:
            if id(entry) not in keep:
                self.workspace.drop_document(entry)
        return sum(1 for entry in self.workspace.entries if entry.dirty)

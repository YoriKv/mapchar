"""Reading a block's strings out of its bytes, and the caches that spare it."""

from __future__ import annotations

from mapchar.core.block import BlockConfig
from mapchar.core.document import Document
from mapchar.core.table import TableSet
from mapchar.pipeline.extract import extract, respell_fixed_end
from mapchar.project.entry import Entry, unjoined_states


class ExtractionMixin:
    """Reading a block's strings out of its bytes, and the caches that spare it.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _extract_current(
        self, entry: Entry, doc: Document, tables: TableSet | None
    ) -> bool:
        """Read the block's strings, unless what they are read from is as it was
        the last time; ``True`` when the strings changed."""
        cfg = entry.config
        if cfg is None:
            changed = bool(doc.strings) or doc.extraction_key is not None
            doc.strings = []
            doc.extraction_key = None
            return changed
        if tables is None:
            # The table set is gone: removed, failed to reload, or never picked.
            # The string records stay exactly as they are and are stashed where
            # a document drop cannot reach them — every translation of every
            # block on that table would otherwise go with it. The block reads
            # as unreadable in the Files panel until the table comes back.
            entry.stash_strings(doc)
            changed = doc.extraction_key is not None
            doc.extraction_key = None
            message = (
                f"{entry.name}: start table @{cfg.table_id} is not loaded; its "
                "strings are kept but cannot be re-read."
            )
            held = sum(
                1
                for st in (entry.pending_strings or {}).values()
                if st.translation is not None
            )
            if held:
                # They are not in the bytes and cannot be put there until the
                # table is back. The project keeps them meanwhile, so a save
                # writes them out again and the next readable extraction lands
                # them — but the user has to be told, in something that outlasts
                # a status message the load's own overwrites.
                message += (
                    f" {held} translation(s) an older project was holding are "
                    "still waiting for it and have not been written into the "
                    "bytes; the project keeps them until the table is back."
                )
                self._note_load_problem(message)
            self.statusBar().showMessage(message, 6000)
            self.files_panel.refresh_labels()
            return changed
        key = (
            cfg,
            tables.start.id,
            id(doc.data),
            sum(len(t.entries) for t in tables.tables.values()),
        )
        if doc.extraction_key == key:
            return False
        try:
            ex = self._reuse_extraction(doc.data, cfg, tables)
            if ex is None:
                ex = extract(doc.data, cfg, tables, self.registry)
        except NotImplementedError as exc:
            # Same rule as a missing table set: the read failed, so there is
            # nothing to replace the translations with, and dropping them would
            # lose work the user cannot get back.
            self.statusBar().showMessage(str(exc), 5000)
            entry.stash_strings(doc)
            return False
        # A re-read without a drop — the bytes changed, the table changed —
        # keeps every string's state by index; a drop stashed it on the entry
        # and it comes back the same way, except where a block edit cut the
        # string at other bits, whose original is then taken from the bytes.
        old = {s.index: s for s in doc.strings}
        for rec in ex.strings:
            prev = old.get(rec.index)
            if prev is not None:
                rec.original, rec.original_digest, rec.status, rec.notes = (
                    prev.original,
                    prev.original_digest,
                    prev.status,
                    prev.notes,
                )
        saved = entry.pending_strings
        legacy: dict[int, str] = {}
        if saved and entry.runs_joined:
            saved = unjoined_states(saved, ex.strings, cfg, tables)
        if saved:

            def spelled(text: str, rec) -> str:
                if not entry.fixed_ends_shown:
                    return text
                return respell_fixed_end(text, rec, cfg, tables)

            for rec in ex.strings:
                st = saved.get(rec.index)
                if st is None:
                    continue
                same = st.extent is None or st.extent == (rec.start_bit, rec.end_bit)
                if same:
                    # State follows the string, not the index: a string the new
                    # reading cuts at other bits is not the one that was
                    # marked, so its original, its mark and its notes are all
                    # about text that is no longer there and it starts afresh.
                    if st.original is not None:
                        rec.original = spelled(st.original, rec)
                        rec.original_digest = st.digest
                    rec.status, rec.notes = st.status, st.notes
                if st.translation is not None:
                    legacy[rec.index] = spelled(st.translation, rec)
            entry.pending_strings = None
            entry.fixed_ends_shown = False
            entry.runs_joined = False
        for rec in ex.strings:
            rec.refresh_status()
        doc.strings = ex.strings
        doc.notices = ex.notices
        doc.inner_tables = ex.inner_tables
        doc.extraction_key = key
        if legacy:
            self._land_legacy_translations(entry, doc, legacy)
        self.files_panel.refresh_labels()
        return True

    @staticmethod
    def _reading_key(cfg: BlockConfig, tables: TableSet) -> tuple:
        """What a reading of some bytes depends on besides the bytes: the
        block's configuration and the tables as they stand."""
        return (
            cfg,
            tables.start.id,
            sum(len(t.entries) for t in tables.tables.values()),
        )

    def _remember_extraction(
        self, data: bytes, cfg: BlockConfig, tables: TableSet, extraction
    ) -> None:
        """Keep the reading a string edit checked its own result against.

        The edit lands by splicing exactly those bytes in, and the block is then
        read again to say what they now mean — the same bytes through the same
        tables, which is the reading already in hand
        (:func:`~mapchar.pipeline.insert.reads_back`).
        """
        self._checked_extraction = (data, self._reading_key(cfg, tables), extraction)

    def _reuse_extraction(self, data: bytes, cfg: BlockConfig, tables: TableSet):
        """The remembered reading when it is of exactly these bytes through
        exactly this reading; ``None`` otherwise.

        Taken once and then forgotten: its records become the block's, and no
        second block may be given the same ones.
        """
        kept = self._checked_extraction
        if kept is None or kept[1] != self._reading_key(cfg, tables) or kept[0] != data:
            return None
        self._checked_extraction = None
        return kept[2]

    def _note_load_problem(self, message: str) -> None:
        """Keep something a block's read has to say where a project load shows
        it (:meth:`~mapchar.ui.main_window.projects.ProjectMixin.open_project`),
        rather than in a status message the load's own replaces."""
        self._load_notices.append(message)

    def _land_legacy_translations(
        self, entry: Entry, doc: Document, texts: dict[int, str]
    ) -> None:
        """Put the translations an older project was still holding into the
        bytes, as the edits they were: the file reads unsaved until written.

        Not an undo step — the project is being read, and the history is
        cleared with it. What will not fit is reported and stays as the
        original, in the notes so it is not lost.
        """
        problems = self._edit_strings_now(entry, doc, texts)
        if not problems:
            return
        for index, text in texts.items():
            rec = doc.string_by_index(index)
            if rec is not None and rec.matches_original(rec.current_text()):
                rec.notes = (
                    rec.notes + "\n" if rec.notes else ""
                ) + f"unplaced: {text}"
        message = (
            f"{entry.name}: {len(problems)} translation(s) from the older project "
            "would not fit and were kept in the notes"
        )
        self._note_load_problem(message + ":\n  " + "\n  ".join(problems))
        self.statusBar().showMessage(message, 8000)

"""Turning the capability table into what is on screen.

:data:`~mapchar.core.capabilities.CAPABILITIES` says which controls a kind of
entry supports; :meth:`CapabilitySyncMixin._sync_capabilities` applies it. One
pass over one declared table, run from the tail of the refresh cycle, in place
of each control carrying its own "...and not on a table entry" clause inside
whichever ``_sync_*`` method happens to own it.

It runs **last** so its answer is the final word: an earlier pass may have
enabled a control on grounds that are true in general and beside the point for
this kind of entry. A control that would be *meaningless* on this kind is
hidden (:data:`_HIDDEN`) — the Block bar on a plain file is furniture for a
different room, not a feature switched off — and one that is merely unavailable
is disabled.

For the controls named here the gate owns enablement in **both** directions,
because nothing else in the window enables them: they applied everywhere before
this table existed, and a veto-only gate would switch one off for the rest of
the session. Controls whose availability something else decides — Open Recent,
Locate Missing Files, the undo actions, the step buttons
(:meth:`~mapchar.ui.main_window.navigation.NavigationMixin._sync_steps`, which
weighs the capability against whether a step has anywhere to go), the per-scan
freeze in
:meth:`~mapchar.ui.main_window.compression.CompressionMixin._set_scan_ui` — are
deliberately absent, and the freeze reaches them anyway by disabling the menu
bar and the central widget above them.

:data:`_GATES` covers the *window's* controls. Four capabilities name surfaces
that are not among them — the Preview window's Wrap button
(:class:`~mapchar.core.capabilities.Capability.WRAP`), the Decompressed view's
structure scan (``COMPRESSION_SCAN``), the Table Editor (``TABLE_EDIT``) and the
Font tab (``FONT_EDIT``) — each a tool window that decides its own enablement
from its own state, so a both-directions gate here would fight it. Those four are
asked instead by the mixin that drives the surface, through :meth:`_can` or
:func:`~mapchar.core.capabilities.supports`, so every declared capability is
enforced somewhere.
"""

from __future__ import annotations

from mapchar.core.capabilities import Capability, EntryKind, supports

# Window attributes each capability gates. A name may hold a single control or a
# tuple of them; every member of a tuple is gated the same way.
_GATES: dict[Capability, tuple[str, ...]] = {
    Capability.NAVIGATION: (
        "offset_box",
        "goto_action",
        "ends_actions",
    ),
    Capability.RAW_VIEW: ("raw_tab_action", "text_tab_action"),
    Capability.HEX_VIEW: ("hex_panel",),
    Capability.CODECS: ("format_bar", "reading_bar"),
    Capability.SEARCH: ("search_actions", "find_bar"),
    Capability.POINTER_DISCOVERY: ("pointers_action",),
    Capability.CONTAINER: ("container_action",),
    Capability.BLOCK_CREATE: ("new_block_action",),
    Capability.BOOKMARK: ("new_bookmark_action",),
    Capability.STRINGS: ("strings_tab_action", "string_actions"),
    Capability.BLOCK_CONFIG: ("block_bar", "block_dump", "dump_action"),
    Capability.FIND_REPLACE: ("find_replace_action",),
    Capability.PREVIEW: ("preview_action",),
    Capability.IMPORT_EXPORT: ("import_action", "export_action"),
    Capability.WRITE: ("write_action",),
}

# Hidden rather than disabled: a greyed Block bar would take a row of the window
# to say nothing, and on a plain file there is no block for it to describe.
_HIDDEN = frozenset({"block_bar"})


class CapabilitySyncMixin:
    """The capability table applied to the window's controls.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _kind(self) -> EntryKind | None:
        """What the entry on screen is, or ``None`` with nothing open."""
        return None if self._entry is None else self._entry.kind

    def _can(self, capability: Capability) -> bool:
        """Whether the entry on screen supports ``capability``.

        The same table read from the other end, for the two controls that gate
        *in place*: the Strings tab, which is enabled on the tab widget rather
        than on a named widget of its own, and anything a mixin has to weigh
        against conditions of its own.
        """
        return supports(self._kind(), capability)

    def _sync_capabilities(self) -> None:
        """Switch off whatever the entry on screen has no capability for.

        Runs last in :meth:`~mapchar.ui.main_window.refresh.RefreshMixin._refresh_view`,
        including its nothing-open early return — a render needs a document, so
        a pass that only ran from one would leave the bars showing whatever the
        last entry needed.
        """
        for capability, names in _GATES.items():
            allowed = self._can(capability)
            for name in names:
                # Not ``getattr(..., None)`` and carry on: a gate naming a control
                # the window does not have is a control that is never gated at
                # all, and swallowing it here is what makes that unfindable. The
                # first refresh runs before any entry can be current, so this
                # fires on construction rather than at some later gesture.
                found = getattr(self, name)
                for control in found if isinstance(found, tuple) else (found,):
                    if name in _HIDDEN:
                        control.setVisible(allowed)
                    control.setEnabled(allowed)
        # The view tabs have no widget of their own to disable: a tab's enabled
        # state lives on the tab bar, keyed by index.
        for widget, capability in (
            (self.raw, Capability.RAW_VIEW),
            (self.text, Capability.RAW_VIEW),
            (self.strings, Capability.STRINGS),
        ):
            self.tabs.setTabEnabled(self.tabs.indexOf(widget), self._can(capability))

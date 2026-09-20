"""The application main window, split by concern.

:class:`~mapchar.ui.main_window.window.MainWindow` is one class assembled from
mixins, one per surface it drives — the active entry and the refresh cycle, the
Text tab's viewport, capability gating, the Format and Reading bars, navigation
and the visit trail, the entries list with its context menu and its clipboard,
the Files dock's own menu, containers, writing back to disk, the decompression
preview and the structure scan, plugins, table files and the Table Editor, the
raw view, blocks and bookmarks, reading a block's strings and reading every
block of the project, the Strings grid's rows and its context menu, the edits
made in it, wrapping, find and replace, the Project Strings window, the
glossary, search (bytes, relative, pointers), the two exchange families,
projects and their missing files, autosave, relocating a moved file, the
Preview window, the Hex dock, the menu bar, the widget tree itself and the Help
menu's dialogs. They are mixins rather than collaborator objects because they
all manipulate the *same* live widgets and the single ``_doc`` on screen;
splitting that state across objects would buy indirection rather than isolation.
What the split does buy is a named home for each concern.

Mixins reach each other only through ``self``. Which controls apply at all is
declared once in :data:`~mapchar.core.capabilities.CAPABILITIES` and applied by
``capability_sync`` at the tail of the refresh, rather than each surface
carrying its own "...and not on a block" clause.

``window.py`` itself is what is left when every surface has one: the documented
state the mixins share, the shared undo stack and the guards every command
applies through, the window title and the project's dirty marker, the file
dialogs and the error modal. It is the shell the mixins hang off, not one more
surface. The widget tree it assembles — the Files dock, the Hex dock, the
editing column and the tool windows — is ``layout.py``.

Only the window class is public; import it from here.
"""

from mapchar.ui.main_window.window import MainWindow

__all__ = ["MainWindow"]

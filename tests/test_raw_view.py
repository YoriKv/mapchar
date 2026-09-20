"""The raw hex and text view: a byte in one column is the byte in the other,
what each column draws in its own cells, and the selection model over both.

A code may be narrower than a byte and may straddle two, so a selection is kept
in *bits* while the text column has it and in whole bytes while the hex column
does, and the anchor a Shift+click reaches from is whatever last set one —
a click, a search hit or Go to String.
"""

from __future__ import annotations


def _raw_widget(qtbot, tokens, data):
    from mapchar.ui.raw_cells import RowModel
    from mapchar.ui.raw_widget import RawWidget

    widget = RawWidget()
    qtbot.addWidget(widget)
    widget.set_model(RowModel(0, data, tokens, set(), len(data)))
    widget.resize(1400, 200)
    return widget


def _text_entry(key: str, text: str, kind=None):
    from mapchar.core.table import TableEntry, TokenKind

    return TableEntry(key, kind or TokenKind.TEXT, text)


def test_a_byte_is_the_same_byte_under_either_column(qtbot):
    """Every hex cell, group gaps included, and every text cell names its own
    byte, whatever the font's fractional width."""
    from mapchar.ui import BYTES_PER_ROW
    from mapchar.ui.widgets import MONO_FAMILIES

    widget = _raw_widget(qtbot, [], bytes(BYTES_PER_ROW * 2))
    # The fallback list is what keeps a Japanese decode from being boxes; the
    # face that answers depends on the machine, so only the list is asserted.
    assert "Noto Sans CJK JP" in MONO_FAMILIES
    assert widget._font.families() == list(MONO_FAMILIES)
    for rel in range(BYTES_PER_ROW * 2):
        for cell in (widget._geom.hex_cell(rel), widget._geom.text_cell(rel)):
            assert widget._byte_at(cell.center()) == rel
            assert widget._byte_at(cell.topLeft()) == rel


def test_every_hex_pair_is_drawn_in_its_own_cell(qtbot):
    """The font's true advance is fractional, so a row drawn as one string
    drifts off the cells its tints fill; each pair is placed in its cell."""
    from PySide6.QtGui import QPainter

    from mapchar.ui import BYTES_PER_ROW

    widget = _raw_widget(qtbot, [], bytes(range(BYTES_PER_ROW)))
    placed = []
    original = QPainter.drawStaticText

    def spy(painter, point, laid):
        placed.append((point, laid))
        return original(painter, point, laid)

    QPainter.drawStaticText = spy
    try:
        widget.viewport().grab()
    finally:
        QPainter.drawStaticText = original
    pairs = {laid.text(): (point, laid.size()) for point, laid in placed}
    for rel in range(BYTES_PER_ROW):
        cell = widget._geom.hex_cell(rel)
        point, size = pairs[f"{rel:02X}"]
        assert point.x() == cell.left() + (cell.width() - size.width()) / 2
        assert cell.top() <= point.y()
        assert point.y() + size.height() <= cell.bottom() + 1


def test_bit_packed_tokens_each_get_a_place_of_their_own(qtbot):
    """Four 6-bit codes fill three bytes: each is placed by its bits, three
    quarters of a byte cell, and none shares another's place."""
    from PySide6.QtCore import QRectF

    from mapchar.core.tokens import Token

    tokens = [
        Token(f"{i:06b}", 6 * i, 6 * i + 6, _text_entry(f"{i:06b}", "かきくけ"[i]))
        for i in range(4)
    ]
    widget = _raw_widget(qtbot, tokens, bytes(3))
    places = [widget._text_segments(t, 3) for t in tokens]
    assert all(len(p) == 1 for p in places)
    rects = [p[0] for p in places]
    for rect in rects:
        assert rect.width() == widget._geom.text_width * 6 / 8
    for left, right in zip(rects, rects[1:], strict=False):
        assert left.right() == right.left()
    assert rects[0].left() == QRectF(widget._geom.text_cell(0)).left()
    assert rects[-1].right() == QRectF(widget._geom.text_cell(2)).right()


def test_a_token_over_a_row_end_is_placed_on_both_rows(qtbot):
    from mapchar.core.tokens import Token
    from mapchar.ui import BYTES_PER_ROW

    last = BYTES_PER_ROW - 1
    token = Token("0" * 16, last * 8, (last + 2) * 8, _text_entry("0" * 16, "漢"))
    widget = _raw_widget(qtbot, [token], bytes(BYTES_PER_ROW * 2))
    first, second = widget._text_segments(token, BYTES_PER_ROW * 2)
    assert first == widget._geom.text_cell(last)
    assert second == widget._geom.text_cell(BYTES_PER_ROW)


def _six_bit_codes(qtbot, count=4):
    """``count`` 6-bit codes over a widget, and the widget."""
    from mapchar.core.tokens import Token

    tokens = [
        Token(f"{i:06b}", 6 * i, 6 * i + 6, _text_entry(f"{i:06b}", "かきくけ"[i % 4]))
        for i in range(count)
    ]
    return tokens, _raw_widget(qtbot, tokens, bytes(-(-6 * count // 8)))


def _click(widget, point):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.mouseClick(widget.viewport(), Qt.MouseButton.LeftButton, pos=point.toPoint())


def _shift_click(widget, point):
    """A press with Shift held, sent to the widget itself.

    Not ``QTest.mouseClick``: the modifiers it is given stay in the
    application's keyboard state, and the next test to read them gets a Shift
    nobody is holding.
    """
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    local = QPointF(point)
    widget.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            local,
            QPointF(widget.viewport().mapToGlobal(point.toPoint())),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ShiftModifier,
        )
    )


def test_a_click_on_a_character_selects_its_bits_in_both_columns(qtbot):
    """The second 6-bit code straddles bytes 0 and 1: clicked, it is selected by
    its bits, the window is told the two bytes it touches, and the hex column
    covers the last two bits of the first pair and exactly the first digit of
    the second."""
    from PySide6.QtCore import QRectF

    tokens, widget = _six_bit_codes(qtbot)
    told = []
    widget.selection_changed.connect(lambda s, e: told.append((s, e)))
    _click(widget, widget._text_segments(tokens[1], 3)[0].center())
    assert widget.selection_bits() == (6, 12)
    assert told == [(0, 2)]
    (text,) = widget._geom.text_span(6, 12)
    assert text == widget._text_segments(tokens[1], 3)[0]
    (hex_span,) = widget._geom.hex_span(6, 12)
    first, second = QRectF(widget._geom.hex_cell(0)), QRectF(widget._geom.hex_cell(1))
    assert first.center().x() < hex_span.left() < first.right()
    assert hex_span.right() == second.center().x()


def test_a_click_on_a_hex_pair_selects_the_whole_byte(qtbot):
    from PySide6.QtCore import QRectF

    tokens, widget = _six_bit_codes(qtbot)
    _click(widget, widget._text_segments(tokens[1], 3)[0].center())
    _click(widget, QRectF(widget._geom.hex_cell(1)).center())
    assert widget.selection() == (1, 2)
    assert widget.selection_bits() is None


def test_dragging_over_characters_selects_every_code_it_crosses(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    tokens, widget = _six_bit_codes(qtbot)
    start = widget._text_segments(tokens[1], 3)[0].center().toPoint()
    end = widget._text_segments(tokens[2], 3)[0].center().toPoint()
    viewport = widget.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
    # QTest's move carries no held button, so the drag is sent as Qt sends it.
    widget.mouseMoveEvent(_move_event(viewport, end))
    assert widget.selection_bits() == (6, 18)
    assert widget.selection() == (0, 3)


def test_shift_clicking_extends_the_selection_the_way_a_drag_does(qtbot):
    """The anchor the last click left, out to what is Shift+clicked, in each
    column's own units — codes by their bits in the text, whole bytes in the
    hex, and backwards as readily as forwards."""
    from PySide6.QtCore import QRectF

    tokens, widget = _six_bit_codes(qtbot)
    _click(widget, widget._text_segments(tokens[1], 3)[0].center())
    _shift_click(widget, widget._text_segments(tokens[3], 3)[0].center())
    assert widget.selection_bits() == (6, 24)
    assert widget.selection() == (0, 3)
    _click(widget, QRectF(widget._geom.hex_cell(2)).center())
    _shift_click(widget, QRectF(widget._geom.hex_cell(0)).center())
    assert widget.selection() == (0, 3)
    assert widget.selection_bits() is None


def test_a_byte_aligned_bit_span_covers_its_cells_exactly(qtbot):
    from PySide6.QtCore import QRectF

    _, widget = _six_bit_codes(qtbot)
    assert widget._geom.hex_span(8, 24) == [QRectF(widget._geom.hex_cell(1, 2))]
    assert widget._geom.text_span(8, 24) == [QRectF(widget._geom.text_cell(1, 2))]


def test_a_selection_set_from_outside_is_whole_bytes(qtbot):
    tokens, widget = _six_bit_codes(qtbot)
    _click(widget, widget._text_segments(tokens[1], 3)[0].center())
    widget.select_bytes(0, 2)
    assert widget.selection_bits() is None


def _hex_click(widget, rel, shift=False):
    from PySide6.QtCore import QRectF

    point = QRectF(widget._geom.hex_cell(rel)).center()
    (_shift_click if shift else _click)(widget, point)


def test_a_selection_set_from_outside_is_what_a_shift_click_reaches_from(qtbot):
    """A search hit, Go to String or a selection made in the Text tab moves the
    anchor to where it starts, so the next Shift+click takes that selection out
    rather than one the last click left somewhere else."""
    widget = _raw_widget(qtbot, [], bytes(32))
    _hex_click(widget, 1)
    widget.select_bytes(10, 12)
    _hex_click(widget, 20, shift=True)
    assert widget.selection() == (10, 21)


def test_a_selection_cleared_from_outside_leaves_nothing_to_reach_from(qtbot):
    widget = _raw_widget(qtbot, [], bytes(32))
    _hex_click(widget, 1)
    widget.select_bytes(0, 0)
    _hex_click(widget, 5, shift=True)
    assert widget.selection() == (5, 6)


def test_the_anchor_is_dropped_when_the_bytes_under_it_change(qtbot):
    """What the view shows can change under the anchor — another entry, other
    bounds, another payload — and then there is nowhere to reach from."""
    widget = _raw_widget(qtbot, [], bytes(32))
    _hex_click(widget, 1)
    widget.clear_anchor()
    _hex_click(widget, 5, shift=True)
    assert widget.selection() == (5, 6)


def test_a_shift_click_reaches_no_further_than_the_view(qtbot):
    from mapchar.ui.raw_cells import RowModel

    widget = _raw_widget(qtbot, [], bytes(32))
    widget.set_model(RowModel(0, bytes(32), [], set(), 32, bounds=(0, 8)))
    _hex_click(widget, 1)
    _hex_click(widget, 20, shift=True)
    assert widget.selection() == (1, 8)


def _move_event(viewport, point):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    local = QPointF(point)
    return QMouseEvent(
        QEvent.Type.MouseMove,
        local,
        QPointF(viewport.mapToGlobal(point)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def test_the_text_column_shows_names_as_labels_and_marks_inside_text():
    from mapchar.core.table import TokenKind
    from mapchar.core.tokens import Token
    from mapchar.ui.token_text import display_text

    def shown(text, kind=None):
        return display_text(Token("0" * 8, 0, 8, _text_entry("0" * 8, text, kind)))

    assert shown("[end]\\n", TokenKind.END) == ("end", True)
    assert shown("[tile60]") == ("tile60", True)
    assert shown("s[line]\\n") == ("s↵", False)
    assert shown("[F6]の") == ("▪の", False)
    assert shown("A") == ("A", False)
    assert display_text(Token("11111111", 0, 16)) == ("·", False)


def test_text_wider_than_its_cells_never_leaves_them(qtbot):
    """A dictionary word on one byte is squeezed and cut to its own cell, and
    says it was cut; a letter that fits is drawn whole."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    widget = _raw_widget(qtbot, [], bytes(1))
    cell = QRectF(widget._geom.text_cell(0))
    image = QImage(400, 100, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    painter.setFont(widget._font)
    try:
        assert not widget._text.fit(painter, cell, "A")
        assert widget._text.fit(painter, cell, "ちからのたね")
        assert painter.clipBoundingRect() == QRectF()  # the clip was restored
    finally:
        painter.end()


def test_one_character_too_wide_is_condensed_rather_than_sliced(qtbot):
    """A single glyph wider than its cell even at MIN_SQUEEZE has nothing left
    to drop, so it is condensed the rest of the way and says it was cut —
    rather than drawn over the cell's edge and sliced there by the clip."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    from mapchar.ui.cell_text import MIN_SQUEEZE

    widget = _raw_widget(qtbot, [], bytes(1))
    image = QImage(400, 100, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.setFont(widget._font)
    try:
        # A cell far too narrow for one character, wherever the faces come from.
        wide = painter.fontMetrics().horizontalAdvance("W")
        cell = QRectF(40, 0, wide * MIN_SQUEEZE / 2, widget.row_height)
        assert widget._text.fit(painter, cell, "W")
    finally:
        painter.end()
    painted = [
        x
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).alpha() > 8
    ]
    # Ink inside the cell, and none of it touching either edge: a glyph that
    # was condensed to fit, not one the clip cut off at the boundary.
    assert painted, "the character was drawn"
    assert cell.left() < min(painted) and max(painted) < cell.right()

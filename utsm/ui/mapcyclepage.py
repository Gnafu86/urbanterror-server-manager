"""Editor for the map rotation and the starting map.

The rotation is a dual list: every installed map on the left, the cycle on the
right, ordered and reorderable. Installed maps come from scanning the ``.pk3``
archives, so the left list only ever offers maps the server can actually load.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..model import maps


class MapCyclePage(QWidget):
    """Pick the starting map and build the rotation."""

    #: Emitted when the rotation changes, carrying the new list.
    cycle_changed = Signal(list)
    #: Emitted when the starting map changes.
    start_map_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._available: list[maps.GameMap] = []
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)

        start_row = QHBoxLayout()
        start_row.setSpacing(8)
        start_row.addWidget(QLabel("Starting map:"))
        self._start = QComboBox()
        self._start.setEditable(True)
        self._start.setMinimumWidth(240)
        self._start.currentTextChanged.connect(self._on_start_changed)
        start_row.addWidget(self._start)
        start_row.addStretch(1)
        layout.addLayout(start_row)

        lists = QHBoxLayout()
        lists.setSpacing(10)

        left_box = QGroupBox("Installed maps")
        left = QVBoxLayout(left_box)
        left.setContentsMargins(10, 10, 10, 10)
        left.setSpacing(6)
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter…")
        self._filter.textChanged.connect(self._apply_filter)
        left.addWidget(self._filter)
        self._available_list = QListWidget()
        self._available_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._available_list.itemDoubleClicked.connect(lambda _: self._add_selected())
        left.addWidget(self._available_list)
        lists.addWidget(left_box, 1)

        middle = QVBoxLayout()
        middle.addStretch(1)
        for text, handler, tip in (
            ("→", self._add_selected, "Add the selected maps to the rotation"),
            ("←", self._remove_selected, "Remove the selected maps from the rotation"),
        ):
            b = QPushButton(text)
            b.setFixedWidth(40)
            b.setToolTip(tip)
            b.clicked.connect(handler)
            middle.addWidget(b)
        middle.addSpacing(16)
        for text, handler, tip in (
            ("↑", lambda: self._move(-1), "Move up in the rotation"),
            ("↓", lambda: self._move(1), "Move down in the rotation"),
        ):
            b = QPushButton(text)
            b.setFixedWidth(40)
            b.setToolTip(tip)
            b.clicked.connect(handler)
            middle.addWidget(b)
        middle.addStretch(1)
        lists.addLayout(middle)

        right_box = QGroupBox("Map cycle (in order)")
        right = QVBoxLayout(right_box)
        right.setContentsMargins(10, 10, 10, 10)
        right.setSpacing(6)
        self._cycle_list = QListWidget()
        self._cycle_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._cycle_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._cycle_list.model().rowsMoved.connect(lambda *_: self._emit_cycle())
        self._cycle_list.itemDoubleClicked.connect(lambda _: self._remove_selected())
        right.addWidget(self._cycle_list)

        cycle_buttons = QHBoxLayout()
        cycle_buttons.setSpacing(8)
        for text, handler in (
            ("Add all", self._add_all),
            ("Clear", self._clear),
        ):
            b = QPushButton(text)
            b.clicked.connect(handler)
            cycle_buttons.addWidget(b)
        cycle_buttons.addStretch(1)
        right.addLayout(cycle_buttons)
        lists.addWidget(right_box, 1)

        layout.addLayout(lists, 1)

        self._status = QLabel()
        self._status.setProperty("role", "hint")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

    # -- Population ---------------------------------------------------------

    def set_available_maps(self, found: list[maps.GameMap]) -> None:
        self._available = list(found)
        self._apply_filter()

        current = self._start.currentText()
        self._loading = True
        self._start.clear()
        self._start.addItems([m.name for m in self._available])
        if current:
            self._start.setCurrentText(current)
        self._loading = False
        self._update_status()

    def load(self, cycle: list[str], start_map: str) -> None:
        self._loading = True
        try:
            self._cycle_list.clear()
            for name in cycle:
                self._cycle_list.addItem(QListWidgetItem(name))
            self._start.setCurrentText(start_map)
        finally:
            self._loading = False
        self._update_status()

    @property
    def cycle(self) -> list[str]:
        return [self._cycle_list.item(i).text() for i in range(self._cycle_list.count())]

    # -- Editing ------------------------------------------------------------

    def _apply_filter(self) -> None:
        needle = self._filter.text().strip().lower()
        self._available_list.clear()
        for m in self._available:
            if needle and needle not in m.name.lower():
                continue
            item = QListWidgetItem(m.name)
            item.setToolTip(f"{m.display_name}\nfrom {m.source.name}")
            self._available_list.addItem(item)

    def _add_selected(self) -> None:
        existing = set(self.cycle)
        for item in self._available_list.selectedItems():
            if item.text() not in existing:
                self._cycle_list.addItem(QListWidgetItem(item.text()))
                existing.add(item.text())
        self._emit_cycle()

    def _add_all(self) -> None:
        existing = set(self.cycle)
        for m in self._available:
            if m.name not in existing:
                self._cycle_list.addItem(QListWidgetItem(m.name))
        self._emit_cycle()

    def _remove_selected(self) -> None:
        for item in self._cycle_list.selectedItems():
            self._cycle_list.takeItem(self._cycle_list.row(item))
        self._emit_cycle()

    def _clear(self) -> None:
        self._cycle_list.clear()
        self._emit_cycle()

    def _move(self, delta: int) -> None:
        rows = sorted(self._cycle_list.row(i) for i in self._cycle_list.selectedItems())
        if not rows:
            return
        # Walk in the direction of travel so items cannot overwrite each other.
        if delta > 0:
            rows.reverse()
        for row in rows:
            target = row + delta
            if not 0 <= target < self._cycle_list.count():
                continue
            item = self._cycle_list.takeItem(row)
            self._cycle_list.insertItem(target, item)
            item.setSelected(True)
        self._emit_cycle()

    # -- Signals ------------------------------------------------------------

    def _emit_cycle(self) -> None:
        if self._loading:
            return
        self._update_status()
        self.cycle_changed.emit(self.cycle)

    def _on_start_changed(self, text: str) -> None:
        if self._loading:
            return
        self._update_status()
        self.start_map_changed.emit(text.strip())

    def _update_status(self) -> None:
        cycle = self.cycle
        start = self._start.currentText().strip()
        parts = [f"{len(cycle)} map{'s' if len(cycle) != 1 else ''} in rotation"]
        if not cycle:
            parts.append("with an empty cycle the server replays the starting map")
        elif start and start not in cycle:
            parts.append(
                f"'{start}' is not in the rotation, so the cycle moves elsewhere "
                "after the first map"
            )
        installed = {m.name for m in self._available}
        missing = [m for m in cycle if installed and m not in installed]
        if missing:
            parts.append(f"not installed: {', '.join(missing[:4])}")
        self._status.setText("  |  ".join(parts))

    def set_read_only(self, read_only: bool) -> None:
        for w in (self._available_list, self._cycle_list, self._start, self._filter):
            w.setEnabled(not read_only)

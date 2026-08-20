"""Editor for ``g_gear`` -- which weapons and items players may spawn with.

The cvar is a set of letters naming *disallowed* equipment, which is awkward to
reason about. The page inverts it: a ticked box means players may use the item,
matching how an operator thinks about a loadout.

The raw cvar string stays visible and editable. The letter table in
:mod:`utsm.model.gear` is derived from the game's own ``ui/menudef.h``, but the
handful of weapons added in 4.3 are the one part not corroborated by a shipped
file, so the escape hatch matters: a wrong letter here can never block anyone.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..model import gear
from .formbuilder import escape_mnemonic

#: Slots laid out in loadout order.
_SLOT_ORDER = (gear.PRIMARY, gear.SECONDARY, gear.SIDEARM, gear.GRENADE, gear.ITEM)


class GearPage(QWidget):
    """Checkbox grid over every weapon and item, plus the raw cvar."""

    changed = Signal(str, object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._boxes: dict[str, QCheckBox] = {}
        self._preserved = ""      # letters from the config this build cannot map
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 14, 16, 18)
        layout.setSpacing(14)
        scroll.setWidget(body)

        intro = QLabel(
            "Ticked equipment can be used. Unticking an item adds its letter to "
            "<code>g_gear</code>, which is the list of gear the server disallows."
        )
        intro.setWordWrap(True)
        intro.setProperty("role", "blurb")
        layout.addWidget(intro)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        # "Disallow all" and "Knives only" would be the same action: the knife
        # has no letter and is always issued, so blocking everything else leaves
        # exactly a knife round. Only the meaningful name is offered.
        for text, handler, tip in (
            ("Allow all", lambda: self._set_all(True),
             "Clear g_gear so every weapon and item is available"),
            ("Knives only", lambda: self._set_all(False),
             "Disallow every weapon and item; the knife is always issued"),
            ("No explosives", self._no_explosives,
             "Disallow the HE grenade and the HK69 grenade launcher"),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(handler)
            buttons.addWidget(b)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        for slot in _SLOT_ORDER:
            items = gear.items_in_slot(slot)
            if not items:
                continue
            box = QGroupBox(gear.SLOT_TITLES[slot])
            grid = QGridLayout(box)
            grid.setContentsMargins(14, 12, 14, 12)
            grid.setHorizontalSpacing(18)
            grid.setVerticalSpacing(6)
            for i, item in enumerate(items):
                cb = QCheckBox(escape_mnemonic(item.label))
                cb.setToolTip(f"g_gear letter: {item.letter}")
                cb.toggled.connect(self._on_toggled)
                self._boxes[item.key] = cb
                grid.addWidget(cb, i // 3, i % 3)
            layout.addWidget(box)

        raw_box = QGroupBox("Raw value")
        raw_layout = QVBoxLayout(raw_box)
        raw_layout.setContentsMargins(14, 12, 14, 12)
        raw_layout.setSpacing(6)

        self._raw = QLineEdit()
        self._raw.setPlaceholderText("empty — everything allowed")
        self._raw.editingFinished.connect(self._on_raw_edited)
        raw_layout.addWidget(self._raw)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setProperty("role", "hint")
        raw_layout.addWidget(self._summary)

        layout.addWidget(raw_box)
        layout.addStretch(1)

    # -- Value transfer -----------------------------------------------------

    def load(self, value: str) -> None:
        """Populate from a ``g_gear`` string."""
        self._loading = True
        try:
            self._preserved = gear.unknown_letters(value or "")
            disallowed = gear.decode(value or "")
            for key, box in self._boxes.items():
                box.setChecked(key not in disallowed)
            self._raw.setText(value or "")
            self._update_summary(value or "")
        finally:
            self._loading = False

    def _current_value(self) -> str:
        disallowed = {key for key, box in self._boxes.items() if not box.isChecked()}
        return gear.encode(disallowed, preserve=self._preserved)

    # -- Events -------------------------------------------------------------

    def _on_toggled(self, _checked: bool) -> None:
        if self._loading:
            return
        value = self._current_value()
        self._raw.setText(value)
        self._update_summary(value)
        self.changed.emit("g_gear", value)

    def _on_raw_edited(self) -> None:
        if self._loading:
            return
        value = self._raw.text().strip()
        self.load(value)
        self.changed.emit("g_gear", value)

    def _set_all(self, allowed: bool) -> None:
        self._loading = True
        for box in self._boxes.values():
            box.setChecked(allowed)
        self._loading = False
        self._on_toggled(allowed)

    def _no_explosives(self) -> None:
        self._loading = True
        for key, box in self._boxes.items():
            box.setChecked(key not in ("he", "hk69"))
        self._loading = False
        self._on_toggled(False)

    def _update_summary(self, value: str) -> None:
        text = gear.describe(value)
        unknown = gear.unknown_letters(value)
        if unknown:
            text += (
                f"  |  Unrecognised letters kept as-is: {unknown}. "
                "They are preserved so nothing is silently re-enabled."
            )
        self._summary.setText(text)

    def set_read_only(self, read_only: bool) -> None:
        for box in self._boxes.values():
            box.setEnabled(not read_only)
        self._raw.setReadOnly(read_only)

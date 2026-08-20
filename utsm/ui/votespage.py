"""Editor for ``g_allowvote`` -- which votes players may call.

The cvar is a 30-bit mask, which is not something anyone should have to compute
by hand. This page presents it as a checklist and shows the resulting integer,
which stays editable so an existing value can be pasted straight in.

Votes that hand a player broad control over the server -- ``exec`` above all,
which runs an arbitrary config -- are called out rather than left looking like
any other checkbox.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
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

from ..model import cvars, votes
from .formbuilder import CVarField

#: Vote-related options that are ordinary cvars rather than mask bits. They live
#: here rather than on a settings tab so everything about voting is in one place.
_TIMING_CVARS = ("g_failedVoteTime", "g_newMapVoteTime")


class VotesPage(QWidget):
    """Checklist over every callable vote, plus the raw bitmask."""

    changed = Signal(str, object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._boxes: dict[str, QCheckBox] = {}
        self._preserved = 0       # bits above the range this build knows
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
            "Ticked votes can be called by players. Untick a vote to block it. "
            "Voting is disabled entirely when nothing is ticked."
        )
        intro.setWordWrap(True)
        intro.setProperty("role", "blurb")
        layout.addWidget(intro)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        for text, handler in (
            ("Allow all", lambda: self._set_all(True)),
            ("Block all", lambda: self._set_all(False)),
            ("Restore default", self._restore_default),
        ):
            b = QPushButton(text)
            b.clicked.connect(handler)
            buttons.addWidget(b)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        ordinary = [v for v in votes.VOTES if v.key not in votes.SENSITIVE]
        sensitive = [v for v in votes.VOTES if v.key in votes.SENSITIVE]

        layout.addWidget(self._make_group("Votes", ordinary))

        risky = self._make_group("Votes that grant broad control", sensitive)
        risky.setToolTip(
            "These let a vote change what the server runs. 'Execute config' in "
            "particular runs an arbitrary config file."
        )
        layout.addWidget(risky)

        layout.addWidget(self._make_timing_group())

        raw_box = QGroupBox("Raw value")
        raw_layout = QVBoxLayout(raw_box)
        raw_layout.setContentsMargins(14, 12, 14, 12)
        raw_layout.setSpacing(6)

        self._raw = QLineEdit()
        self._raw.setPlaceholderText(str(votes.DEFAULT_MASK))
        self._raw.editingFinished.connect(self._on_raw_edited)
        raw_layout.addWidget(self._raw)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setProperty("role", "hint")
        raw_layout.addWidget(self._summary)

        layout.addWidget(raw_box)
        layout.addStretch(1)

    def _make_timing_group(self) -> QGroupBox:
        box = QGroupBox("Vote timing")
        grid = QGridLayout(box)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)

        self._timing: dict[str, CVarField] = {}
        row = 0
        for name in _TIMING_CVARS:
            cvar = cvars.get(name)
            if cvar is None:
                continue
            field = CVarField(cvar, self)
            field.changed.connect(self.changed)
            self._timing[cvar.name] = field

            label = QLabel(cvar.label)
            label.setToolTip(f"{cvar.name}\n\n{cvar.help}")
            grid.addWidget(label, row, 0)
            grid.addWidget(field.widget, row, 1)

            hint = QLabel(cvar.help)
            hint.setWordWrap(True)
            hint.setProperty("role", "hint")
            grid.addWidget(hint, row, 2)
            grid.setColumnStretch(2, 1)
            row += 1
        return box

    def _make_group(self, title: str, entries: list[votes.Vote]) -> QGroupBox:
        box = QGroupBox(title)
        grid = QGridLayout(box)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        for i, vote in enumerate(entries):
            cb = QCheckBox(vote.label)
            cb.setToolTip(f"{vote.key}  (bit {vote.bit})\n\n{vote.help}")
            cb.toggled.connect(self._on_toggled)
            self._boxes[vote.key] = cb
            grid.addWidget(cb, i // 3, i % 3)
        return box

    # -- Value transfer -----------------------------------------------------

    def load(self, mask: int, values: dict | None = None) -> None:
        self._loading = True
        try:
            mask = int(mask or 0)
            self._preserved = votes.unknown_bits(mask)
            allowed = votes.decode(mask)
            for key, box in self._boxes.items():
                box.setChecked(key in allowed)
            self._raw.setText(str(mask))
            self._update_summary(mask)
            for name, field in self._timing.items():
                if values and name in values:
                    field.value = values[name]
        finally:
            self._loading = False

    def _current_value(self) -> int:
        allowed = {key for key, box in self._boxes.items() if box.isChecked()}
        return votes.encode(allowed, preserve_high_bits=self._preserved)

    # -- Events -------------------------------------------------------------

    def _on_toggled(self, _checked: bool) -> None:
        if self._loading:
            return
        mask = self._current_value()
        self._raw.setText(str(mask))
        self._update_summary(mask)
        self.changed.emit("g_allowvote", mask)

    def _on_raw_edited(self) -> None:
        if self._loading:
            return
        text = self._raw.text().strip()
        try:
            mask = int(text, 0)
        except ValueError:
            # Revert first: load() rewrites the summary, so the explanation has
            # to be set afterwards or it is immediately overwritten.
            self.load(self._current_value())
            self._summary.setText(f"'{text}' is not a number. Reverted.")
            return
        self.load(mask)
        self.changed.emit("g_allowvote", mask)

    def _set_all(self, allowed: bool) -> None:
        self._loading = True
        for box in self._boxes.values():
            box.setChecked(allowed)
        self._loading = False
        self._on_toggled(allowed)

    def _restore_default(self) -> None:
        self.load(votes.DEFAULT_MASK)
        self.changed.emit("g_allowvote", votes.DEFAULT_MASK)

    def _update_summary(self, mask: int) -> None:
        text = votes.describe(mask)
        allowed = votes.decode(mask)
        risky = sorted(votes.SENSITIVE & allowed)
        if risky:
            names = ", ".join(votes.VOTES_BY_KEY[k].label for k in risky)
            text += f"  |  Grants broad control: {names}."
        if self._preserved:
            text += f"  |  Unknown high bits preserved: {self._preserved:#x}."
        self._summary.setText(text)

    def set_read_only(self, read_only: bool) -> None:
        for box in self._boxes.values():
            box.setEnabled(not read_only)
        for field in self._timing.values():
            field.widget.setEnabled(not read_only)
        self._raw.setReadOnly(read_only)

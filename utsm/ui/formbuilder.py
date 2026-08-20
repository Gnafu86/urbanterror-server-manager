"""Turning the cvar registry into editable forms.

Every server option is one row in :mod:`utsm.model.cvars`, and this module maps
each row to a widget. Adding an option to the GUI therefore means adding a
registry entry, not writing a widget -- which is what makes covering the whole
server configuration tractable.

Two things the plain form deliberately surfaces:

* **Latched options.** The engine accepts these immediately but only applies
  them on map reload. They are badged so a change that has not taken effect
  never looks like one that has.
* **Help text.** Taken from the shipped ``server_example.cfg``, shown inline
  rather than hidden in tooltips, because nobody remembers what ``g_antiwarptol``
  does.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..model import cvars
from ..model.cvars import CVar, Kind


def escape_mnemonic(text: str) -> str:
    """Protect a literal ``&`` from Qt's keyboard-accelerator syntax.

    Qt reads ``&`` in a label as "underline the next character". Real names
    contain it -- ``H&K G36``, ``Network & Slots`` -- and would otherwise render
    as ``HK G36`` with the K underlined. The model keeps the true name; the view
    escapes it here.
    """
    return text.replace("&", "&&")


class CVarField(QObject):
    """One bound editor: a widget that reads and writes a single cvar."""

    changed = Signal(str, object)

    def __init__(self, cvar: CVar, parent: QObject | None = None):
        super().__init__(parent)
        self.cvar = cvar
        self.widget = self._build()
        self._suppress = False

    # -- Widget construction ------------------------------------------------

    def _build(self) -> QWidget:
        cvar = self.cvar
        kind = cvar.kind

        if kind is Kind.BOOL:
            w = QCheckBox()
            w.toggled.connect(self._emit)
            return w

        if kind is Kind.ENUM:
            w = QComboBox()
            for value, label in cvar.choices:
                w.addItem(label, value)
            w.currentIndexChanged.connect(self._emit)
            return w

        if kind is Kind.INT:
            w = QSpinBox()
            w.setRange(
                int(cvar.minimum) if cvar.minimum is not None else -2_147_483_648,
                int(cvar.maximum) if cvar.maximum is not None else 2_147_483_647,
            )
            if cvar.unit:
                w.setSuffix(f" {cvar.unit}")
            # Several options use 0 to mean "off"; say so instead of showing 0.
            if cvar.minimum == 0 and _zero_means_off(cvar):
                w.setSpecialValueText("Off")
            w.valueChanged.connect(self._emit)
            return w

        if kind is Kind.FLOAT:
            w = QDoubleSpinBox()
            w.setRange(
                float(cvar.minimum) if cvar.minimum is not None else -1e9,
                float(cvar.maximum) if cvar.maximum is not None else 1e9,
            )
            w.setDecimals(2)
            w.valueChanged.connect(self._emit)
            return w

        if kind is Kind.PASSWORD:
            w = QLineEdit()
            w.setEchoMode(QLineEdit.EchoMode.Password)
            w.setPlaceholderText("not set")
            w.textChanged.connect(self._emit)
            return w

        w = QLineEdit()
        w.setPlaceholderText(str(cvar.default) if cvar.default else "empty")
        w.textChanged.connect(self._emit)
        return w

    # -- Value access -------------------------------------------------------

    @property
    def value(self) -> Any:
        w = self.widget
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, QComboBox):
            return w.currentData()
        if isinstance(w, (QSpinBox, QDoubleSpinBox)):
            return w.value()
        return w.text()

    @value.setter
    def value(self, new: Any) -> None:
        self._suppress = True
        try:
            w = self.widget
            if isinstance(w, QCheckBox):
                w.setChecked(bool(new))
            elif isinstance(w, QComboBox):
                index = w.findData(new)
                w.setCurrentIndex(index if index >= 0 else 0)
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                w.setValue(type(w.value())(new or 0))
            else:
                w.setText("" if new is None else str(new))
        finally:
            self._suppress = False

    def _emit(self, *_args) -> None:
        if not self._suppress:
            self.changed.emit(self.cvar.name, self.value)


def _zero_means_off(cvar: CVar) -> bool:
    """Whether this option's help says zero disables it."""
    text = cvar.help.lower()
    return any(p in text for p in ("0 = never", "0 = no limit", "0 = unlimited",
                                   "0 disables", "0 = all", "disables the check"))


class CVarFormPage(QWidget):
    """A scrollable page of options for one registry group."""

    changed = Signal(str, object)

    def __init__(
        self,
        group: cvars.Group,
        gametype: int,
        show_advanced: bool = False,
        show_help: bool = True,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.group = group
        self._gametype = gametype
        self._show_advanced = show_advanced
        self._show_help = show_help
        self.fields: dict[str, CVarField] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        self._body = QWidget()
        self._form = QFormLayout(self._body)
        self._form.setContentsMargins(16, 14, 16, 18)
        self._form.setVerticalSpacing(10)
        self._form.setHorizontalSpacing(18)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        scroll.setWidget(self._body)

        self.rebuild()

    # -- Building -----------------------------------------------------------

    def rebuild(self) -> None:
        """Recreate the rows, e.g. after the gametype or filters changed."""
        while self._form.count():
            item = self._form.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.fields.clear()

        if self.group.blurb:
            blurb = QLabel(self.group.blurb)
            blurb.setWordWrap(True)
            blurb.setProperty("role", "blurb")
            self._form.addRow(blurb)

        # COMMAND_LINE_ONLY affects where a value is *written* (argv rather than
        # the config file), not whether it is editable. Port and visibility are
        # among the first things anyone sets, so they belong on the form.
        for cvar in cvars.for_group(self.group.key, self._gametype):
            if cvar.advanced and not self._show_advanced:
                continue
            if cvar.kind in (Kind.GEAR, Kind.VOTES, Kind.MAPCYCLE):
                continue  # these have dedicated pages
            self._add_row(cvar)

    def _add_row(self, cvar: CVar) -> None:
        field = CVarField(cvar, self)
        field.changed.connect(self.changed)
        self.fields[cvar.name] = field

        label = QLabel(escape_mnemonic(cvar.label))
        label.setToolTip(f"{cvar.name}\n\n{cvar.help}" if cvar.help else cvar.name)
        field.widget.setToolTip(label.toolTip())

        editor = QWidget()
        row = QHBoxLayout(editor)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(field.widget, 1)

        if cvar.latched:
            badge = QLabel("needs map reload")
            badge.setProperty("role", "badge")
            badge.setToolTip(
                "The server accepts this immediately but only applies it when the "
                "map reloads."
            )
            row.addWidget(badge, 0)

        container = QWidget()
        stack = QVBoxLayout(container)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(2)
        stack.addWidget(editor)

        if self._show_help and cvar.help:
            hint = QLabel(cvar.help)
            hint.setWordWrap(True)
            hint.setProperty("role", "hint")
            stack.addWidget(hint)

        self._form.addRow(label, container)

    # -- Bulk value transfer ------------------------------------------------

    def load(self, values: dict[str, Any]) -> None:
        """Populate every field from a settings mapping."""
        for name, field in self.fields.items():
            if name in values:
                field.value = values[name]

    def set_gametype(self, gametype: int) -> None:
        if gametype != self._gametype:
            self._gametype = gametype
            self.rebuild()

    def set_show_advanced(self, show: bool) -> None:
        if show != self._show_advanced:
            self._show_advanced = show
            self.rebuild()

    def set_show_help(self, show: bool) -> None:
        if show != self._show_help:
            self._show_help = show
            self.rebuild()

    def set_read_only(self, read_only: bool) -> None:
        for field in self.fields.values():
            field.widget.setEnabled(not read_only)

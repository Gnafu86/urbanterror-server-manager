"""Encoding and decoding of the ``g_gear`` cvar.

``g_gear`` is a set of letters naming the weapons and items players may *not*
spawn with. An empty string allows everything.

Provenance
----------
The item table is not guesswork: it is lifted from ``ui/menudef.h`` inside
``zUrT43_020.pk3``, which is the game's own header defining ``ITEM_KNIFE = 1``
through ``ITEM_MAGNUM = 33``. The slot menus (``ui/ingame_select_gear_*.menu``)
confirm which items are selectable in which loadout slot, and supply the display
names used here.

The letter for an item is its index offset so that ``ITEM_BERETTA`` (2) is ``A``.
The knife has no letter because it cannot be taken away. This reproduces the
long-published mapping for the classic weapons and every item -- ``F`` = HK69,
``G`` = LR300, ``H`` = G36, ``K`` = HE grenade, ``N`` = kevlar, ``R`` = laser,
``T`` = extra ammo.

Items above index 27 are the weapons added in 4.3 (Colt 1911, MAC11, FR-F1, P90,
Benelli, Magnum). Their letters continue into lowercase, which is the reading
consistent with the 4.3 gear strings seen in the wild, but it is the one part of
this table not corroborated by a shipped file. The UI therefore always exposes
the raw ``g_gear`` string next to the checkboxes, so an operator is never
blocked by a wrong letter here, and correcting one is a single edit to ``ITEMS``.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Loadout slots, in the order the in-game menus present them.
PRIMARY = "primary"
SECONDARY = "secondary"
SIDEARM = "sidearm"
GRENADE = "grenade"
ITEM = "item"

SLOT_TITLES: dict[str, str] = {
    PRIMARY: "Primary weapons",
    SECONDARY: "Secondary weapons",
    SIDEARM: "Sidearms",
    GRENADE: "Grenades",
    ITEM: "Items",
}


@dataclass(frozen=True)
class GearItem:
    index: int
    letter: str
    key: str
    label: str
    slot: str


def _letter(index: int) -> str:
    """Item index to its ``g_gear`` letter. ITEM_BERETTA (2) is 'A'."""
    offset = index - 2
    if offset < 0:
        return ""
    if offset < 26:
        return chr(ord("A") + offset)
    return chr(ord("a") + offset - 26)


def _mk(index: int, key: str, label: str, slot: str) -> GearItem:
    return GearItem(index, _letter(index), key, label, slot)


#: Every gear item that can be disallowed, in ITEM_* index order.
#: ITEM_KNIFE (1) is intentionally absent: it is always issued.
#: ITEM_APR (22) and ITEM_GRENADE_FRAG (25) exist in menudef.h but are not
#: selectable in any loadout menu, so they are kept here for a faithful letter
#: sequence but marked as unused slots.
ITEMS: tuple[GearItem, ...] = (
    _mk(2, "beretta", "Beretta 92G", SIDEARM),
    _mk(3, "deagle", "Desert Eagle .50", SIDEARM),
    _mk(4, "spas12", "Franchi SPAS-12", SECONDARY),
    _mk(5, "mp5k", "H&K MP5K", SECONDARY),
    _mk(6, "ump45", "H&K UMP45", SECONDARY),
    _mk(7, "hk69", "H&K 69 40mm", PRIMARY),
    _mk(8, "lr300", "ZM LR300", PRIMARY),
    _mk(9, "g36", "H&K G36", PRIMARY),
    _mk(10, "ak103", "AK103 7.62mm", PRIMARY),
    _mk(11, "psg1", "H&K PSG-1", PRIMARY),
    _mk(12, "he", "HE Grenade", GRENADE),
    _mk(13, "flash", "Flash Grenade", GRENADE),
    _mk(14, "smoke", "Smoke Grenade", GRENADE),
    _mk(15, "vest", "Kevlar Vest", ITEM),
    _mk(16, "nvg", "Tactical Goggles", ITEM),
    _mk(17, "medkit", "Medkit", ITEM),
    _mk(18, "silencer", "Silencer", ITEM),
    _mk(19, "laser", "Laser Sight", ITEM),
    _mk(20, "helmet", "Kevlar Helmet", ITEM),
    _mk(21, "ammo", "Extra Ammo", ITEM),
    _mk(23, "sr8", "Remington SR-8", PRIMARY),
    _mk(24, "negev", "IMI Negev", PRIMARY),
    _mk(26, "m4", "M4A1", PRIMARY),
    _mk(27, "glock", "Glock 18", SIDEARM),
    _mk(28, "colt1911", "Colt 1911", SIDEARM),
    _mk(29, "mac11", "Ingram MAC 11", SECONDARY),
    _mk(30, "frf1", "FR-F1", PRIMARY),
    _mk(31, "p90", "FN P90", SECONDARY),
    _mk(32, "benelli", "Benelli M4", SECONDARY),
    _mk(33, "magnum", ".44 Magnum", SIDEARM),
)

ITEMS_BY_LETTER: dict[str, GearItem] = {i.letter: i for i in ITEMS}
ITEMS_BY_KEY: dict[str, GearItem] = {i.key: i for i in ITEMS}

#: Letters this build understands. Anything outside is preserved verbatim on
#: round-trip rather than silently dropped.
KNOWN_LETTERS = frozenset(ITEMS_BY_LETTER)


def items_in_slot(slot: str) -> list[GearItem]:
    return [i for i in ITEMS if i.slot == slot]


def decode(value: str) -> set[str]:
    """``g_gear`` string to the set of item keys that are **disallowed**."""
    return {ITEMS_BY_LETTER[ch].key for ch in (value or "") if ch in ITEMS_BY_LETTER}


def unknown_letters(value: str) -> str:
    """Letters in ``value`` this table does not recognise, in original order."""
    return "".join(ch for ch in (value or "") if ch not in ITEMS_BY_LETTER)


def encode(disallowed: set[str] | frozenset[str], preserve: str = "") -> str:
    """Set of disallowed item keys back to a ``g_gear`` string.

    ``preserve`` carries through any letters from the original value that this
    table did not recognise, so editing an unfamiliar config never quietly
    re-enables equipment.
    """
    letters = [i.letter for i in ITEMS if i.key in disallowed]
    return "".join(letters) + "".join(dict.fromkeys(preserve))


def describe(value: str) -> str:
    """Short human summary of a gear string, for status lines."""
    blocked = decode(value)
    if not blocked:
        return "All weapons and items allowed"
    names = [i.label for i in ITEMS if i.key in blocked]
    if len(names) <= 3:
        return "Disallowed: " + ", ".join(names)
    return f"Disallowed: {len(names)} weapons and items"

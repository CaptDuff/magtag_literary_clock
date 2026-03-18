"""
menu.py — Menu item definitions and navigation for the Literary Clock.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW TO ADD A NEW MENU ITEM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Open clock.py and find build_menu(). Add a MenuItem to the list:

  ── Static action (goes to a new screen / does something once) ──
  MenuItem(
      label  = "My Action",
      action = lambda: "state_name",   # return state to transition to
  )

  ── Toggle (flips a config value and stays in menu) ──
  MenuItem(
      label  = lambda: f"My Toggle: {'ON' if config.get('my_key') else 'OFF'}",
      action = lambda: config.toggle("my_key") and None,
  )

  ── Conditional item (hidden unless condition returns True) ──
  MenuItem(
      label     = "Only When Ready",
      action    = lambda: do_thing(),
      condition = lambda: some_check(),
  )

  ── Message result (show a brief status screen then return to clock) ──
  MenuItem(
      label  = "Do Something",
      action = lambda: MsgResult("Done!", "It worked."),
  )

Return values from action():
  • None          → stay in menu (re-renders to update toggle labels)
  • str           → switch to that state ("clock", "set_h", etc.)
  • MsgResult     → show a timed message screen then return to clock
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from dataclasses import dataclass, field


@dataclass
class MsgResult:
    """Return this from a MenuItem action to display a brief status message."""
    text:    str
    subtext: str = ""


class MenuItem:
    """
    A single entry in a Menu.

    Parameters
    ----------
    label : str or callable() -> str
        Display text. Use a callable for dynamic labels (e.g. toggle state).
    action : callable() -> None | str | MsgResult
        Called when the item is selected. See module docstring for return values.
    condition : callable() -> bool, optional
        When provided, the item is hidden whenever this returns False.
    """

    def __init__(self, label, action, condition=None):
        self._label    = label
        self.action    = action
        self.condition = condition

    @property
    def label(self) -> str:
        return self._label() if callable(self._label) else self._label

    @property
    def visible(self) -> bool:
        if self.condition is None:
            return True
        return bool(self.condition())


class Menu:
    """
    Manages cursor state for a list of MenuItems.

    Only 'visible' items (those whose condition is True) are navigable.
    The cursor is always in bounds for the current visible set.
    """

    def __init__(self, title: str, items: list):
        self.title   = title
        self._items  = items
        self.cursor  = 0

    @property
    def items(self) -> list:
        """Currently visible items."""
        return [it for it in self._items if it.visible]

    def move(self, direction: int) -> None:
        """Move cursor. direction: -1 = up, +1 = down."""
        visible = self.items
        if not visible:
            return
        self.cursor = (self.cursor + direction) % len(visible)

    def select(self):
        """Execute the selected item's action and return its result."""
        visible = self.items
        if not visible:
            return None
        return visible[self.cursor].action()

    def reset(self) -> None:
        """Reset cursor to the top (call when re-opening the menu)."""
        self.cursor = 0
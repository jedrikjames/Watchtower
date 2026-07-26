"""The key hints across the top.

Textual's own Footer is not used: it docks to the bottom, styles itself from
the theme, and advertises the command palette, none of which we want. This is a
single static line instead, centred, drawn once and only redrawn if the hints
themselves change - which they do not.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from ..render import palette

#: (key, what it does). Deliberately short: the full list lives behind ?.
HINTS: tuple[tuple[str, str], ...] = (
    ("a", "Add"),
    ("s", "Settings"),
    ("?", "Help"),
    ("q", "Quit"),
)

GAP = "   "


class KeyHints(Static):
    def render(self) -> Text:
        colours = palette()
        line = Text(no_wrap=True, justify="center")
        for index, (key, label) in enumerate(HINTS):
            if index:
                line.append(GAP)
            # Reversed key cap, then the label beside it.
            line.append(f" {key} ", style="black on white")
            line.append(f" {label}", style=colours.subtitle)
        return line

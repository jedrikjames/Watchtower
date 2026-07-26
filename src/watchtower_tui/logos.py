"""Provider marks, drawn in braille.

A terminal cannot show an SVG, so the Bootstrap Icons `openai` and `claude`
glyphs are traced onto a dot grid instead. Braille (U+2800-U+28FF) packs 2x4
dots into one cell, so the 5x3 cells a card gives us become a 10x12 bitmap -
eight times the resolution of box-drawing characters in exactly the same space,
which is the difference between a recognisable mark and a vague squiggle.

Every modern terminal font covers this block.

Dot numbering within a cell, and the bit each one sets:

    1 (0x01)  4 (0x08)
    2 (0x02)  5 (0x10)
    3 (0x04)  6 (0x20)
    7 (0x40)  8 (0x80)
"""

from __future__ import annotations

BRAILLE_BASE = 0x2800

# (column within cell, row within cell) -> bit
_DOT_BITS = {
    (0, 0): 0x01,
    (0, 1): 0x02,
    (0, 2): 0x04,
    (0, 3): 0x40,
    (1, 0): 0x08,
    (1, 1): 0x10,
    (1, 2): 0x20,
    (1, 3): 0x80,
}


def to_braille(bitmap: tuple[str, ...]) -> tuple[str, ...]:
    """Turn rows of '#' and '.' into braille cells, 2 wide by 4 tall each."""
    if not bitmap:
        return ()

    height = len(bitmap)
    width = max(len(row) for row in bitmap)
    padded = [row.ljust(width, ".") for row in bitmap]

    lines = []
    for top in range(0, height, 4):
        line = []
        for left in range(0, width, 2):
            bits = 0
            for (dot_x, dot_y), bit in _DOT_BITS.items():
                y, x = top + dot_y, left + dot_x
                if y < height and x < width and padded[y][x] == "#":
                    bits |= bit
            line.append(chr(BRAILLE_BASE + bits))
        lines.append("".join(line))
    return tuple(lines)


# The OpenAI knot: a hexagonal outline with the interlocking loop suggested
# inside it. Traced from the Bootstrap Icons `openai` glyph.
OPENAI_BITMAP = (
    "...####...",
    "..#....#..",
    ".#..##..#.",
    "#..#..#..#",
    "#.#....#.#",
    "#.#....#.#",
    "#.#....#.#",
    "#..#..#..#",
    ".#..##..#.",
    "..#....#..",
    "...####...",
    "..........",
)

# The Claude burst: rays radiating from a solid centre, thicker towards the
# middle. Traced from the Bootstrap Icons `claude` glyph.
CLAUDE_BITMAP = (
    "....##....",
    "#...##...#",
    ".#..##..#.",
    "..#.##.#..",
    "...####...",
    "###.##.###",
    "###.##.###",
    "...####...",
    "..#.##.#..",
    ".#..##..#.",
    "#...##...#",
    "....##....",
)

OPENAI_BRAILLE = to_braille(OPENAI_BITMAP)
CLAUDE_BRAILLE = to_braille(CLAUDE_BITMAP)

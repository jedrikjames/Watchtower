"""Rasterise the Bootstrap Icons SVGs into the PNGs the image renderer ships.

    python packaging/render_icons.py

Run this only when the source SVGs change; the PNGs are committed so nobody
needs a rasteriser to build or run Watchtower.

Why a hand-rolled rasteriser rather than cairosvg or reportlab: both need a
native cairo library, which is a reliable way to make a build script fail on
Windows. These are two monochrome single-path icons, so a scanline fill with
nonzero winding is a couple of hundred lines and works anywhere Pillow does.

Bootstrap Icons are MIT licensed - see assets/icons/LICENSE.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SVG_DIR = ROOT / "assets" / "icons"
OUT_DIR = ROOT / "src" / "watchtower_tui" / "assets"

#: Rendered size in pixels. Generous, because the terminal decides how many
#: pixels a cell is and we would rather downscale than upscale.
SIZE = 256
#: Supersampling factor for antialiasing.
SS = 4
#: Points per curve segment when flattening. Plenty at this resolution.
CURVE_STEPS = 24

NUMBER = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
COMMAND = re.compile(r"([MmZzLlHhVvCcSsQqTtAa])")


def tokenise(data: str) -> list[tuple[str, list[float]]]:
    """Split path data into (command, numbers) pairs."""
    out: list[tuple[str, list[float]]] = []
    for chunk in (c for c in COMMAND.split(data) if c.strip()):
        if COMMAND.fullmatch(chunk):
            out.append((chunk, []))
        elif out:
            out[-1] = (out[-1][0], out[-1][1] + [float(n) for n in NUMBER.findall(chunk)])
    return out


def _cubic(p0, p1, p2, p3, steps=CURVE_STEPS):
    for i in range(1, steps + 1):
        t = i / steps
        u = 1 - t
        yield (
            u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
            u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1],
        )


def _quadratic(p0, p1, p2, steps=CURVE_STEPS):
    for i in range(1, steps + 1):
        t = i / steps
        u = 1 - t
        yield (
            u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
            u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
        )


def _arc(start, rx, ry, rotation, large_arc, sweep, end, steps=CURVE_STEPS):
    """SVG endpoint arc -> points, via the centre parameterisation in the spec."""
    if start == end:
        return
    if rx == 0 or ry == 0:
        yield end
        return

    rx, ry = abs(rx), abs(ry)
    phi = math.radians(rotation)
    cos_p, sin_p = math.cos(phi), math.sin(phi)

    dx2, dy2 = (start[0] - end[0]) / 2, (start[1] - end[1]) / 2
    x1 = cos_p * dx2 + sin_p * dy2
    y1 = -sin_p * dx2 + cos_p * dy2

    # Scale the radii up if they are too small to span the endpoints.
    lam = (x1 * x1) / (rx * rx) + (y1 * y1) / (ry * ry)
    if lam > 1:
        scale = math.sqrt(lam)
        rx, ry = rx * scale, ry * scale

    denom = rx * rx * y1 * y1 + ry * ry * x1 * x1
    numer = max(0.0, rx * rx * ry * ry - denom)
    coef = (0 if denom == 0 else math.sqrt(numer / denom)) * (-1 if large_arc == sweep else 1)
    cx1, cy1 = coef * rx * y1 / ry, -coef * ry * x1 / rx

    cx = cos_p * cx1 - sin_p * cy1 + (start[0] + end[0]) / 2
    cy = sin_p * cx1 + cos_p * cy1 + (start[1] + end[1]) / 2

    def angle(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        mag = math.hypot(ux, uy) * math.hypot(vx, vy)
        if mag == 0:
            return 0.0
        value = math.acos(max(-1.0, min(1.0, dot / mag)))
        return -value if ux * vy - uy * vx < 0 else value

    theta = angle(1, 0, (x1 - cx1) / rx, (y1 - cy1) / ry)
    delta = angle((x1 - cx1) / rx, (y1 - cy1) / ry, (-x1 - cx1) / rx, (-y1 - cy1) / ry)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi

    for i in range(1, steps + 1):
        t = theta + delta * i / steps
        px, py = rx * math.cos(t), ry * math.sin(t)
        yield (cos_p * px - sin_p * py + cx, sin_p * px + cos_p * py + cy)


def flatten(data: str) -> list[list[tuple[float, float]]]:
    """Path data -> a list of closed polygons."""
    polygons: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    point = (0.0, 0.0)
    start = (0.0, 0.0)
    last_cubic = None
    last_quad = None

    def close():
        nonlocal current
        if len(current) > 2:
            polygons.append(current)
        current = []

    for command, numbers in tokenise(data):
        relative = command.islower()
        code = command.upper()
        i = 0

        if code == "Z":
            close()
            point = start
            continue

        while True:
            if code == "M":
                need = 2
            elif code in "LT":
                need = 2
            elif code in "HV":
                need = 1
            elif code == "C":
                need = 6
            elif code in "SQ":
                need = 4
            elif code == "A":
                need = 7
            else:
                break
            if i + need > len(numbers):
                break

            args = numbers[i : i + need]
            i += need

            if code == "M":
                close()
                point = (point[0] + args[0], point[1] + args[1]) if relative else (args[0], args[1])
                start = point
                current = [point]
                code = "L"  # further pairs after a moveto are implicit linetos
            elif code == "L":
                point = (point[0] + args[0], point[1] + args[1]) if relative else (args[0], args[1])
                current.append(point)
            elif code == "H":
                point = (point[0] + args[0], point[1]) if relative else (args[0], point[1])
                current.append(point)
            elif code == "V":
                point = (point[0], point[1] + args[0]) if relative else (point[0], args[0])
                current.append(point)
            elif code in ("C", "S"):
                if code == "C":
                    c1 = (point[0] + args[0], point[1] + args[1]) if relative else (args[0], args[1])
                    c2 = (point[0] + args[2], point[1] + args[3]) if relative else (args[2], args[3])
                    end = (point[0] + args[4], point[1] + args[5]) if relative else (args[4], args[5])
                else:
                    c1 = (
                        (2 * point[0] - last_cubic[0], 2 * point[1] - last_cubic[1])
                        if last_cubic
                        else point
                    )
                    c2 = (point[0] + args[0], point[1] + args[1]) if relative else (args[0], args[1])
                    end = (point[0] + args[2], point[1] + args[3]) if relative else (args[2], args[3])
                current.extend(_cubic(point, c1, c2, end))
                last_cubic, last_quad = c2, None
                point = end
            elif code in ("Q", "T"):
                if code == "Q":
                    c1 = (point[0] + args[0], point[1] + args[1]) if relative else (args[0], args[1])
                    end = (point[0] + args[2], point[1] + args[3]) if relative else (args[2], args[3])
                else:
                    c1 = (
                        (2 * point[0] - last_quad[0], 2 * point[1] - last_quad[1])
                        if last_quad
                        else point
                    )
                    end = (point[0] + args[0], point[1] + args[1]) if relative else (args[0], args[1])
                current.extend(_quadratic(point, c1, end))
                last_quad, last_cubic = c1, None
                point = end
            elif code == "A":
                end = (point[0] + args[5], point[1] + args[6]) if relative else (args[5], args[6])
                current.extend(
                    _arc(point, args[0], args[1], args[2], bool(args[3]), bool(args[4]), end)
                )
                point = end
                last_cubic = last_quad = None

            if code not in ("C", "S", "Q", "T"):
                last_cubic = last_quad = None
            if i >= len(numbers):
                break

    close()
    return polygons


def fill(polygons, width: int, height: int) -> Image.Image:
    """Scanline fill with the nonzero winding rule, so holes come out as holes.

    Pillow's polygon() cannot express holes, and these icons are full of them -
    the OpenAI knot is mostly hole. Hence doing it by hand.
    """
    mask = Image.new("L", (width, height), 0)
    pixels = mask.load()

    edges = []
    for polygon in polygons:
        for i in range(len(polygon)):
            (x0, y0), (x1, y1) = polygon[i], polygon[(i + 1) % len(polygon)]
            if y0 != y1:
                edges.append((x0, y0, x1, y1))

    for y in range(height):
        sample = y + 0.5
        crossings = []
        for x0, y0, x1, y1 in edges:
            if (y0 <= sample < y1) or (y1 <= sample < y0):
                t = (sample - y0) / (y1 - y0)
                crossings.append((x0 + t * (x1 - x0), 1 if y1 > y0 else -1))
        if not crossings:
            continue

        crossings.sort()
        winding = 0
        for index in range(len(crossings) - 1):
            winding += crossings[index][1]
            if winding != 0:
                left = max(0, int(math.ceil(crossings[index][0] - 0.5)))
                right = min(width - 1, int(math.floor(crossings[index + 1][0] - 0.5)))
                for x in range(left, right + 1):
                    pixels[x, y] = 255
    return mask


def render(svg_path: Path, out_path: Path, size: int = SIZE) -> None:
    text = svg_path.read_text(encoding="utf-8")

    box = re.search(r'viewBox="([^"]+)"', text)
    min_x, min_y, vb_w, vb_h = (float(v) for v in re.split(r"[ ,]+", box.group(1).strip()))

    data = " ".join(re.findall(r'\sd="([^"]+)"', text))
    polygons = flatten(data)

    big = size * SS
    scale = big / max(vb_w, vb_h)
    scaled = [[((x - min_x) * scale, (y - min_y) * scale) for x, y in poly] for poly in polygons]

    mask = fill(scaled, big, big).resize((size, size), Image.LANCZOS)

    # White icon on transparency: the terminal supplies its own background, and
    # the widget tints it to the provider accent.
    icon = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    icon.putalpha(mask)
    icon.paste(Image.new("RGBA", (size, size), (255, 255, 255, 255)), (0, 0), mask)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    icon.save(out_path)
    coverage = sum(mask.getdata()) / (size * size * 255)
    print(f"{svg_path.name} -> {out_path.name}  {size}x{size}  ink {coverage:.1%}")


def main() -> int:
    for name in ("openai", "claude"):
        svg = SVG_DIR / f"{name}.svg"
        if not svg.is_file():
            print(f"missing {svg}")
            return 1
        render(svg, OUT_DIR / f"{name}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

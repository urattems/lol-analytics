"""Display-only signed regions: no changes to frames, metrics or exports."""
from math import isfinite

BLUE = "#5aa9ff"
RED = "#ff7186"


def leading_side(value, player_side):
    if value is None or not isfinite(value) or player_side not in ("blue", "red"):
        return None
    if value == 0:
        return "equal"
    return player_side if value > 0 else ("red" if player_side == "blue" else "blue")


def advantage_regions(minutes, values, player_side):
    """Two constant traces, with explicit gaps and visual zero crossings only.

    Each adjacent valid segment forms its own trapezoid against zero. Splitting
    segments at zero prevents either color spilling into the opposite region.
    Isolated observations do not invent a duration/area.
    """
    regions = {side: {"x": [], "y": []} for side in ("blue", "red")}
    def add(x0, y0, x1, y1):
        side = leading_side(y0 if y0 else y1, player_side)
        if side in regions:
            regions[side]["x"].extend([x0, x0, x1, x1, x0, None])
            regions[side]["y"].extend([0, y0, y1, 0, 0, None])
    for i in range(1, len(values)):
        x0, x1, y0, y1 = minutes[i-1], minutes[i], values[i-1], values[i]
        if leading_side(y0, player_side) is None or leading_side(y1, player_side) is None:
            continue
        if y0 * y1 < 0:
            cross = x0 + (x1-x0) * abs(y0) / (abs(y0)+abs(y1))
            add(x0, y0, cross, 0)
            add(cross, 0, x1, y1)
        else:
            add(x0, y0, x1, y1)
    return regions


def advantage_label(value, player_side):
    side = leading_side(value, player_side)
    if side is None:
        return "Avantage indisponible"
    if side == "equal":
        return "Égalité"
    amount = f"{abs(value):,.0f}".replace(",", " ")
    return f"{side.title()} Side +{amount} g"

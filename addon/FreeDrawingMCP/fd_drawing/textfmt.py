"""Dimension text exactly as TechDraw displays it (DimensionFormatter port).

``DrawViewDimension.getText()`` differs between GUI and console mode, so the
snapshot formats values itself: prefix + value per ``%.Nw|f|g|r`` + suffix.
"""

import math
import re

_SPEC = re.compile(r"%(?P<dec>[-+ #0]*\d*(?:\.\d+)?)(?P<spec>[wWrRfFgGeE])")


def format_value(value, format_spec):
    """Apply a TechDraw FormatSpec to ``value`` and return the full string."""
    if format_spec is None:
        format_spec = "%.2w"
    m = _SPEC.search(format_spec)
    if not m:
        return format_spec
    dec, spec = m.group("dec"), m.group("spec").lower()
    if spec == "w":
        s = ("%" + dec + "f") % value
        if "." in s:
            s = s.rstrip("0").rstrip(".")
    elif spec == "r":
        try:
            rounder = float(dec) if dec else 1.0
        except ValueError:
            rounder = 1.0
        rv = round(value / rounder) * rounder if rounder else value
        decimals = len(dec.split(".")[1].rstrip("0")) if "." in dec else 0
        s = f"{rv:.{decimals}f}"
    else:
        s = ("%" + dec + spec) % value
    return format_spec[: m.start()] + s + format_spec[m.end():]


def tolerance_text(dim):
    """'' or the tolerance suffix shown next to the value."""
    try:
        over = float(dim.OverTolerance.Value if hasattr(dim.OverTolerance, "Value") else dim.OverTolerance)
        under = float(dim.UnderTolerance.Value if hasattr(dim.UnderTolerance, "Value") else dim.UnderTolerance)
    except Exception:
        return ""
    if getattr(dim, "ArbitraryTolerances", False):
        o = str(dim.FormatSpecOverTolerance)
        u = str(dim.FormatSpecUnderTolerance)
        return f" {o}/{u}".rstrip("/ ") if (o or u) else ""
    if abs(over) < 1e-12 and abs(under) < 1e-12:
        return ""
    if getattr(dim, "EqualTolerance", False) or abs(over + under) < 1e-12:
        return " ±" + format_value(abs(over), "%.3w")
    return " " + format_value(over, "%+.3w") + "/" + format_value(under, "%+.3w")


def display_text(dim):
    """Value text plus tolerance, as drawn on the sheet."""
    spec = str(getattr(dim, "FormatSpec", "%.2w"))
    if getattr(dim, "Arbitrary", False):
        text = spec
    else:
        try:
            value = float(dim.getRawValue())
        except Exception:
            value = float("nan")
        if math.isnan(value):
            text = spec
        else:
            text = format_value(value, spec)
            if getattr(dim, "ShowUnits", False) and dim.Type not in ("Angle", "Angle3Pt"):
                text += " mm"
    return text + tolerance_text(dim)

import re
from typing import Optional


_RUG_KEYWORDS = [
    "fabric rug",
    "large rug",
    "rug",
    "carpet",
    "mat",
    "러그",
    "카페트",
    "카펫",
]

_DIM_KEY_PATTERNS = {
    "width_mm": r"(?:\bW\b|width|가로|너비)\s*[:=]?\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?",
    "depth_mm": r"(?:\bD\b|depth|세로|깊이)\s*[:=]?\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?",
    "height_mm": r"(?:\bH\b|height|높이)\s*[:=]?\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?",
}
_LENGTH_PAT = r"(?:\bL\b|length|len)\s*[:=]?\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?"
_TRIPLE_PATTERNS = [
    r"([0-9][0-9,\.]*)\s*(mm|cm|m)?\s*[xX]\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?\s*[xX]\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?",
    r"\bW\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?\s*\bD\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?\s*\bH\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?",
]
_DOUBLE_PATTERNS = [
    r"([0-9][0-9,\.]*)\s*(mm|cm|m)?\s*[xX]\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?",
]

_DIM_2D_OK_PAT = re.compile(
    r"(tv|mirror|frame|art|painting|poster|picture|print|wall|wall\s*-?\s*mounted|wall\s*system|rug|carpet|mat|러그|카페트|카펫|액자|그림|거울|벽걸이|월\s*시스템)",
    re.IGNORECASE,
)


def is_rug_like(label: str) -> bool:
    try:
        text = (label or "").strip().lower()
        if not text:
            return False
        for keyword in _RUG_KEYWORDS:
            if keyword in text:
                if keyword == "mat":
                    if re.search(r"\bmat\b", text):
                        return True
                    continue
                return True
        return False
    except Exception:
        return False


def to_mm(value: float, unit: Optional[str]) -> int:
    unit_text = (unit or "").strip().lower()
    try:
        if unit_text in ("mm",):
            return int(round(value))
        if unit_text in ("cm",):
            return int(round(value * 10.0))
        if unit_text in ("m", "meter", "metre"):
            return int(round(value * 1000.0))
        if value <= 20.0:
            return int(round(value * 1000.0))
        return int(round(value))
    except Exception:
        return 0


def parse_object_dimensions_mm(text: str) -> dict:
    raw_text = text or ""
    normalized = raw_text.replace("×", "x").replace("횞", "x")
    out = {"width_mm": None, "depth_mm": None, "height_mm": None, "radius_mm": None, "raw": {}}

    for pattern in _TRIPLE_PATTERNS:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        n1, u1, n2, u2, n3, u3 = match.groups()

        def _num(value):
            return float(str(value).replace(",", ""))

        width_mm = to_mm(_num(n1), u1)
        depth_mm = to_mm(_num(n2), u2 or u1)
        height_mm = to_mm(_num(n3), u3 or u2 or u1)
        if width_mm:
            out["width_mm"] = width_mm
        if depth_mm:
            out["depth_mm"] = depth_mm
        if height_mm:
            out["height_mm"] = height_mm
        out["raw"]["triple"] = match.group(0)
        return out

    for key, pattern in _DIM_KEY_PATTERNS.items():
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        num_str, unit = match.group(1), match.group(2)
        try:
            value = float(num_str.replace(",", ""))
        except Exception:
            continue
        mm = to_mm(value, unit)
        if mm:
            out[key] = mm
            out["raw"][key] = match.group(0)

    match = re.search(
        r"(?:\bR\b|radius|반지름)\s*[:=]?\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?",
        normalized,
        flags=re.IGNORECASE,
    )
    if match:
        num_str, unit = match.group(1), match.group(2)
        try:
            value = float(num_str.replace(",", ""))
        except Exception:
            value = None
        if value is not None:
            mm = to_mm(value, unit)
            if mm:
                out["radius_mm"] = mm
                out["raw"]["radius_mm"] = match.group(0)

    if not out["width_mm"]:
        match = re.search(_LENGTH_PAT, normalized, flags=re.IGNORECASE)
        if match:
            num_str, unit = match.group(1), match.group(2)
            try:
                value = float(num_str.replace(",", ""))
            except Exception:
                value = None
            if value is not None:
                mm = to_mm(value, unit)
                if mm:
                    out["width_mm"] = mm
                    out["raw"]["length"] = match.group(0)

    if not out["height_mm"]:
        match = re.search(r"(?:\bSH\b|SH)\s*[:=]?\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?", normalized, flags=re.IGNORECASE)
        if match:
            num_str, unit = match.group(1), match.group(2)
            try:
                value = float(num_str.replace(",", ""))
            except Exception:
                value = None
            if value is not None:
                mm = to_mm(value, unit)
                if mm:
                    out["height_mm"] = mm
                    out["raw"]["seat_height"] = match.group(0)

    if not any([out["width_mm"], out["depth_mm"], out["height_mm"]]):
        for pattern in _DOUBLE_PATTERNS:
            match = re.search(pattern, normalized, flags=re.IGNORECASE)
            if not match:
                continue
            n1, u1, n2, u2 = match.groups()

            def _num(value):
                return float(str(value).replace(",", ""))

            v1 = to_mm(_num(n1), u1)
            v2 = to_mm(_num(n2), u2 or u1)
            if re.search(r"\b(poster|frame|wall|art|painting)\b", normalized, flags=re.IGNORECASE):
                if v1:
                    out["width_mm"] = v1
                if v2:
                    out["height_mm"] = v2
            else:
                if v1:
                    out["width_mm"] = v1
                if v2:
                    out["depth_mm"] = v2
            out["raw"]["double"] = match.group(0)
            break

    return out


def parse_room_dimensions_mm(text: str) -> dict:
    normalized = (text or "").strip()
    if not normalized:
        return {"width_mm": 0, "depth_mm": 0, "height_mm": 0}
    normalized = normalized.replace("×", "x").replace("횞", "x").replace("X", "x")

    match = re.search(_TRIPLE_PATTERNS[0], normalized, flags=re.IGNORECASE)
    if match:
        n1, u1, n2, u2, n3, u3 = match.groups()

        def _num(value):
            return float(str(value).replace(",", ""))

        width_mm = to_mm(_num(n1), u1)
        depth_mm = to_mm(_num(n2), u2 or u1)
        height_mm = to_mm(_num(n3), u3 or u2 or u1)
        return {"width_mm": width_mm or 0, "depth_mm": depth_mm or 0, "height_mm": height_mm or 0}

    parts = re.findall(r"([0-9][0-9,\.]*)\s*(mm|cm|m)?", normalized, flags=re.IGNORECASE)
    values = []
    for num_str, unit in parts:
        try:
            value = float(num_str.replace(",", ""))
        except Exception:
            continue
        values.append(to_mm(value, unit))
    values = [value for value in values if value > 0]
    if not values:
        return {"width_mm": 0, "depth_mm": 0, "height_mm": 0}
    if len(values) == 1:
        return {"width_mm": values[0], "depth_mm": 0, "height_mm": 0}
    if len(values) == 2:
        return {"width_mm": values[0], "depth_mm": values[1], "height_mm": 0}
    return {"width_mm": values[0], "depth_mm": values[1], "height_mm": values[2]}


def normalize_dims_dict(raw: dict) -> dict:
    if not isinstance(raw, dict):
        return {}

    def _pick(*keys):
        for key in keys:
            if key in raw and raw.get(key) is not None:
                return raw.get(key)
        return None

    width = _pick("width_mm", "width", "w")
    depth = _pick("depth_mm", "depth", "d")
    height = _pick("height_mm", "height", "h")
    radius = _pick("radius_mm", "radius", "r")

    out = {}
    try:
        if width is not None:
            out["width_mm"] = int(width)
    except Exception:
        pass
    try:
        if depth is not None:
            out["depth_mm"] = int(depth)
    except Exception:
        pass
    try:
        if height is not None:
            out["height_mm"] = int(height)
    except Exception:
        pass
    try:
        if radius is not None:
            out["radius_mm"] = int(radius)
    except Exception:
        pass
    return out


def dims_has_positive_values(dims: dict) -> bool:
    if not isinstance(dims, dict):
        return False
    for key in ("width_mm", "depth_mm", "height_mm", "radius_mm"):
        try:
            if int(dims.get(key) or 0) > 0:
                return True
        except Exception:
            continue
    return False


def is_two_dim_ok_label(label: str) -> bool:
    try:
        return bool(_DIM_2D_OK_PAT.search((label or "").strip()))
    except Exception:
        return False


def available_dim_axes(dims: dict) -> set:
    axes = set()
    try:
        if int(dims.get("width_mm") or 0) > 0:
            axes.add("W")
    except Exception:
        pass
    try:
        if int(dims.get("depth_mm") or 0) > 0:
            axes.add("D")
    except Exception:
        pass
    try:
        if int(dims.get("height_mm") or 0) > 0:
            axes.add("H")
    except Exception:
        pass
    try:
        if int(dims.get("radius_mm") or 0) > 0:
            axes.add("R")
    except Exception:
        pass
    return axes


def dims_to_str(dims: dict) -> str:
    if not isinstance(dims, dict):
        return ""
    width = dims.get("width_mm")
    depth = dims.get("depth_mm")
    height = dims.get("height_mm")
    radius = dims.get("radius_mm")
    if width or depth or height or radius:
        base = f" Dimensions: W={width or 'null'}mm, D={depth or 'null'}mm, H={height or 'null'}mm."
        if radius:
            base += f" R={radius}mm."
        return base
    return ""

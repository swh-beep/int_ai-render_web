import re


def parse_placement_constraints(text: str | None) -> dict:
    raw = str(text or "").strip()
    normalized = re.sub(r"\s+", " ", raw.lower())
    constraints = {
        "original_text": raw,
        "horizontal_anchor": None,
        "depth_anchor": None,
        "window_relation": None,
        "clearance": [],
        "symmetry": None,
    }
    if not normalized:
        return constraints

    anchor_match = re.search(
        r"(?:place|keep|put|position|anchor).{0,50}\b(left|right|center|centred|centered|middle)\b",
        normalized,
    )
    if anchor_match:
        token = anchor_match.group(1)
        if token in {"left"}:
            constraints["horizontal_anchor"] = "left"
        elif token in {"right"}:
            constraints["horizontal_anchor"] = "right"
        else:
            constraints["horizontal_anchor"] = "center"
    else:
        has_left = any(token in normalized for token in ["left side", "left wall", "left corner", "to the left", "on the left"])
        has_right = any(token in normalized for token in ["right side", "right wall", "right corner", "to the right", "on the right"])
        if has_left and not has_right:
            constraints["horizontal_anchor"] = "left"
        elif has_right and not has_left:
            constraints["horizontal_anchor"] = "right"
        elif any(token in normalized for token in ["center", "centred", "centered", "middle"]):
            constraints["horizontal_anchor"] = "center"

    if any(token in normalized for token in ["back wall", "against the wall", "against back wall", "rear wall", "along the wall"]):
        constraints["depth_anchor"] = "back_wall"
    elif any(token in normalized for token in ["floating", "float in the room", "pulled forward"]):
        constraints["depth_anchor"] = "floating"

    if any(token in normalized for token in ["near window", "near the window", "window side", "by the window", "window-adjacent", "next to the window"]):
        constraints["window_relation"] = "near_window"
    elif any(token in normalized for token in ["away from window", "keep window clear", "clear of the window", "not near the window"]):
        constraints["window_relation"] = "away_from_window"

    if any(token in normalized for token in ["walkway", "circulation", "clearance", "breathing room", "not cramped", "keep open", "spacing"]):
        constraints["clearance"].append("Preserve open circulation and visible breathing room.")
    if any(token in normalized for token in ["symmetry", "symmetrical", "balanced"]):
        constraints["symmetry"] = "prefer_symmetric"
    if any(token in normalized for token in ["asymmetry", "asymmetrical", "off-center"]):
        constraints["symmetry"] = "prefer_asymmetric"

    return constraints


def build_placement_prompt_block(text: str | None) -> str:
    constraints = parse_placement_constraints(text)
    if not constraints.get("original_text"):
        return ""

    lines = ["<PLACEMENT CONSTRAINTS (NORMALIZED)>"]
    if constraints.get("horizontal_anchor"):
        lines.append(f"- HORIZONTAL ANCHOR: {constraints['horizontal_anchor'].upper()} side.")
    if constraints.get("depth_anchor") == "back_wall":
        lines.append("- DEPTH ANCHOR: keep the primary furniture against the back wall unless impossible.")
    elif constraints.get("depth_anchor") == "floating":
        lines.append("- DEPTH ANCHOR: allow the primary furniture to float forward in the room.")
    if constraints.get("window_relation") == "near_window":
        lines.append("- WINDOW RELATION: keep the requested item near the window side.")
    elif constraints.get("window_relation") == "away_from_window":
        lines.append("- WINDOW RELATION: keep the requested item away from the window side.")
    for rule in constraints.get("clearance") or []:
        lines.append(f"- CLEARANCE: {rule}")
    if constraints.get("symmetry") == "prefer_symmetric":
        lines.append("- COMPOSITION: preserve a symmetric layout if it does not conflict with the anchor rules.")
    elif constraints.get("symmetry") == "prefer_asymmetric":
        lines.append("- COMPOSITION: keep the layout intentionally asymmetric if it does not conflict with the anchor rules.")
    lines.append(f"- ORIGINAL REQUEST: {constraints['original_text']}")
    lines.append("- HARD RULE: placement constraints override aesthetic balancing. Do not re-center the scene unless CENTER was requested.")
    lines.append("--------------------------------------------------")
    return "\n".join(lines) + "\n"

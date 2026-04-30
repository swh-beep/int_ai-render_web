# -*- coding: utf-8 -*-

ROOM_STYLES = {
    "Living room": [
        "French-modern",
        "Luxury",
        "Mid-Century",
        "Modern",
        "Natural",
        "Oriental",
        "Scandinavian",
        "Unique",
    ],
    "Dining room": [
        "French-modern",
        "Luxury",
        "Mid-Century",
        "Modern",
        "Natural",
        "Oriental",
        "Scandinavian",
        "Unique",
    ],
    "Bedroom": [
        "French-modern",
        "Luxury",
        "Mid-Century",
        "Modern",
        "Natural",
        "Oriental",
        "Scandinavian",
        "Unique",
    ],
}


_STYLE_LIBRARY = {
    "french-modern": {
        "prompt": (
            "Blend refined French detailing with restrained modern styling. "
            "Prefer elegant profiles, tailored upholstery, sculpted wood or metal details, "
            "and a composed editorial balance without clutter."
        ),
        "furniture_specs": {},
    },
    "luxury": {
        "prompt": (
            "Aim for quiet luxury rather than flashy staging. "
            "Use premium materials, generous proportions, deliberate spacing, "
            "and polished but believable lighting with exact product fidelity."
        ),
        "furniture_specs": {},
    },
    "mid-century": {
        "prompt": (
            "Preserve a mid-century modern mood with clean structural lines, warm wood tones, "
            "grounded lounge seating, and balanced negative space. "
            "Keep silhouettes crisp and avoid overstuffed substitutions."
        ),
        "furniture_specs": {},
    },
    "modern": {
        "prompt": (
            "Keep the room modern, architectural, and uncluttered. "
            "Favor clean geometry, confident spacing, and exact furniture identity "
            "without decorative noise or unnecessary accessories."
        ),
        "furniture_specs": {},
    },
    "natural": {
        "prompt": (
            "Keep the room bright, calm, and natural. "
            "Favor warm neutrals, soft daylight, tactile natural materials, "
            "and relaxed but precise staging without changing the listed product identities."
        ),
        "furniture_specs": {},
    },
    "oriental": {
        "prompt": (
            "Respect an oriental-inspired mood through calm symmetry, low visual noise, "
            "natural materials, and restrained decorative emphasis. "
            "Keep the arrangement grounded and avoid western generic substitutions."
        ),
        "furniture_specs": {},
    },
    "scandinavian": {
        "prompt": (
            "Keep the room Scandinavian in tone: airy daylight, pale materials, "
            "minimal but warm styling, and practical uncluttered composition. "
            "Maintain exact product identity and avoid overdecorating the scene."
        ),
        "furniture_specs": {},
    },
    "unique": {
        "prompt": (
            "Allow a distinctive editorial character, but preserve the listed products exactly. "
            "The room may feel bold or collectible, yet the staging must remain coherent, "
            "architecturally believable, and free of random extra objects."
        ),
        "furniture_specs": {},
    },
}


STYLES = {}
for _canonical_key, _payload in _STYLE_LIBRARY.items():
    STYLES[_canonical_key] = _payload
    title_key = _canonical_key.replace("-", " ").title().replace(" ", "-")
    STYLES[title_key] = _payload

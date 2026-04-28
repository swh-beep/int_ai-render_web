from application.render.product_identity_stage import build_product_identity_bundle


def test_build_product_identity_bundle_extracts_gap_support_and_reflection_features():
    items = [
        {
            "target_key": "sofa-1",
            "label": "De Sede Sofa",
            "category": "sofa",
            "requested_dims_mm": {"width_mm": 2600, "depth_mm": 1100, "height_mm": 720},
            "description": "Black leather sofa with a split back gap and segmented low-profile form.",
            "crop_path": "outputs/example.png",
            "reference_features": {
                "silhouette_cues": ["low-profile", "segmented"],
                "material_cues": ["leather"],
                "distinctive_parts": ["split back gap", "broad base"],
                "preserve_rules": ["preserve center gap", "keep modular backrest"],
                "reflective_surface": False,
            },
            "identity_profile": {
                "material_cues": ["leather"],
                "shape_cues": ["low-profile"],
                "preserve_rules": ["preserve center gap"],
            },
        },
        {
            "target_key": "mirror-1",
            "label": "Rounded Mirror",
            "category": "mirror",
            "requested_dims_mm": {"width_mm": 800, "depth_mm": 60, "height_mm": 1500},
            "description": "Rounded black frame wall mirror with reflective plane.",
            "reference_features": {
                "silhouette_cues": ["rounded"],
                "material_cues": ["mirror", "black frame"],
                "distinctive_parts": ["rounded frame"],
                "preserve_rules": ["keep reflection orientation"],
                "reflective_surface": True,
            },
            "identity_profile": {"family": "mirror"},
        },
    ]

    enriched, identities = build_product_identity_bundle(items)

    sofa = next(row for row in enriched if row["target_key"] == "sofa-1")
    mirror = next(row for row in enriched if row["target_key"] == "mirror-1")

    assert sofa["product_identity"]["family"] == "sofa"
    assert "split back gap" in sofa["product_identity"]["opening_or_gap_features"]
    assert "preserve center gap" in sofa["product_identity"]["preserve_rules"]
    assert sofa["identity_confidence"] > 0.3

    assert mirror["product_identity"]["family"] == "mirror"
    assert "reflective_surface" in mirror["product_identity"]["reflection_constraints"]
    assert mirror["identity_strictness"] == "critical"
    assert len(identities) == 2

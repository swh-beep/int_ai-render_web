from types import SimpleNamespace

import pytest

from application.media import frontal_view_workflow, image_edit_workflow


def _build_s3_prefix(audience: str, category: str, subfolder: str | None) -> str:
    return f"{audience}/{category}/{subfolder or ''}"


@pytest.mark.parametrize(
    ("mode", "stage_name", "category"),
    [
        ("edit", "image_studio_edit", "editrendered"),
        ("decorate", "image_studio_decorate", "decorrendered"),
    ],
)
def test_image_edit_workflow_corrects_final_output_before_publish(
    monkeypatch,
    mode,
    stage_name,
    category,
):
    correction_calls = []
    published = []

    def _correct(image_path, **kwargs):
        correction_calls.append((image_path, kwargs))
        return SimpleNamespace(path=f"{image_path}.corrected.jpg")

    def _resolve(path, s3_prefix_override=None):
        published.append((path, s3_prefix_override))
        return f"https://example.test/{path}"

    monkeypatch.setattr(
        image_edit_workflow,
        "apply_reference_relative_white_balance",
        _correct,
    )

    result = image_edit_workflow.run_image_edit_job(
        {
            "photo_paths": ["target-upload"],
            "instructions": "change the room",
            "mode": mode,
            "unique_id": "unit",
            "audience": "internal",
        },
        normalize_audience=lambda audience: audience or "internal",
        build_s3_prefix=_build_s3_prefix,
        materialize_input=lambda path, label: f"local-{path}",
        resolve_image_url=_resolve,
        process_image_edit_logic=lambda *args: "generated.png",
    )

    assert correction_calls == [
        (
            "generated.png",
            {
                "reference_path": "local-target-upload",
                "stage_name": stage_name,
            },
        )
    ]
    assert published[-1] == (
        "generated.png.corrected.jpg",
        f"internal/{category}/rendered",
    )
    assert result["urls"] == ["https://example.test/generated.png.corrected.jpg"]


def test_real_photo_workflow_corrects_final_output_before_publish(monkeypatch):
    correction_calls = []
    published = []

    def _correct(image_path, **kwargs):
        correction_calls.append((image_path, kwargs))
        return SimpleNamespace(path=f"{image_path}.corrected.jpg")

    def _resolve(path, s3_prefix_override=None):
        published.append((path, s3_prefix_override))
        return f"https://example.test/{path}"

    monkeypatch.setattr(
        frontal_view_workflow,
        "apply_reference_relative_white_balance",
        _correct,
    )

    result = frontal_view_workflow.run_frontal_view_job(
        {
            "photo_paths": ["primary-upload", "secondary-upload"],
            "unique_id": "unit",
            "audience": "internal",
        },
        normalize_audience=lambda audience: audience or "internal",
        build_s3_prefix=_build_s3_prefix,
        materialize_input=lambda path, label: f"local-{path}",
        resolve_image_url=_resolve,
        generate_frontal_room_from_photos=lambda *args: "frontal.png",
    )

    assert correction_calls == [
        (
            "frontal.png",
            {
                "reference_path": "local-primary-upload",
                "stage_name": "image_studio_real_photo",
            },
        )
    ]
    assert published[-1] == (
        "frontal.png.corrected.jpg",
        "internal/realphotorendered/rendered",
    )
    assert result["urls"] == ["https://example.test/frontal.png.corrected.jpg"]

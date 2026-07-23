from application.render.render_response_stage import build_render_response_payload


def test_build_render_response_payload_persists_local_crop_urls_without_mutating_input(tmp_path):
    crop_path = tmp_path / "chair.png"
    crop_path.write_bytes(b"crop")
    furniture_data = [
        {"label": "Chair", "target_key": "cart_1", "crop_path": str(crop_path)},
        {"label": "Table", "target_key": "cart_2", "crop_path": str(tmp_path / "missing.png")},
    ]
    original = [dict(item) for item in furniture_data]
    uploads = []

    def resolve(path, s3_prefix_override=None):
        uploads.append((path, s3_prefix_override))
        return f"https://cdn.example/{s3_prefix_override}/{path}"

    payload = build_render_response_payload(
        std_path="outputs/original.png",
        step1_img="outputs/empty.png",
        scale_guide_path=None,
        generated_results=["outputs/result.png"],
        moodboard_url=None,
        furniture_data=furniture_data,
        volume_ranking=[],
        prefix_main_user="internal/mainrendered/user",
        prefix_main_empty="internal/mainrendered/empty",
        prefix_main_rendered="internal/mainrendered/rendered",
        artifact_root_prefix="internal/jobs/job-1",
        resolve_image_url=resolve,
    )

    persisted = payload["furniture_data"]
    assert persisted[0]["crop_path"] == str(crop_path)
    assert persisted[0]["crop_url"] == f"https://cdn.example/internal/jobs/job-1/source-references/{crop_path}"
    assert "crop_url" not in persisted[1]
    assert furniture_data == original
    assert (str(crop_path), "internal/jobs/job-1/source-references") in uploads


def test_build_render_response_payload_does_not_reupload_existing_crop_url(tmp_path):
    crop_path = tmp_path / "chair.png"
    crop_path.write_bytes(b"crop")
    furniture_data = [{"label": "Chair", "crop_path": str(crop_path), "crop_url": "https://cdn.example/existing.png"}]
    uploads = []

    def resolve(path, s3_prefix_override=None):
        uploads.append((path, s3_prefix_override))
        return f"https://cdn.example/{s3_prefix_override}/{path}"

    payload = build_render_response_payload(
        std_path="outputs/original.png",
        step1_img="outputs/empty.png",
        scale_guide_path=None,
        generated_results=["outputs/result.png"],
        moodboard_url=None,
        furniture_data=furniture_data,
        volume_ranking=[],
        prefix_main_user="internal/mainrendered/user",
        prefix_main_empty="internal/mainrendered/empty",
        prefix_main_rendered="internal/mainrendered/rendered",
        artifact_root_prefix="internal/jobs/job-1",
        resolve_image_url=resolve,
    )

    assert payload["furniture_data"][0]["crop_url"] == "https://cdn.example/existing.png"
    assert (str(crop_path), "internal/jobs/job-1/source-references") not in uploads

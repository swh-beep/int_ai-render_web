import json
from pathlib import Path
from types import SimpleNamespace

from tools.replay import exactness_qc_replay as replay


def _write_png(path: Path) -> None:
    path.write_bytes(
        bytes.fromhex(
            "89504E470D0A1A0A"
            "0000000D4948445200000001000000010802000000907753DE"
            "0000000C49444154789C6360600000000400010D0A2DB40000000049454E44AE426082"
        )
    )


def test_internal_manifest_builds_render_payload(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    item_path = tmp_path / "item.png"
    _write_png(room_path)
    _write_png(item_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "mode": "internal_itemized_render",
                "entrypoint": "/async/render",
                "form_data": {
                    "room": "livingroom",
                    "style": "Customize",
                    "variant": "1",
                    "dimensions": "4000*4000*2400",
                    "placement": "",
                },
                "room_file": {"path": "room.png"},
                "item_files": {"item-1": "item.png"},
                "items_json": [
                    {
                        "client_id": "item-1",
                        "name": "Chair",
                        "category": "chair",
                        "qty": 1,
                        "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 900},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def _build_internal(**kwargs):
        return {
            "file_path": kwargs["raw_path"],
            "moodboard_items": [{"target_key": "tk-1"}],
            "room": kwargs["room"],
            "style": kwargs["style"],
            "variant": kwargs["variant"],
            "dimensions": kwargs["dimensions"],
            "placement": kwargs["placement"],
            "audience": "internal",
        }

    monkeypatch.setattr(
        replay.main,
        "_queue_route_deps",
        lambda: SimpleNamespace(
            build_internal_itemized_async_render_job_payload=_build_internal,
            resolve_image_url=lambda path, prefix=None: path,
            build_s3_prefix=lambda audience, category=None, subfolder=None: "s3://stub/",
            build_item_target_key=lambda *args, **kwargs: "tk-1",
        ),
    )

    case = replay.load_case_manifest(manifest_path)
    invocation = replay.build_replay_invocation(case)

    assert invocation.job_runner_name == "job_render"
    assert invocation.job_payload["room"] == "livingroom"
    assert invocation.payload_metadata["item_count"] == 1
    assert invocation.persist_result is False


def test_external_cart_manifest_uses_existing_builder_and_report_fields(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    item_path = tmp_path / "item.png"
    _write_png(room_path)
    _write_png(item_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "mode": "external_cart_render",
                "entrypoint": "/api/external/render/cart",
                "output_dir": str(tmp_path),
                "report_filename": "report.json",
                "request": {
                    "image_url": str(room_path),
                    "room": "livingroom",
                    "style": "Scandinavian",
                    "variant": "2",
                    "dimensions": "",
                    "placement": "Keep the window clear",
                    "items": [
                        {
                            "id": "sku-1",
                            "category": "table",
                            "image_url": str(item_path),
                            "qty": 1,
                            "dims_mm": {"width_mm": 900, "depth_mm": 900, "height_mm": 350},
                        }
                    ],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def _build_cart(req, **kwargs):
        assert req.image_url == str(room_path)
        assert req.items[0].image_url == str(item_path)
        return (
            {
                "render": {
                    "file_path": req.image_url,
                    "moodboard_items": [{"target_key": "cart-1"}],
                    "room": req.room,
                    "style": "Customize",
                    "variant": req.variant,
                    "dimensions": req.dimensions,
                    "placement": "summary",
                    "audience": "external",
                },
                "extra": {"cart_kept": [{"id": "sku-1"}], "cart_dropped": []},
            },
            [{"id": "sku-1"}],
            [],
        )

    monkeypatch.setattr(
        replay.main,
        "_queue_route_deps",
        lambda: SimpleNamespace(
            cart_max_items=12,
            apply_cart_limits=lambda items, max_items: (items, []),
            build_cart_summary=lambda items: "summary",
            materialize_input=lambda path, prefix="input": path,
            normalize_item_image=lambda local_path, unique_id, index: local_path,
            resolve_image_url=lambda path, prefix=None: path,
            build_s3_prefix=lambda audience, category=None, subfolder=None: "s3://stub/",
            build_item_target_key=lambda *args, **kwargs: "cart-1",
            build_external_cart_job=_build_cart,
        ),
    )
    monkeypatch.setattr(
        replay.main,
        "job_render_with_details",
        lambda payload: {
            "result_url": "https://example.com/render.png",
            "result_urls": ["https://example.com/render.png", "https://example.com/alt.png"],
            "selected_result_reason": "hard_qc_pass_ranked",
        },
    )

    case = replay.load_case_manifest(manifest_path)
    report_path = replay.run_case(case)
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))

    assert report["job_runner"] == "job_render_with_details"
    assert report["job_payload_metadata"]["cart_kept_count"] == 1
    assert report["selected_result_info"]["result_url"] == "https://example.com/render.png"
    assert report["result_urls"] == ["https://example.com/render.png", "https://example.com/alt.png"]


def test_external_preset_manifest_builds_resolved_metadata(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    _write_png(room_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "mode": "external_preset_render",
                "entrypoint": "/api/external/render/preset",
                "request": {
                    "image_url": str(room_path),
                    "preset_id": "preset-123",
                    "dimensions": "",
                    "placement": "",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def _build_preset(req, preset_map):
        assert req.preset_id == "preset-123"
        return (
            {
                "render": {
                    "file_path": req.image_url,
                    "room": "bedroom",
                    "style": "Minimal",
                    "variant": "4",
                    "dimensions": req.dimensions,
                    "placement": req.placement,
                    "audience": "external",
                },
                "extra": {"preset_id": req.preset_id},
            },
            {"room": "bedroom", "style": "Minimal", "variant": "4"},
        )

    monkeypatch.setattr(
        replay.main,
        "_queue_route_deps",
        lambda: SimpleNamespace(
            load_preset_map=lambda: {"preset-123": {}},
            build_external_preset_job=_build_preset,
        ),
    )

    case = replay.load_case_manifest(manifest_path)
    invocation = replay.build_replay_invocation(case)

    assert invocation.job_runner_name == "job_render_with_details"
    assert invocation.payload_metadata["preset_id"] == "preset-123"
    assert invocation.payload_metadata["resolved"] == {"room": "bedroom", "style": "Minimal", "variant": "4"}

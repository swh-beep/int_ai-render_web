from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import importlib
import threading
import time


ROOT = Path(__file__).resolve().parents[1]


def test_main_wires_angle_generation_and_quality_gate_to_separate_providers():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    detail_block = source.split("generate_detail_view=lambda", 1)[1].split(
        "volume_ranking_snapshot=_volume_ranking_snapshot",
        1,
    )[0]

    assert "call_gemini_with_failover=CALL_REPAIR_IMAGE_WITH_PROVIDER" in detail_block
    assert "model_name=REPAIR_IMAGE_MODEL_NAME" in detail_block
    assert "call_analysis_with_failover=call_gemini_with_failover" in detail_block
    assert "analysis_model_name=ANALYSIS_MODEL_NAME" in detail_block
    assert "safe_json_from_model_text=_safe_json_from_model_text" in detail_block
    assert "refurnish_locked_angle=_generate_locked_angle_furnishing" in detail_block


def test_locked_angle_stage2_uses_detail_timeout_budget(tmp_path, monkeypatch):
    main = importlib.import_module("main")
    captured = {}
    monkeypatch.setattr(main.time, "time", lambda: 4321.5)
    crop_path = tmp_path / "sofa-cutout.png"
    crop_path.write_bytes(b"product-cutout")

    monkeypatch.setattr(
        main,
        "build_furniture_specs_json",
        lambda inventory: {"items": list(inventory)},
    )
    atlas_paths = []
    atlas_inputs = []

    def _build_atlas(
        _furnished_path,
        _empty_path,
        output_path,
        *,
        item_boxes=None,
    ):
        atlas_path = Path(output_path)
        atlas_path.parent.mkdir(parents=True, exist_ok=True)
        atlas_path.write_bytes(b"atlas")
        atlas_paths.append(atlas_path)
        atlas_inputs.extend(item_boxes or [])
        return str(atlas_path)

    monkeypatch.setattr(
        main,
        "build_furniture_only_reference_atlas",
        _build_atlas,
    )
    monkeypatch.setattr(
        main,
        "_detect_locked_angle_reference_boxes_once",
        lambda _path: [
            {
                "label": "Detected Sofa",
                "box_2d": [420, 180, 820, 780],
            }
        ],
    )

    def _generate_furnished_room(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"path": "outputs/locked-stage2.png"}

    monkeypatch.setattr(main, "generate_furnished_room", _generate_furnished_room)

    result = main._generate_locked_angle_furnishing(
        guide_path="outputs/validated-guide.png",
        furnished_main_path="outputs/furnished-main.png",
        empty_room_path="outputs/original-empty-room.png",
        style_prompt="Keep the guide camera locked.",
        unique_id="timeout-budget",
        furniture_data=[
            {
                "target_key": "sofa-1",
                "label": "Sofa",
                "box_2d": [430, 190, 810, 770],
                "box_source": "main_render",
                "crop_path": str(crop_path),
            }
        ],
        geometry_contract={
            "item_targets": [
                {"target_key": "sofa-1", "family": "sofa", "qty": 1},
                {"target_key": "lamp-1", "family": "floor_lamp", "qty": 1},
            ]
        },
        timeout_sec=42.5,
    )

    assert result == {
        "path": "outputs/locked-stage2.png",
        "inventory_reference_mode": "detected_object_atlas",
        "product_cutout_reference_count": 1,
    }
    assert captured["args"][0] == "outputs/validated-guide.png"
    assert captured["kwargs"]["furnished_scene_reference_path"] is None
    assert captured["kwargs"]["furniture_atlas_reference_path"] == str(atlas_paths[0])
    assert result["inventory_reference_mode"] == "detected_object_atlas"
    assert result["product_cutout_reference_count"] == 1
    assert atlas_inputs[0]["label"] == "Detected Sofa"
    assert any(item.get("target_key") == "sofa-1" for item in atlas_inputs)
    assert not atlas_paths[0].exists()
    assert captured["kwargs"]["max_generation_attempts"] == 1
    assert captured["kwargs"]["total_timeout_limit_override"] == 42.5
    assert captured["kwargs"]["start_time"] == 4321.5
    assert [
        item["target_key"]
        for item in captured["kwargs"]["furniture_specs_json"]["items"]
    ] == ["sofa-1", "lamp-1"]


def test_locked_angle_stage2_fails_closed_when_furniture_atlas_is_unavailable(
    monkeypatch,
):
    main = importlib.import_module("main")
    monkeypatch.setattr(
        main,
        "build_furniture_only_reference_atlas",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        main,
        "_detect_locked_angle_reference_boxes_once",
        lambda _path: [],
    )
    monkeypatch.setattr(
        main,
        "generate_furnished_room",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Stage2 must not run without the camera-neutral atlas")
        ),
    )

    result = main._generate_locked_angle_furnishing(
        guide_path="outputs/validated-guide.png",
        furnished_main_path="outputs/furnished-main.png",
        empty_room_path="outputs/original-empty-room.png",
        style_prompt="Keep the guide camera locked.",
        unique_id="missing-atlas",
        furniture_data=[{"target_key": "sofa-1", "label": "Sofa"}],
        geometry_contract={"item_targets": [{"target_key": "sofa-1"}]},
        timeout_sec=42.5,
    )

    assert result == {
        "path": None,
        "inventory_reference_mode": "furniture_only_atlas_unavailable",
        "product_cutout_reference_count": 0,
    }


def test_locked_angle_reference_detection_is_single_flight_and_copy_safe(
    tmp_path,
    monkeypatch,
):
    main = importlib.import_module("main")
    source_path = tmp_path / "main-render.jpg"
    source_path.write_bytes(b"source-file-identity")
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def _detect(path, **kwargs):
        calls.append((path, kwargs))
        entered.set()
        assert release.wait(timeout=3.0)
        return [{"label": "Ivory Sofa", "box_2d": [420, 180, 820, 780]}]

    monkeypatch.setattr(main, "detect_furniture_boxes", _detect)
    main._clear_locked_angle_reference_box_cache()
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            first = executor.submit(
                main._detect_locked_angle_reference_boxes_once,
                str(source_path),
            )
            assert entered.wait(timeout=3.0)
            remaining = [
                executor.submit(
                    main._detect_locked_angle_reference_boxes_once,
                    str(source_path),
                )
                for _ in range(7)
            ]
            time.sleep(0.05)
            release.set()
            results = [first.result(timeout=3.0)]
            results.extend(future.result(timeout=3.0) for future in remaining)

        assert len(calls) == 1
        assert calls[0][1] == {"timeout_sec": 120, "max_attempts": 3}
        assert all(result == results[0] for result in results)
        results[0][0]["label"] = "Mutated Caller Copy"
        cached_again = main._detect_locked_angle_reference_boxes_once(
            str(source_path)
        )
        assert cached_again[0]["label"] == "Ivory Sofa"
        assert not main._locked_angle_reference_box_inflight
    finally:
        main._clear_locked_angle_reference_box_cache()


def test_locked_angle_reference_detection_failure_releases_waiters(
    tmp_path,
    monkeypatch,
):
    main = importlib.import_module("main")
    source_path = tmp_path / "main-render.jpg"
    source_path.write_bytes(b"source-file-identity")
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def _detect(_path, **_kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(timeout=3.0)
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(main, "detect_furniture_boxes", _detect)
    main._clear_locked_angle_reference_box_cache()
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                main._detect_locked_angle_reference_boxes_once,
                str(source_path),
            )
            assert entered.wait(timeout=3.0)
            second = executor.submit(
                main._detect_locked_angle_reference_boxes_once,
                str(source_path),
            )
            time.sleep(0.05)
            release.set()
            assert first.result(timeout=3.0) == []
            assert second.result(timeout=3.0) == []

        assert len(calls) == 1
        assert not main._locked_angle_reference_box_inflight
    finally:
        main._clear_locked_angle_reference_box_cache()


def test_locked_angle_reference_detection_cache_invalidates_when_source_changes(
    tmp_path,
    monkeypatch,
):
    main = importlib.import_module("main")
    source_path = tmp_path / "main-render.jpg"
    source_path.write_bytes(b"first-source-version")
    calls = []

    def _detect(_path, **_kwargs):
        calls.append(1)
        return [
            {
                "label": f"Detected Version {len(calls)}",
                "box_2d": [420, 180, 820, 780],
            }
        ]

    monkeypatch.setattr(main, "detect_furniture_boxes", _detect)
    main._clear_locked_angle_reference_box_cache()
    try:
        first = main._detect_locked_angle_reference_boxes_once(str(source_path))
        cached = main._detect_locked_angle_reference_boxes_once(str(source_path))
        source_path.write_bytes(b"second-source-version-with-new-size")
        changed = main._detect_locked_angle_reference_boxes_once(str(source_path))

        assert len(calls) == 2
        assert first == cached
        assert first[0]["label"] == "Detected Version 1"
        assert changed[0]["label"] == "Detected Version 2"
    finally:
        main._clear_locked_angle_reference_box_cache()


def test_locked_angle_inventory_boxes_require_current_render_provenance():
    main = importlib.import_module("main")
    rows = [
        {
            "label": "Main Sofa",
            "box_2d": [400, 100, 800, 700],
            "box_source": "main_render",
        },
        {
            "label": "Localized Chair",
            "box_2d": [500, 720, 800, 900],
            "box_source": "product_reference_localization",
            "detail_localization_status": "product_reference_verified",
        },
        {
            "label": "Unverified Product",
            "box_2d": [100, 100, 300, 300],
            "box_source": "product_reference_localization",
        },
        {
            "label": "Moodboard Placeholder",
            "box_2d": [100, 100, 300, 300],
            "box_source": "source_reference",
        },
        {
            "label": "Missing Provenance",
            "box_2d": [100, 100, 300, 300],
        },
    ]

    selected = main._locked_angle_inventory_reference_items(rows)

    assert [item["label"] for item in selected] == [
        "Main Sofa",
        "Localized Chair",
    ]
    assert main._locked_angle_inventory_needs_fresh_detection(
        rows,
        selected,
    )

    complete_rows = [
        {
            "label": f"Localized Object {index}",
            "box_2d": [100 + index * 100, 100, 180 + index * 100, 240],
            "box_source": "main_render",
        }
        for index in range(4)
    ]
    assert not main._locked_angle_inventory_needs_fresh_detection(
        complete_rows,
        complete_rows,
    )

    geometry_ordered_rows = [
        {
            "label": f"Small Object {index}",
            "volume_rank": index + 3,
            "volume_proxy": 100 - index,
            "box_2d": [100 + index * 100, 100, 180 + index * 100, 240],
            "box_source": "main_render",
        }
        for index in range(3)
    ]
    geometry_ordered_rows.append(
        {
            "label": "Main Sofa",
            "volume_rank": 1,
            "volume_proxy": 10000,
            "box_2d": [0, 0, 1000, 1000],
            "box_source": "source_reference",
        }
    )
    trusted_small_items = main._locked_angle_inventory_reference_items(
        geometry_ordered_rows
    )
    assert main._locked_angle_inventory_needs_fresh_detection(
        geometry_ordered_rows,
        trusted_small_items,
    )

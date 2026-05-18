from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from application.marketing import routes
from application.marketing.db import MarketingDatabaseConfigError
from application.marketing.repository import MarketingReelsRepository, create_sqlite_memory_repository


def _client():
    repo = create_sqlite_memory_repository()
    return _client_for_repo(repo)


def _client_for_repo(repo: MarketingReelsRepository):
    routes.reset_marketing_repository_cache()
    routes.get_marketing_repository = lambda: repo
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def _create_group(client: TestClient) -> tuple[str, str]:
    group_id, clip_ids = _create_group_with_clips(client, 3)
    return group_id, clip_ids[0]


def _create_group_with_clips(client: TestClient, count: int = 3) -> tuple[str, list[str]]:
    response = client.post(
        "/api/marketing/reel-groups",
        json={
            "global_prompt": "warm reel",
            "platform": "Instagram",
            "tone": "Editorial",
            "goal": "awareness",
            "clips": [
                {
                    "client_image_id": f"client-{index + 1}",
                    "source_image_url": f"/outputs/{index + 1}.png",
                    "order": index + 1,
                    "prompt": f"clip {index + 1}",
                    "duration_sec": 5,
                }
                for index in range(count)
            ],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    return payload["group_id"], [clip["clip_id"] for clip in payload["clips"]]


def _post_group_with_clip_count(client: TestClient, count: int):
    response = client.post(
        "/api/marketing/reel-groups",
        json={
            "global_prompt": "warm reel",
            "platform": "Instagram",
            "tone": "Editorial",
            "goal": "awareness",
            "clips": [
                {
                    "client_image_id": f"client-{index + 1}",
                    "source_image_url": f"/outputs/{index + 1}.png",
                    "order": index + 1,
                    "prompt": f"clip {index + 1}",
                    "duration_sec": 5,
                }
                for index in range(count)
            ],
        },
    )
    return response


def test_group_create_requires_one_to_ten_clips():
    client = _client()

    assert _post_group_with_clip_count(client, 0).status_code == 422
    assert _post_group_with_clip_count(client, 1).status_code == 200
    assert _post_group_with_clip_count(client, 2).status_code == 200
    assert _post_group_with_clip_count(client, 3).status_code == 200
    assert _post_group_with_clip_count(client, 10).status_code == 200
    assert _post_group_with_clip_count(client, 11).status_code == 422


def test_db_config_error_returns_service_unavailable():
    original_get_repository = routes.get_marketing_repository
    routes.reset_marketing_repository_cache()
    routes.get_marketing_repository = lambda: (_ for _ in ()).throw(
        MarketingDatabaseConfigError("Marketing DB password could not be read from AWS SSM. Set MARKETING_DB_PASSWORD locally.")
    )
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    try:
        response = _post_group_with_clip_count(client, 3)
    finally:
        routes.get_marketing_repository = original_get_repository
        routes.reset_marketing_repository_cache()

    assert response.status_code == 503
    assert "MARKETING_DB_PASSWORD" in response.json()["detail"]


def test_marketing_db_health_returns_connected_status():
    original_get_engine = routes.get_marketing_engine
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    routes.get_marketing_engine = lambda: engine
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    try:
        response = client.get("/api/marketing/db-health")
    finally:
        routes.get_marketing_engine = original_get_engine

    assert response.status_code == 200
    assert response.json() == {"ok": True, "status": "connected", "database": "marketing"}


def test_marketing_db_health_returns_service_unavailable_on_config_error():
    original_get_engine = routes.get_marketing_engine
    routes.get_marketing_engine = lambda: (_ for _ in ()).throw(
        MarketingDatabaseConfigError("Marketing DB password could not be read from AWS SSM. Set MARKETING_DB_PASSWORD locally.")
    )
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    try:
        response = client.get("/api/marketing/db-health")
    finally:
        routes.get_marketing_engine = original_get_engine

    assert response.status_code == 503
    assert "MARKETING_DB_PASSWORD" in response.json()["detail"]


def test_global_prompt_history_can_be_saved_and_listed():
    client = _client()

    save_response = client.post(
        "/api/marketing/global-prompts",
        json={"global_prompt": "warm oak showroom reel"},
    )
    list_response = client.get("/api/marketing/global-prompts")

    assert save_response.status_code == 200
    saved = save_response.json()
    assert saved["global_prompt"] == "warm oak showroom reel"
    assert saved["id"]
    assert list_response.status_code == 200
    assert list_response.json()[0]["global_prompt"] == "warm oak showroom reel"


def test_audio_prompt_history_can_be_saved_listed_and_soft_deleted_without_global_leakage():
    repo = create_sqlite_memory_repository()
    client = _client_for_repo(repo)

    global_response = client.post(
        "/api/marketing/global-prompts",
        json={"global_prompt": "shared global prompt"},
    )
    clip_response = client.post(
        "/api/marketing/clip-prompts",
        json={"title": "Window opening shot", "prompt": "slow push toward sheer curtain"},
    )
    save_response = client.post(
        "/api/marketing/audio-prompts",
        json={"title": "Soft room tone", "prompt": "subtle interior ambience, no speech"},
    )
    list_response = client.get("/api/marketing/audio-prompts")
    global_list_response = client.get("/api/marketing/global-prompts")
    clip_list_response = client.get("/api/marketing/clip-prompts")

    assert global_response.status_code == 200
    assert clip_response.status_code == 200
    assert save_response.status_code == 200
    saved = save_response.json()
    assert saved["title"] == "Soft room tone"
    assert saved["prompt"] == "subtle interior ambience, no speech"
    assert list_response.status_code == 200
    assert list_response.json()[0]["title"] == "Soft room tone"
    assert list_response.json()[0]["prompt"] == "subtle interior ambience, no speech"
    assert [item["global_prompt"] for item in global_list_response.json()] == ["shared global prompt"]
    assert [item["prompt"] for item in clip_list_response.json()] == ["slow push toward sheer curtain"]

    delete_response = client.delete(f"/api/marketing/audio-prompts/{saved['id']}")
    assert delete_response.status_code == 200
    assert client.get("/api/marketing/audio-prompts").json() == []
    with repo.engine.begin() as conn:
        row = conn.execute(
            text("SELECT deleted_at FROM marketing_global_prompts WHERE id = :id"),
            {"id": saved["id"]},
        ).mappings().one()
    assert row["deleted_at"] is not None
    assert client.delete(f"/api/marketing/audio-prompts/{saved['id']}").status_code == 404


def test_clip_prompt_history_can_be_saved_listed_and_deleted_without_global_leakage():
    client = _client()

    global_response = client.post(
        "/api/marketing/global-prompts",
        json={"global_prompt": "shared global prompt"},
    )
    save_response = client.post(
        "/api/marketing/clip-prompts",
        json={"title": "Window opening shot", "prompt": "slow push toward sheer curtain"},
    )
    list_response = client.get("/api/marketing/clip-prompts")
    global_list_response = client.get("/api/marketing/global-prompts")

    assert global_response.status_code == 200
    assert save_response.status_code == 200
    saved = save_response.json()
    assert saved["title"] == "Window opening shot"
    assert saved["prompt"] == "slow push toward sheer curtain"
    assert list_response.status_code == 200
    assert list_response.json()[0]["title"] == "Window opening shot"
    assert list_response.json()[0]["prompt"] == "slow push toward sheer curtain"
    assert global_list_response.status_code == 200
    assert [item["global_prompt"] for item in global_list_response.json()] == ["shared global prompt"]

    delete_response = client.delete(f"/api/marketing/clip-prompts/{saved['id']}")
    assert delete_response.status_code == 200
    assert client.get("/api/marketing/clip-prompts").json() == []


def test_clip_prompt_history_rejects_blank_title_or_prompt():
    client = _client()

    blank_title = client.post("/api/marketing/clip-prompts", json={"title": "   ", "prompt": "valid"})
    blank_prompt = client.post("/api/marketing/clip-prompts", json={"title": "Valid", "prompt": "   "})

    assert blank_title.status_code == 422
    assert blank_prompt.status_code == 422


def test_global_prompt_history_can_be_soft_deleted():
    repo = create_sqlite_memory_repository()
    client = _client_for_repo(repo)
    saved = client.post(
        "/api/marketing/global-prompts",
        json={"global_prompt": "prompt to delete"},
    ).json()

    delete_response = client.delete(f"/api/marketing/global-prompts/{saved['id']}")
    list_response = client.get("/api/marketing/global-prompts")

    assert delete_response.status_code == 200
    assert delete_response.json() == {"id": saved["id"]}
    assert list_response.status_code == 200
    assert list_response.json() == []
    with repo.engine.begin() as conn:
        row = conn.execute(
            text("SELECT deleted_at FROM marketing_global_prompts WHERE id = :id"),
            {"id": saved["id"]},
        ).mappings().one()
    assert row["deleted_at"] is not None
    assert client.delete(f"/api/marketing/global-prompts/{saved['id']}").status_code == 404


def test_global_prompt_history_rejects_blank_prompt():
    client = _client()

    response = client.post("/api/marketing/global-prompts", json={"global_prompt": "   "})

    assert response.status_code == 422


def test_group_create_requires_sequential_clip_order():
    client = _client()

    base_clips = [
        {
            "client_image_id": f"client-{index + 1}",
            "source_image_url": f"/outputs/{index + 1}.png",
            "order": index + 1,
            "prompt": f"clip {index + 1}",
            "duration_sec": 5,
        }
        for index in range(3)
    ]
    duplicate_response = client.post(
        "/api/marketing/reel-groups",
        json={"clips": [{**clip, "order": 1} if index == 1 else clip for index, clip in enumerate(base_clips)]},
    )
    gapped_response = client.post(
        "/api/marketing/reel-groups",
        json={"clips": [{**clip, "order": 4} if index == 2 else clip for index, clip in enumerate(base_clips)]},
    )

    assert duplicate_response.status_code == 422
    assert gapped_response.status_code == 422


def test_group_create_detail_and_list_preserve_aspect_ratio_and_video_quality():
    client = _client()
    response = client.post(
        "/api/marketing/reel-groups",
        json={
            "global_prompt": "wide reel",
            "platform": "Instagram",
            "tone": "Editorial",
            "goal": "awareness",
            "aspect_ratio": "16:9",
            "video_quality": "1080p",
            "clips": [
                {
                    "client_image_id": "client-wide",
                    "source_image_url": "/outputs/wide.png",
                    "order": 1,
                    "prompt": "wide opening",
                    "duration_sec": 5,
                }
            ],
        },
    )

    assert response.status_code == 200
    group_id = response.json()["group_id"]
    clip_id = response.json()["clips"][0]["clip_id"]
    assert response.json()["aspect_ratio"] == "16:9"
    assert response.json()["video_quality"] == "1080p"
    assert client.patch(
        f"/api/marketing/reel-groups/{group_id}/clips/source-images",
        json={
            "clips": [
                {
                    "clip_id": clip_id,
                    "source_image_url": "/outputs/wide.png",
                }
            ]
        },
    ).status_code == 200
    assert client.post(
        f"/api/marketing/reel-groups/{group_id}/clip-attempts",
        json={
            "attempt_id": "attempt-wide",
            "clip_id": clip_id,
            "source_job_id": "job-wide",
            "source_job_item_index": 0,
            "prompt": "wide opening",
            "duration_sec": 5,
            "status": "COMPLETED",
            "source_video_url": "/outputs/wide.mp4",
        },
    ).status_code == 200
    assert client.get(f"/api/marketing/reel-groups/{group_id}").json()["aspect_ratio"] == "16:9"
    assert client.get(f"/api/marketing/reel-groups/{group_id}").json()["video_quality"] == "1080p"
    listed = client.get("/api/marketing/reel-groups?limit=1").json()[0]
    assert listed["aspect_ratio"] == "16:9"
    assert listed["video_quality"] == "1080p"


def test_group_create_detail_and_update_persist_audio_settings():
    client = _client()
    response = client.post(
        "/api/marketing/reel-groups",
        json={
            "global_prompt": "wide reel",
            "audio_enabled": True,
            "audio_prompt": "subtle interior ambience, no speech",
            "platform": "Instagram",
            "tone": "Editorial",
            "goal": "awareness",
            "aspect_ratio": "16:9",
            "video_quality": "1080p",
            "clips": [
                {
                    "client_image_id": "client-audio",
                    "source_image_url": "/outputs/audio.png",
                    "order": 1,
                    "prompt": "audio opening",
                    "duration_sec": 5,
                }
            ],
        },
    )

    assert response.status_code == 200
    group_id = response.json()["group_id"]
    assert response.json()["audio_enabled"] is True
    assert response.json()["audio_prompt"] == "subtle interior ambience, no speech"
    detail = client.get(f"/api/marketing/reel-groups/{group_id}").json()
    assert detail["audio_enabled"] is True
    assert detail["audio_prompt"] == "subtle interior ambience, no speech"

    update_response = client.patch(
        f"/api/marketing/reel-groups/{group_id}/audio-settings",
        json={"audio_enabled": False, "audio_prompt": "keep this saved prompt"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["audio_enabled"] is False
    assert update_response.json()["audio_prompt"] == "keep this saved prompt"
    updated_detail = client.get(f"/api/marketing/reel-groups/{group_id}").json()
    assert updated_detail["audio_enabled"] is False
    assert updated_detail["audio_prompt"] == "keep this saved prompt"


def test_group_aspect_ratio_defaults_to_vertical_for_legacy_payloads():
    client = _client()
    group_id, _ = _create_group(client)

    detail = client.get(f"/api/marketing/reel-groups/{group_id}").json()

    assert detail["aspect_ratio"] == "9:16"
    assert detail["video_quality"] == "720p"
    assert detail["audio_enabled"] is False
    assert detail["audio_prompt"] == ""


def test_marketing_video_group_attempt_approval_and_final_flow():
    client = _client()
    group_id, clip_id = _create_group(client)

    attempt_payload = {
        "attempt_id": "attempt-1",
        "clip_id": clip_id,
        "source_job_id": "job-1",
        "source_job_item_index": 0,
        "prompt": "opening",
        "duration_sec": 5,
        "status": "RUNNING",
    }
    response = client.post(f"/api/marketing/reel-groups/{group_id}/clip-attempts", json=attempt_payload)
    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"

    response = client.patch(
        f"/api/marketing/reel-groups/{group_id}/clip-attempts/attempt-1",
        json={"status": "COMPLETED", "source_video_url": "/outputs/a.mp4"},
    )
    assert response.status_code == 200
    assert response.json()["source_video_url"] == "/outputs/a.mp4"

    response = client.patch(
        f"/api/marketing/reel-groups/{group_id}/clips/{clip_id}/approval",
        json={"attempt_id": "attempt-1"},
    )
    assert response.status_code == 200
    assert response.json()["approved_attempt_id"] == "attempt-1"

    response = client.patch(
        f"/api/marketing/reel-groups/{group_id}/final",
        json={
            "compile_job_id": "compile-1",
            "final_video_url": "/outputs/final.mp4",
            "final_title": "Hotel mood bedroom",
            "compile_payload_summary": {"clips": 1},
        },
    )
    assert response.status_code == 200

    detail = client.get(f"/api/marketing/reel-groups/{group_id}").json()
    assert detail["final_video_url"] == "/outputs/final.mp4"
    assert detail["final_title"] == "Hotel mood bedroom"
    assert detail["clips"][0]["approved_attempt_id"] == "attempt-1"
    assert detail["clips"][0]["attempts"][0]["source_video_url"] == "/outputs/a.mp4"


def test_clip_generation_tracks_requested_drafts_and_attempts():
    client = _client()
    group_id, clip_ids = _create_group_with_clips(client, 3)

    response = client.post(
        f"/api/marketing/reel-groups/{group_id}/clip-generations",
        json={
            "generation_type": "REGENERATE",
            "clip_ids": [clip_ids[1]],
            "source_job_id": "job-regenerate-1",
        },
    )

    assert response.status_code == 200
    generation = response.json()
    assert generation["generation_type"] == "REGENERATE"
    assert generation["clip_ids"] == [clip_ids[1]]

    attempt_payload = {
        "attempt_id": "attempt-regenerated",
        "clip_id": clip_ids[1],
        "clip_generation_id": generation["clip_generation_id"],
        "source_job_id": "job-regenerate-1",
        "source_job_item_index": 0,
        "prompt": "changed single clip",
        "duration_sec": 5,
        "status": "COMPLETED",
        "source_video_url": "/outputs/regenerated.mp4",
    }
    attempt_response = client.post(f"/api/marketing/reel-groups/{group_id}/clip-attempts", json=attempt_payload)

    assert attempt_response.status_code == 200
    assert attempt_response.json()["clip_generation_id"] == generation["clip_generation_id"]
    detail = client.get(f"/api/marketing/reel-groups/{group_id}").json()
    assert detail["generations"][0]["clip_generation_id"] == generation["clip_generation_id"]
    assert detail["generations"][0]["clip_ids"] == [clip_ids[1]]


def test_final_composition_preserves_selected_attempt_order():
    client = _client()
    group_id, clip_ids = _create_group_with_clips(client, 3)
    generation = client.post(
        f"/api/marketing/reel-groups/{group_id}/clip-generations",
        json={"generation_type": "INITIAL", "clip_ids": clip_ids, "source_job_id": "job-1"},
    ).json()
    for index, clip_id in enumerate(clip_ids):
        attempt_id = f"attempt-{index + 1}"
        assert client.post(
            f"/api/marketing/reel-groups/{group_id}/clip-attempts",
            json={
                "attempt_id": attempt_id,
                "clip_id": clip_id,
                "clip_generation_id": generation["clip_generation_id"],
                "source_job_id": "job-1",
                "source_job_item_index": index,
                "prompt": f"clip {index + 1}",
                "duration_sec": 5,
                "status": "COMPLETED",
                "source_video_url": f"/outputs/{attempt_id}.mp4",
            },
        ).status_code == 200
        assert client.patch(
            f"/api/marketing/reel-groups/{group_id}/clips/{clip_id}/approval",
            json={"attempt_id": attempt_id},
        ).status_code == 200

    response = client.patch(
        f"/api/marketing/reel-groups/{group_id}/final",
        json={
            "compile_job_id": "compile-ordered",
            "final_video_url": "/outputs/final.mp4",
            "selected_attempt_ids": ["attempt-2", "attempt-1", "attempt-3"],
            "compile_payload_summary": {"clips": 3},
        },
    )

    assert response.status_code == 200
    detail = client.get(f"/api/marketing/reel-groups/{group_id}").json()
    assert detail["compositions"][0]["selected_attempt_ids"] == ["attempt-2", "attempt-1", "attempt-3"]

def test_attempt_and_final_writes_are_idempotent():
    client = _client()
    group_id, clip_id = _create_group(client)
    attempt_payload = {
        "attempt_id": "attempt-1",
        "clip_id": clip_id,
        "source_job_id": "job-1",
        "source_job_item_index": 0,
        "prompt": "opening",
        "duration_sec": 5,
        "status": "COMPLETED",
        "source_video_url": "/outputs/a.mp4",
    }

    assert client.post(f"/api/marketing/reel-groups/{group_id}/clip-attempts", json=attempt_payload).status_code == 200
    assert client.post(f"/api/marketing/reel-groups/{group_id}/clip-attempts", json=attempt_payload).status_code == 200
    assert client.patch(
        f"/api/marketing/reel-groups/{group_id}/clips/{clip_id}/approval",
        json={"attempt_id": "attempt-1"},
    ).status_code == 200

    final_payload = {
        "compile_job_id": "compile-1",
        "final_video_url": "/outputs/final.mp4",
        "compile_payload_summary": {"clips": 1},
    }
    assert client.patch(f"/api/marketing/reel-groups/{group_id}/final", json=final_payload).status_code == 200
    assert client.patch(f"/api/marketing/reel-groups/{group_id}/final", json=final_payload).status_code == 200

    detail = client.get(f"/api/marketing/reel-groups/{group_id}").json()
    assert len(detail["clips"][0]["attempts"]) == 1


def test_final_patch_requires_an_approved_source_clip():
    client = _client()
    group_id, clip_id = _create_group(client)
    assert client.post(
        f"/api/marketing/reel-groups/{group_id}/clip-attempts",
        json={
            "attempt_id": "attempt-unapproved",
            "clip_id": clip_id,
            "source_job_id": "job-1",
            "source_job_item_index": 0,
            "prompt": "opening",
            "duration_sec": 5,
            "status": "COMPLETED",
            "source_video_url": "/outputs/a.mp4",
        },
    ).status_code == 200

    response = client.patch(
        f"/api/marketing/reel-groups/{group_id}/final",
        json={
            "compile_job_id": "compile-no-approved",
            "final_video_url": "/outputs/final.mp4",
            "compile_payload_summary": {"clips": 1},
        },
    )

    assert response.status_code == 409


def test_only_completed_attempts_can_be_approved():
    client = _client()
    group_id, clip_id = _create_group(client)
    response = client.post(
        f"/api/marketing/reel-groups/{group_id}/clip-attempts",
        json={
            "attempt_id": "attempt-1",
            "clip_id": clip_id,
            "source_job_id": "job-1",
            "source_job_item_index": 0,
            "prompt": "opening",
            "duration_sec": 5,
            "status": "FAILED",
        },
    )
    assert response.status_code == 200

    response = client.patch(
        f"/api/marketing/reel-groups/{group_id}/clips/{clip_id}/approval",
        json={"attempt_id": "attempt-1"},
    )
    assert response.status_code == 409


def test_completed_attempt_without_video_url_cannot_be_created():
    client = _client()
    group_id, clip_id = _create_group(client)
    response = client.post(
        f"/api/marketing/reel-groups/{group_id}/clip-attempts",
        json={
            "attempt_id": "attempt-no-video",
            "clip_id": clip_id,
            "source_job_id": "job-1",
            "source_job_item_index": 0,
            "prompt": "opening",
            "duration_sec": 5,
            "status": "COMPLETED",
        },
    )
    assert response.status_code == 422


def test_attempt_update_to_completed_requires_video_url():
    client = _client()
    group_id, clip_id = _create_group(client)
    assert client.post(
        f"/api/marketing/reel-groups/{group_id}/clip-attempts",
        json={
            "attempt_id": "attempt-no-video",
            "clip_id": clip_id,
            "source_job_id": "job-1",
            "source_job_item_index": 0,
            "prompt": "opening",
            "duration_sec": 5,
            "status": "RUNNING",
        },
    ).status_code == 200
    response = client.patch(
        f"/api/marketing/reel-groups/{group_id}/clip-attempts/attempt-no-video",
        json={"status": "COMPLETED"},
    )
    assert response.status_code == 409


def test_shared_history_list_returns_group_summary():
    client = _client()
    group_id, clip_id = _create_group(client)

    response = client.patch(
        f"/api/marketing/reel-groups/{group_id}/clips/source-images",
        json={
            "clips": [
                {
                    "clip_id": clip_id,
                    "source_image_url": "https://cdn.example/marketing-kling/group-1/images/a.png",
                }
            ]
        },
    )
    assert response.status_code == 200
    assert client.post(
        f"/api/marketing/reel-groups/{group_id}/clip-attempts",
        json={
            "attempt_id": "attempt-history",
            "clip_id": clip_id,
            "source_job_id": "job-history",
            "source_job_item_index": 0,
            "prompt": "history",
            "duration_sec": 5,
            "status": "COMPLETED",
            "source_video_url": "/outputs/history.mp4",
        },
    ).status_code == 200

    response = client.get("/api/marketing/reel-groups?limit=5")
    assert response.status_code == 200
    assert response.json()[0]["group_id"] == group_id
    assert response.json()[0]["clip_count"] == 3


def test_shared_history_list_excludes_failed_setup_groups():
    client = _client()
    failed_group_id, _ = _create_group(client)
    source_only_group_id, source_only_clip_id = _create_group(client)
    empty_video_group_id, empty_video_clip_id = _create_group(client)
    reviewing_group_id, reviewing_clip_id = _create_group(client)

    assert client.patch(f"/api/marketing/reel-groups/{failed_group_id}/failed").status_code == 200
    assert client.patch(
        f"/api/marketing/reel-groups/{source_only_group_id}/clips/source-images",
        json={
            "clips": [
                {
                    "clip_id": source_only_clip_id,
                    "source_image_url": "https://cdn.example/marketing-kling/group-1/images/source-only.png",
                }
            ]
        },
    ).status_code == 200
    assert client.patch(
        f"/api/marketing/reel-groups/{reviewing_group_id}/clips/source-images",
        json={
            "clips": [
                {
                    "clip_id": reviewing_clip_id,
                    "source_image_url": "https://cdn.example/marketing-kling/group-1/images/reviewing.png",
                }
            ]
        },
    ).status_code == 200
    assert client.patch(
        f"/api/marketing/reel-groups/{empty_video_group_id}/clips/source-images",
        json={
            "clips": [
                {
                    "clip_id": empty_video_clip_id,
                    "source_image_url": "https://cdn.example/marketing-kling/group-1/images/empty-video.png",
                }
            ]
        },
    ).status_code == 200
    assert client.post(
        f"/api/marketing/reel-groups/{empty_video_group_id}/clip-attempts",
        json={
            "attempt_id": "attempt-empty-video",
            "clip_id": empty_video_clip_id,
            "source_job_id": "job-empty-video",
            "source_job_item_index": 0,
            "prompt": "empty video",
            "duration_sec": 5,
            "status": "COMPLETED",
            "source_video_url": "",
        },
    ).status_code == 422
    assert client.post(
        f"/api/marketing/reel-groups/{reviewing_group_id}/clip-attempts",
        json={
            "attempt_id": "attempt-reviewing",
            "clip_id": reviewing_clip_id,
            "source_job_id": "job-reviewing",
            "source_job_item_index": 0,
            "prompt": "reviewing",
            "duration_sec": 5,
            "status": "COMPLETED",
            "source_video_url": "/outputs/reviewing.mp4",
        },
    ).status_code == 200

    response = client.get("/api/marketing/reel-groups?limit=10")

    assert response.status_code == 200
    group_ids = [item["group_id"] for item in response.json()]
    assert reviewing_group_id in group_ids
    assert failed_group_id not in group_ids
    assert source_only_group_id not in group_ids
    assert empty_video_group_id not in group_ids


def test_group_can_be_created_before_image_upload_then_patch_source_urls():
    client = _client()
    response = client.post(
        "/api/marketing/reel-groups",
        json={
            "global_prompt": "warm reel",
            "platform": "Instagram",
            "tone": "Editorial",
            "goal": "awareness",
            "clips": [
                {
                    "client_image_id": f"client-{index + 1}",
                    "source_image_url": "",
                    "order": index + 1,
                    "prompt": f"clip {index + 1}",
                    "duration_sec": 5,
                }
                for index in range(3)
            ],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    group_id = payload["group_id"]
    clip_id = payload["clips"][0]["clip_id"]

    response = client.patch(
        f"/api/marketing/reel-groups/{group_id}/clips/source-images",
        json={
            "clips": [
                {
                    "clip_id": clip_id,
                    "source_image_url": "https://intea-ai-render.s3.ap-northeast-2.amazonaws.com/marketing-kling/group-1/images/a.png",
                }
            ]
        },
    )
    assert response.status_code == 200

    detail = client.get(f"/api/marketing/reel-groups/{group_id}").json()
    assert detail["status"] == "GENERATING"
    assert detail["clips"][0]["source_image_url"].endswith("/marketing-kling/group-1/images/a.png")


def test_group_detail_returns_end_frame_fields_after_frame_patch():
    client = _client()
    response = client.post(
        "/api/marketing/reel-groups",
        json={
            "clips": [
                {
                    "client_image_id": f"client-{index + 1}",
                    "source_image_url": "",
                    "end_image_url": None,
                    "generation_mode": "START_ONLY",
                    "order": index + 1,
                    "prompt": f"clip {index + 1}",
                    "duration_sec": 5,
                }
                for index in range(3)
            ],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    group_id = payload["group_id"]
    clip_id = payload["clips"][0]["clip_id"]

    response = client.patch(
        f"/api/marketing/reel-groups/{group_id}/clips/source-images",
        json={
            "clips": [
                {
                    "clip_id": clip_id,
                    "source_image_url": "https://cdn.example/marketing-kling/group-1/images/start/a.png",
                    "end_image_url": "https://cdn.example/marketing-kling/group-1/images/end/b.png",
                    "generation_mode": "START_END",
                }
            ]
        },
    )
    assert response.status_code == 200
    patched = response.json()["clips"][0]
    assert patched["source_image_url"].endswith("/images/start/a.png")
    assert patched["end_image_url"].endswith("/images/end/b.png")
    assert patched["generation_mode"] == "START_END"

    detail = client.get(f"/api/marketing/reel-groups/{group_id}").json()
    clip = detail["clips"][0]
    assert clip["source_image_url"].endswith("/images/start/a.png")
    assert clip["end_image_url"].endswith("/images/end/b.png")
    assert clip["generation_mode"] == "START_END"


def test_start_end_patch_requires_end_image_url():
    client = _client()
    group_id, clip_id = _create_group(client)

    response = client.patch(
        f"/api/marketing/reel-groups/{group_id}/clips/source-images",
        json={
            "clips": [
                {
                    "clip_id": clip_id,
                    "source_image_url": "https://cdn.example/marketing-kling/group-1/images/start/a.png",
                    "generation_mode": "START_END",
                }
            ]
        },
    )
    assert response.status_code == 422


def test_next_start_end_patch_persists_generation_mode_with_resolved_end_url():
    client = _client()
    group_id, clip_id = _create_group(client)

    response = client.patch(
        f"/api/marketing/reel-groups/{group_id}/clips/source-images",
        json={
            "clips": [
                {
                    "clip_id": clip_id,
                    "source_image_url": "https://cdn.example/marketing-kling/group-1/images/start/a.png",
                    "end_image_url": "https://cdn.example/marketing-kling/group-1/images/start/b.png",
                    "generation_mode": "NEXT_START_AS_END",
                }
            ]
        },
    )

    assert response.status_code == 200
    patched = response.json()["clips"][0]
    assert patched["generation_mode"] == "NEXT_START_AS_END"
    assert patched["end_image_url"].endswith("/images/start/b.png")


def test_ensure_schema_creates_clip_domain_tables_for_local_tests():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    repo = MarketingReelsRepository(engine)
    repo.ensure_schema()

    with engine.begin() as conn:
        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'")).all()}
        draft_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(clip_drafts)")).all()}
        attempt_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(clip_attempts)")).all()}

    assert {"clip_groups", "clip_drafts", "clip_generations", "clip_attempts", "clip_compositions"}.issubset(tables)
    assert {"end_image_url", "generation_mode", "version"}.issubset(draft_columns)
    assert {"clip_generation_id", "based_on_draft_version"}.issubset(attempt_columns)


def test_ensure_schema_adds_audio_columns_to_legacy_clip_groups():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(text(
            """
            CREATE TABLE clip_groups (
                id VARCHAR(36) PRIMARY KEY,
                status VARCHAR(24) NOT NULL DEFAULT 'DRAFT',
                global_prompt TEXT NOT NULL DEFAULT '',
                platform VARCHAR(64) NOT NULL DEFAULT '',
                tone VARCHAR(64) NOT NULL DEFAULT '',
                goal VARCHAR(255) NOT NULL DEFAULT '',
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        ))
        conn.execute(text(
            """
            CREATE TABLE marketing_global_prompts (
                id VARCHAR(36) PRIMARY KEY,
                global_prompt TEXT NOT NULL,
                created_at DATETIME NOT NULL
            )
            """
        ))

    repo = MarketingReelsRepository(engine)
    repo.ensure_schema()

    with engine.begin() as conn:
        group_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(clip_groups)")).all()}
        prompt_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(marketing_global_prompts)")).all()}

    assert {"aspect_ratio", "video_quality", "audio_enabled", "audio_prompt"}.issubset(group_columns)
    assert {"prompt_type", "title", "deleted_at"}.issubset(prompt_columns)


def test_start_only_patch_clears_stale_end_image_url():
    client = _client()
    group_id, clip_id = _create_group(client)

    response = client.patch(
        f"/api/marketing/reel-groups/{group_id}/clips/source-images",
        json={
            "clips": [
                {
                    "clip_id": clip_id,
                    "source_image_url": "https://cdn.example/marketing-kling/group-1/images/start/a.png",
                    "end_image_url": "https://cdn.example/marketing-kling/group-1/images/end/stale.png",
                    "generation_mode": "START_ONLY",
                }
            ]
        },
    )
    assert response.status_code == 200
    patched = response.json()["clips"][0]
    assert patched["generation_mode"] == "START_ONLY"
    assert patched["end_image_url"] is None

    detail = client.get(f"/api/marketing/reel-groups/{group_id}").json()
    assert detail["clips"][0]["generation_mode"] == "START_ONLY"
    assert detail["clips"][0]["end_image_url"] is None


def test_clip_delete_is_persisted_and_excluded_from_history_counts():
    client = _client()
    group_id, clip_ids = _create_group_with_clips(client, 3)
    assert client.patch(
        f"/api/marketing/reel-groups/{group_id}/clips/source-images",
        json={
            "clips": [
                {
                    "clip_id": clip_id,
                    "source_image_url": f"https://cdn.example/marketing-kling/group-1/images/{index}.png",
                }
                for index, clip_id in enumerate(clip_ids, start=1)
            ]
        },
    ).status_code == 200
    assert client.post(
        f"/api/marketing/reel-groups/{group_id}/clip-attempts",
        json={
            "attempt_id": "attempt-history-count",
            "clip_id": clip_ids[0],
            "source_job_id": "job-history-count",
            "source_job_item_index": 0,
            "prompt": "history count",
            "duration_sec": 5,
            "status": "COMPLETED",
            "source_video_url": "/outputs/history-count.mp4",
        },
    ).status_code == 200

    response = client.patch(f"/api/marketing/reel-groups/{group_id}/clips/{clip_ids[1]}/deleted")
    assert response.status_code == 200
    assert response.json()["clip_id"] == clip_ids[1]
    assert response.json()["deleted_at"]

    detail = client.get(f"/api/marketing/reel-groups/{group_id}").json()
    assert [clip["clip_id"] for clip in detail["clips"]] == [clip_ids[0], clip_ids[2]]

    history = client.get("/api/marketing/reel-groups?limit=5").json()
    assert history[0]["clip_count"] == 2


def test_clip_delete_unknown_clip_returns_404():
    client = _client()
    group_id, _ = _create_group(client)

    response = client.patch(f"/api/marketing/reel-groups/{group_id}/clips/missing/deleted")
    assert response.status_code == 404


def test_attempt_cannot_be_created_for_clip_from_another_group():
    client = _client()
    group_a, _ = _create_group(client)
    _, clip_b = _create_group(client)

    response = client.post(
        f"/api/marketing/reel-groups/{group_a}/clip-attempts",
        json={
            "attempt_id": "attempt-cross",
            "clip_id": clip_b,
            "source_job_id": "job-1",
            "source_job_item_index": 0,
            "prompt": "bad",
            "duration_sec": 5,
            "status": "RUNNING",
        },
    )
    assert response.status_code == 404


def test_attempt_id_cannot_be_reused_across_groups():
    client = _client()
    group_a, clip_a = _create_group(client)
    group_b, clip_b = _create_group(client)

    response = client.post(
        f"/api/marketing/reel-groups/{group_a}/clip-attempts",
        json={
            "attempt_id": "attempt-shared",
            "clip_id": clip_a,
            "source_job_id": "job-1",
            "source_job_item_index": 0,
            "prompt": "first",
            "duration_sec": 5,
            "status": "RUNNING",
        },
    )
    assert response.status_code == 200

    response = client.post(
        f"/api/marketing/reel-groups/{group_b}/clip-attempts",
        json={
            "attempt_id": "attempt-shared",
            "clip_id": clip_b,
            "source_job_id": "job-2",
            "source_job_item_index": 0,
            "prompt": "second",
            "duration_sec": 5,
            "status": "RUNNING",
        },
    )
    assert response.status_code == 409

    detail = client.get(f"/api/marketing/reel-groups/{group_a}").json()
    assert detail["clips"][0]["attempts"][0]["prompt"] == "first"


def test_compile_job_id_cannot_be_reused_across_groups():
    client = _client()
    group_a, clip_a = _create_group(client)
    group_b, clip_b = _create_group(client)
    for group_id, clip_id, attempt_id in [
        (group_a, clip_a, "attempt-final-a"),
        (group_b, clip_b, "attempt-final-b"),
    ]:
        assert client.post(
            f"/api/marketing/reel-groups/{group_id}/clip-attempts",
            json={
                "attempt_id": attempt_id,
                "clip_id": clip_id,
                "source_job_id": f"job-{attempt_id}",
                "source_job_item_index": 0,
                "prompt": "final",
                "duration_sec": 5,
                "status": "COMPLETED",
                "source_video_url": f"/outputs/{attempt_id}.mp4",
            },
        ).status_code == 200
        assert client.patch(
            f"/api/marketing/reel-groups/{group_id}/clips/{clip_id}/approval",
            json={"attempt_id": attempt_id},
        ).status_code == 200

    payload = {
        "compile_job_id": "compile-shared",
        "final_video_url": "/outputs/final-a.mp4",
        "compile_payload_summary": {"clips": 1},
    }
    response = client.patch(f"/api/marketing/reel-groups/{group_a}/final", json=payload)
    assert response.status_code == 200

    response = client.patch(
        f"/api/marketing/reel-groups/{group_b}/final",
        json={
            **payload,
            "final_video_url": "/outputs/final-b.mp4",
        },
    )
    assert response.status_code == 409

    detail = client.get(f"/api/marketing/reel-groups/{group_a}").json()
    assert detail["final_video_url"] == "/outputs/final-a.mp4"


def test_final_title_is_returned_in_history_list():
    client = _client()
    group_id, clip_id = _create_group(client)
    assert client.post(
        f"/api/marketing/reel-groups/{group_id}/clip-attempts",
        json={
            "attempt_id": "attempt-title",
            "clip_id": clip_id,
            "source_job_id": "job-title",
            "source_job_item_index": 0,
            "prompt": "final",
            "duration_sec": 5,
            "status": "COMPLETED",
            "source_video_url": "/outputs/title.mp4",
        },
    ).status_code == 200
    assert client.patch(
        f"/api/marketing/reel-groups/{group_id}/clips/{clip_id}/approval",
        json={"attempt_id": "attempt-title"},
    ).status_code == 200
    assert client.patch(
        f"/api/marketing/reel-groups/{group_id}/final",
        json={
            "compile_job_id": "compile-title",
            "final_title": "컬러 팔레트 거실 릴스",
            "final_video_url": "/outputs/final-title.mp4",
            "compile_payload_summary": {"clips": 1},
        },
    ).status_code == 200

    response = client.get("/api/marketing/reel-groups")
    assert response.status_code == 200
    assert response.json()[0]["final_title"] == "컬러 팔레트 거실 릴스"


def test_group_title_can_be_updated_after_final_save():
    client = _client()
    group_id, _ = _create_group(client)

    response = client.patch(
        f"/api/marketing/reel-groups/{group_id}/title",
        json={"final_title": "수정된 히스토리 제목"},
    )

    assert response.status_code == 200
    assert response.json()["final_title"] == "수정된 히스토리 제목"
    detail = client.get(f"/api/marketing/reel-groups/{group_id}").json()
    assert detail["final_title"] == "수정된 히스토리 제목"


def test_update_missing_attempt_returns_404():
    client = _client()
    group_id, _ = _create_group(client)

    response = client.patch(
        f"/api/marketing/reel-groups/{group_id}/clip-attempts/missing",
        json={"status": "COMPLETED"},
    )
    assert response.status_code == 404


def test_deleted_clip_cannot_be_approved():
    client = _client()
    group_id, clip_id = _create_group(client)
    attempt_payload = {
        "attempt_id": "attempt-1",
        "clip_id": clip_id,
        "source_job_id": "job-1",
        "source_job_item_index": 0,
        "prompt": "opening",
        "duration_sec": 5,
        "status": "COMPLETED",
        "source_video_url": "/outputs/a.mp4",
    }
    assert client.post(f"/api/marketing/reel-groups/{group_id}/clip-attempts", json=attempt_payload).status_code == 200
    assert client.patch(f"/api/marketing/reel-groups/{group_id}/clips/{clip_id}/deleted").status_code == 200

    response = client.patch(
        f"/api/marketing/reel-groups/{group_id}/clips/{clip_id}/approval",
        json={"attempt_id": "attempt-1"},
    )
    assert response.status_code == 409


def test_final_patch_missing_group_returns_404():
    client = _client()

    response = client.patch(
        "/api/marketing/reel-groups/missing/final",
        json={
            "compile_job_id": "compile-404",
            "final_video_url": "/outputs/final.mp4",
            "compile_payload_summary": {"clips": 1},
        },
    )
    assert response.status_code == 404


def test_failed_patch_missing_group_returns_404():
    client = _client()

    response = client.patch("/api/marketing/reel-groups/missing/failed")

    assert response.status_code == 404

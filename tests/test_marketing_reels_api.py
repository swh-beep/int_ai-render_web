from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from application.marketing import routes
from application.marketing.db import MarketingDatabaseConfigError
from application.marketing.repository import MarketingReelsRepository, create_sqlite_memory_repository


def _client():
    repo = create_sqlite_memory_repository()
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


def test_group_create_requires_three_to_ten_clips():
    client = _client()

    assert _post_group_with_clip_count(client, 1).status_code == 422
    assert _post_group_with_clip_count(client, 2).status_code == 422
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


def test_ensure_schema_adds_missing_start_end_frame_columns_to_existing_clip_table():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE marketing_video_groups (id VARCHAR(36) PRIMARY KEY)"))
        conn.execute(
            text(
                "CREATE TABLE marketing_video_clips ("
                "id VARCHAR(36) PRIMARY KEY, "
                "group_id VARCHAR(36) NOT NULL, "
                "client_image_id VARCHAR(120) NOT NULL, "
                "source_image_url TEXT NOT NULL, "
                "original_order INTEGER NOT NULL, "
                "current_order INTEGER NOT NULL, "
                "initial_prompt TEXT NOT NULL DEFAULT '', "
                "target_duration_sec INTEGER NOT NULL DEFAULT 5, "
                "approved_attempt_id VARCHAR(120), "
                "deleted_at DATETIME, "
                "created_at DATETIME NOT NULL, "
                "updated_at DATETIME NOT NULL)"
            )
        )

    repo = MarketingReelsRepository(engine)
    repo.ensure_schema()

    with engine.begin() as conn:
        clip_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(marketing_video_clips)")).all()}
        group_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(marketing_video_groups)")).all()}

    assert "end_image_url" in clip_columns
    assert "generation_mode" in clip_columns
    assert "final_title" in group_columns


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

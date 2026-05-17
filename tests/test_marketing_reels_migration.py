from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from application.marketing import routes
from application.marketing.repository import metadata
from scripts.migrate_marketing_reels_clip_schema import migrate_marketing_reels


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _create_legacy_schema(engine):
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE marketing_video_groups (
                    id VARCHAR(36) PRIMARY KEY,
                    status VARCHAR(24) NOT NULL,
                    global_prompt TEXT NOT NULL,
                    platform VARCHAR(64) NOT NULL,
                    tone VARCHAR(64) NOT NULL,
                    goal VARCHAR(255) NOT NULL,
                    final_title VARCHAR(255),
                    final_video_url TEXT,
                    final_download_url TEXT,
                    current_final_attempt_id VARCHAR(36),
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE marketing_video_clips (
                    id VARCHAR(36) PRIMARY KEY,
                    group_id VARCHAR(36) NOT NULL,
                    client_image_id VARCHAR(120) NOT NULL,
                    source_image_url TEXT NOT NULL,
                    end_image_url TEXT,
                    generation_mode VARCHAR(24) NOT NULL,
                    original_order INTEGER NOT NULL,
                    current_order INTEGER NOT NULL,
                    initial_prompt TEXT NOT NULL,
                    target_duration_sec INTEGER NOT NULL,
                    approved_attempt_id VARCHAR(120),
                    deleted_at DATETIME,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE marketing_video_clip_attempts (
                    id VARCHAR(120) PRIMARY KEY,
                    group_id VARCHAR(36) NOT NULL,
                    clip_id VARCHAR(36) NOT NULL,
                    source_job_id VARCHAR(120) NOT NULL,
                    source_job_item_index INTEGER NOT NULL,
                    prompt TEXT NOT NULL,
                    duration_sec INTEGER NOT NULL,
                    status VARCHAR(24) NOT NULL,
                    source_video_url TEXT,
                    download_url TEXT,
                    error TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE marketing_video_final_attempts (
                    id VARCHAR(36) PRIMARY KEY,
                    group_id VARCHAR(36) NOT NULL,
                    compile_job_id VARCHAR(120) NOT NULL,
                    status VARCHAR(24) NOT NULL,
                    final_video_url TEXT NOT NULL,
                    final_download_url TEXT,
                    compile_payload_json TEXT,
                    error TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )


def _seed_legacy_data(engine):
    now = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO marketing_video_groups (
                    id, status, global_prompt, platform, tone, goal, final_title,
                    final_video_url, final_download_url, current_final_attempt_id,
                    created_at, updated_at
                )
                VALUES (
                    'group-1', 'COMPLETED', 'warm showroom', 'Instagram', 'Editorial', 'awareness',
                    'Finished reel', '/final.mp4', '/final-download.mp4', 'final-1',
                    :now, :now
                )
                """
            ),
            {"now": now},
        )
        conn.execute(
            text(
                """
                INSERT INTO marketing_video_clips (
                    id, group_id, client_image_id, source_image_url, end_image_url,
                    generation_mode, original_order, current_order, initial_prompt,
                    target_duration_sec, approved_attempt_id, deleted_at, created_at, updated_at
                )
                VALUES
                    ('clip-1', 'group-1', 'client-1', '/image-1.png', NULL, 'START_ONLY', 1, 1,
                     'opening', 5, 'attempt-1', NULL, :now, :now),
                    ('clip-2', 'group-1', 'client-2', '/image-2.png', '/end-2.png', 'START_AND_END', 2, 2,
                     'closing', 6, 'attempt-2', NULL, :now, :now)
                """
            ),
            {"now": now},
        )
        conn.execute(
            text(
                """
                INSERT INTO marketing_video_clip_attempts (
                    id, group_id, clip_id, source_job_id, source_job_item_index, prompt,
                    duration_sec, status, source_video_url, download_url, error, created_at, updated_at
                )
                VALUES
                    ('attempt-1', 'group-1', 'clip-1', 'job-1', 0, 'opening', 5,
                     'COMPLETED', '/clip-1.mp4', '/clip-1-download.mp4', NULL, :now, :now),
                    ('attempt-2', 'group-1', 'clip-2', 'job-1', 1, 'closing', 6,
                     'COMPLETED', '/clip-2.mp4', '/clip-2-download.mp4', NULL, :now, :now)
                """
            ),
            {"now": now},
        )
        conn.execute(
            text(
                """
                INSERT INTO marketing_video_final_attempts (
                    id, group_id, compile_job_id, status, final_video_url,
                    final_download_url, compile_payload_json, error, created_at, updated_at
                )
                VALUES (
                    'final-1', 'group-1', 'compile-1', 'COMPLETED', '/final.mp4',
                    '/final-download.mp4', '{"legacy": true}', NULL, :now, :now
                )
                """
            ),
            {"now": now},
        )


def _fetch_all(conn, table_name):
    return conn.execute(text(f"SELECT * FROM {table_name}")).mappings().all()


def test_migrates_legacy_marketing_reels_into_clip_schema_idempotently():
    engine = _engine()
    _create_legacy_schema(engine)
    metadata.create_all(engine)
    _seed_legacy_data(engine)

    first_summary = migrate_marketing_reels(engine, apply=True)
    second_summary = migrate_marketing_reels(engine, apply=True)

    assert first_summary.inserted["clip_groups"] == 1
    assert first_summary.inserted["clip_drafts"] == 2
    assert first_summary.inserted["clip_generations"] == 1
    assert first_summary.inserted["clip_generation_drafts"] == 2
    assert first_summary.inserted["clip_attempts"] == 2
    assert first_summary.inserted["clip_compositions"] == 1
    assert first_summary.inserted["clip_composition_attempts"] == 2
    assert all(count == 0 for count in second_summary.inserted.values())

    with engine.connect() as conn:
        groups = _fetch_all(conn, "clip_groups")
        drafts = _fetch_all(conn, "clip_drafts")
        generations = _fetch_all(conn, "clip_generations")
        attempts = _fetch_all(conn, "clip_attempts")
        composition_attempts = conn.execute(
            text(
                """
                SELECT clip_composition_id, clip_attempt_id, composition_order
                FROM clip_composition_attempts
                ORDER BY composition_order
                """
            )
        ).mappings().all()

    assert groups[0]["id"] == "group-1"
    assert groups[0]["aspect_ratio"] == "9:16"
    assert groups[0]["title"] == "Finished reel"
    assert [draft["version"] for draft in drafts] == [1, 1]
    assert [draft["is_active"] for draft in drafts] == [1, 1]
    assert generations[0]["source_job_id"] == "job-1"
    assert generations[0]["generation_type"] == "INITIAL"
    assert {attempt["clip_generation_id"] for attempt in attempts} == {generations[0]["id"]}
    assert attempts[1]["source_image_url_snapshot"] == "/image-2.png"
    assert attempts[1]["end_image_url_snapshot"] == "/end-2.png"
    assert attempts[1]["generation_mode_snapshot"] == "START_AND_END"
    assert [row["clip_attempt_id"] for row in composition_attempts] == ["attempt-1", "attempt-2"]


def test_dev_migration_api_runs_clip_schema_migration(monkeypatch):
    engine = _engine()
    _create_legacy_schema(engine)
    metadata.create_all(engine)
    _seed_legacy_data(engine)
    original_get_engine = routes.get_marketing_engine
    routes.get_marketing_engine = lambda: engine
    monkeypatch.setenv("MARKETING_REELS_MIGRATION_API_ENABLED", "1")
    monkeypatch.setenv("SPRING_PROFILES_ACTIVE", "qa")
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)

    try:
        response = client.post("/api/marketing/dev/migrate-clip-schema?apply=true")
    finally:
        routes.get_marketing_engine = original_get_engine

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "apply"
    assert payload["inserted"]["clip_groups"] == 1
    assert payload["inserted"]["clip_attempts"] == 2
    assert payload["inserted"]["clip_composition_attempts"] == 2

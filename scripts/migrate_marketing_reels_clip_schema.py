#!/usr/bin/env python3
"""Migrate legacy marketing reel tables into the clip schema.

The script is intentionally non-destructive: it only inserts rows missing from
the new tables. Run without --apply to inspect the planned copy counts.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


LEGACY_GROUPS = "marketing_video_groups"
LEGACY_CLIPS = "marketing_video_clips"
LEGACY_ATTEMPTS = "marketing_video_clip_attempts"
LEGACY_FINALS = "marketing_video_final_attempts"

NEW_TABLES = (
    "clip_groups",
    "clip_drafts",
    "clip_generations",
    "clip_generation_drafts",
    "clip_attempts",
    "clip_compositions",
    "clip_composition_attempts",
)

REQUIRED_LEGACY_TABLES = (LEGACY_GROUPS, LEGACY_CLIPS, LEGACY_ATTEMPTS)
GENERATION_NAMESPACE = uuid.UUID("3d1c5d9c-48a8-49a9-9d1a-1e68f129bcb7")
TARGET_COLUMNS = {
    "clip_groups": {
        "id",
        "status",
        "global_prompt",
        "aspect_ratio",
        "title",
        "platform",
        "tone",
        "goal",
        "final_title",
        "final_video_url",
        "final_download_url",
        "current_final_attempt_id",
        "created_at",
        "updated_at",
    },
    "clip_drafts": {
        "id",
        "group_id",
        "client_image_id",
        "source_image_url",
        "end_image_url",
        "generation_mode",
        "original_order",
        "current_order",
        "initial_prompt",
        "target_duration_sec",
        "version",
        "is_active",
        "approved_attempt_id",
        "deleted_at",
        "created_at",
        "updated_at",
    },
    "clip_generations": {
        "id",
        "group_id",
        "generation_type",
        "status",
        "source_job_id",
        "error",
        "created_at",
        "updated_at",
    },
    "clip_generation_drafts": {"clip_generation_id", "clip_draft_id", "request_order"},
    "clip_attempts": {
        "id",
        "group_id",
        "clip_id",
        "clip_generation_id",
        "based_on_draft_version",
        "source_job_id",
        "source_job_item_index",
        "prompt",
        "duration_sec",
        "source_image_url_snapshot",
        "end_image_url_snapshot",
        "generation_mode_snapshot",
        "status",
        "source_video_url",
        "download_url",
        "error",
        "created_at",
        "updated_at",
    },
    "clip_compositions": {
        "id",
        "group_id",
        "compile_job_id",
        "status",
        "title",
        "final_video_url",
        "final_download_url",
        "compile_payload_json",
        "error",
        "created_at",
        "updated_at",
    },
    "clip_composition_attempts": {"clip_composition_id", "clip_attempt_id", "composition_order"},
}


@dataclass
class MigrationSummary:
    planned: dict[str, int] = field(default_factory=lambda: {table: 0 for table in NEW_TABLES})
    inserted: dict[str, int] = field(default_factory=lambda: {table: 0 for table in NEW_TABLES})
    warnings: list[str] = field(default_factory=list)


def _table_columns(bind: Connection | Engine, table_name: str) -> set[str]:
    return {column["name"] for column in inspect(bind).get_columns(table_name)}


def _require_tables(engine: Engine, *, include_finals: bool) -> None:
    inspector = inspect(engine)
    required_legacy = set(REQUIRED_LEGACY_TABLES)
    if include_finals:
        required_legacy.add(LEGACY_FINALS)
    missing_legacy = sorted(table for table in required_legacy if not inspector.has_table(table))
    missing_new = sorted(table for table in NEW_TABLES if not inspector.has_table(table))
    if missing_legacy or missing_new:
        parts = []
        if missing_legacy:
            parts.append(f"legacy tables missing: {', '.join(missing_legacy)}")
        if missing_new:
            parts.append(f"new tables missing: {', '.join(missing_new)}")
        raise RuntimeError("; ".join(parts))

    missing_columns = {
        table: sorted(columns - _table_columns(engine, table))
        for table, columns in TARGET_COLUMNS.items()
        if columns - _table_columns(engine, table)
    }
    if missing_columns:
        details = "; ".join(f"{table}: {', '.join(columns)}" for table, columns in missing_columns.items())
        raise RuntimeError(f"new table columns missing: {details}. Re-run with create_missing=true to normalize schema.")


def _quote_identifier(name: str) -> str:
    return f"`{name}`"


def _rename_column_if_needed(conn: Connection, table_name: str, old_name: str, new_name: str, mysql_definition: str) -> None:
    columns = _table_columns(conn, table_name)
    if new_name in columns or old_name not in columns:
        return
    if conn.dialect.name == "mysql":
        conn.execute(
            text(
                f"ALTER TABLE {_quote_identifier(table_name)} "
                f"CHANGE {_quote_identifier(old_name)} {_quote_identifier(new_name)} {mysql_definition}"
            )
        )
        return
    conn.execute(
        text(
            f"ALTER TABLE {_quote_identifier(table_name)} "
            f"RENAME COLUMN {_quote_identifier(old_name)} TO {_quote_identifier(new_name)}"
        )
    )


def _add_column_if_missing(conn: Connection, table_name: str, column_name: str, definition: str) -> None:
    if column_name in _table_columns(conn, table_name):
        return
    conn.execute(text(f"ALTER TABLE {_quote_identifier(table_name)} ADD COLUMN {_quote_identifier(column_name)} {definition}"))


def _modify_column_if_mysql(conn: Connection, table_name: str, column_name: str, definition: str) -> None:
    if conn.dialect.name != "mysql" or column_name not in _table_columns(conn, table_name):
        return
    conn.execute(text(f"ALTER TABLE {_quote_identifier(table_name)} MODIFY {_quote_identifier(column_name)} {definition}"))


def _mysql_clip_attempt_id_constraints(conn: Connection) -> list[dict[str, Any]]:
    if conn.dialect.name != "mysql":
        return []
    return [
        dict(row)
        for row in conn.execute(
            text(
                """
                SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name, CONSTRAINT_NAME AS constraint_name
                FROM information_schema.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA = DATABASE()
                  AND REFERENCED_TABLE_NAME = 'clip_attempts'
                  AND REFERENCED_COLUMN_NAME = 'id'
                """
            )
        ).mappings()
    ]


def _normalize_clip_attempt_id_width(conn: Connection) -> None:
    if conn.dialect.name != "mysql" or "id" not in _table_columns(conn, "clip_attempts"):
        return
    constraints = _mysql_clip_attempt_id_constraints(conn)
    for constraint in constraints:
        conn.execute(
            text(
                f"ALTER TABLE {_quote_identifier(constraint['table_name'])} "
                f"DROP FOREIGN KEY {_quote_identifier(constraint['constraint_name'])}"
            )
        )

    if "clip_attempt_id" in _table_columns(conn, "clip_composition_attempts"):
        _modify_column_if_mysql(conn, "clip_composition_attempts", "clip_attempt_id", "VARCHAR(120) NOT NULL")
    _modify_column_if_mysql(conn, "clip_attempts", "id", "VARCHAR(120) NOT NULL")

    existing_constraints = {
        constraint["constraint_name"] for constraint in _mysql_clip_attempt_id_constraints(conn)
    }
    for constraint in constraints:
        if constraint["constraint_name"] in existing_constraints:
            continue
        conn.execute(
            text(
                f"ALTER TABLE {_quote_identifier(constraint['table_name'])} "
                f"ADD CONSTRAINT {_quote_identifier(constraint['constraint_name'])} "
                f"FOREIGN KEY ({_quote_identifier(constraint['column_name'])}) REFERENCES clip_attempts(id)"
            )
        )


def _normalize_new_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        _add_column_if_missing(conn, "clip_groups", "platform", "VARCHAR(64) NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "clip_groups", "tone", "VARCHAR(64) NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "clip_groups", "goal", "VARCHAR(255) NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "clip_groups", "final_title", "VARCHAR(255) NULL")
        _add_column_if_missing(conn, "clip_groups", "final_video_url", "TEXT NULL")
        _add_column_if_missing(conn, "clip_groups", "final_download_url", "TEXT NULL")
        _add_column_if_missing(conn, "clip_groups", "current_final_attempt_id", "VARCHAR(36) NULL")

        _rename_column_if_needed(conn, "clip_drafts", "clip_group_id", "group_id", "VARCHAR(36) NOT NULL")
        _rename_column_if_needed(conn, "clip_drafts", "draft_order", "original_order", "INTEGER NOT NULL")
        _rename_column_if_needed(conn, "clip_drafts", "prompt", "initial_prompt", "TEXT NOT NULL")
        _rename_column_if_needed(conn, "clip_drafts", "duration_sec", "target_duration_sec", "INTEGER NOT NULL DEFAULT 5")
        _add_column_if_missing(conn, "clip_drafts", "current_order", "INTEGER NOT NULL DEFAULT 0")
        if {"current_order", "original_order"} <= _table_columns(conn, "clip_drafts"):
            conn.execute(text("UPDATE clip_drafts SET current_order = original_order WHERE current_order = 0"))
        _add_column_if_missing(conn, "clip_drafts", "approved_attempt_id", "VARCHAR(120) NULL")
        _add_column_if_missing(conn, "clip_drafts", "deleted_at", "DATETIME NULL")

        _rename_column_if_needed(conn, "clip_generations", "clip_group_id", "group_id", "VARCHAR(36) NOT NULL")

        _normalize_clip_attempt_id_width(conn)
        _rename_column_if_needed(conn, "clip_attempts", "clip_group_id", "group_id", "VARCHAR(36) NOT NULL")
        _rename_column_if_needed(conn, "clip_attempts", "clip_draft_id", "clip_id", "VARCHAR(36) NOT NULL")
        _rename_column_if_needed(conn, "clip_attempts", "prompt_snapshot", "prompt", "TEXT NOT NULL")
        _rename_column_if_needed(conn, "clip_attempts", "duration_sec_snapshot", "duration_sec", "INTEGER NOT NULL")
        _rename_column_if_needed(conn, "clip_attempts", "video_url", "source_video_url", "TEXT NULL")
        _add_column_if_missing(conn, "clip_attempts", "source_job_id", "VARCHAR(120) NOT NULL DEFAULT ''")

        _rename_column_if_needed(conn, "clip_compositions", "clip_group_id", "group_id", "VARCHAR(36) NOT NULL")
        _rename_column_if_needed(conn, "clip_compositions", "video_url", "final_video_url", "TEXT NOT NULL")
        _rename_column_if_needed(conn, "clip_compositions", "download_url", "final_download_url", "TEXT NULL")


def _select_expr(columns: set[str], column: str, fallback: str = "NULL") -> str:
    if column in columns:
        return column
    return f"{fallback} AS {column}"


def _fetch_rows(conn: Connection, table_name: str, columns: list[str], *, order_by: str = "id") -> list[dict[str, Any]]:
    existing_columns = _table_columns(conn, table_name)
    select_columns = [_select_expr(existing_columns, column) for column in columns]
    return [
        dict(row)
        for row in conn.execute(text(f"SELECT {', '.join(select_columns)} FROM {table_name} ORDER BY {order_by}")).mappings()
    ]


def _exists(conn: Connection, table_name: str, where_clause: str, params: dict[str, Any]) -> bool:
    return bool(conn.execute(text(f"SELECT 1 FROM {table_name} WHERE {where_clause} LIMIT 1"), params).first())


def _insert_if_missing(
    conn: Connection,
    table_name: str,
    key_clause: str,
    params: dict[str, Any],
    summary: MigrationSummary,
    *,
    apply: bool,
) -> None:
    if _exists(conn, table_name, key_clause, params):
        return
    summary.planned[table_name] += 1
    if not apply:
        return

    columns = list(params)
    values = ", ".join(f":{column}" for column in columns)
    conn.execute(text(f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({values})"), params)
    summary.inserted[table_name] += 1


def _generation_id(group_id: str, source_job_id: str) -> str:
    return str(uuid.uuid5(GENERATION_NAMESPACE, f"{group_id}:{source_job_id}"))


def _generation_status(attempts: list[dict[str, Any]]) -> str:
    statuses = {str(attempt["status"] or "").upper() for attempt in attempts}
    if statuses & {"QUEUED", "RUNNING", "PROCESSING"}:
        return "RUNNING"
    if statuses and statuses <= {"FAILED"}:
        return "FAILED"
    return "COMPLETED"


def _migrate_groups(conn: Connection, summary: MigrationSummary, *, apply: bool) -> None:
    rows = _fetch_rows(
        conn,
        LEGACY_GROUPS,
        [
            "id",
            "status",
            "global_prompt",
            "platform",
            "tone",
            "goal",
            "final_title",
            "final_video_url",
            "final_download_url",
            "current_final_attempt_id",
            "created_at",
            "updated_at",
        ],
    )
    for row in rows:
        _insert_if_missing(
            conn,
            "clip_groups",
            "id = :id",
            {
                "id": row["id"],
                "status": row["status"] or "DRAFT",
                "global_prompt": row["global_prompt"] or "",
                "aspect_ratio": "9:16",
                "title": row["final_title"],
                "platform": row["platform"] or "",
                "tone": row["tone"] or "",
                "goal": row["goal"] or "",
                "final_title": row["final_title"],
                "final_video_url": row["final_video_url"],
                "final_download_url": row["final_download_url"],
                "current_final_attempt_id": row["current_final_attempt_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            },
            summary,
            apply=apply,
        )


def _migrate_drafts(conn: Connection, summary: MigrationSummary, *, apply: bool) -> None:
    rows = _fetch_rows(
        conn,
        LEGACY_CLIPS,
        [
            "id",
            "group_id",
            "client_image_id",
            "source_image_url",
            "end_image_url",
            "generation_mode",
            "original_order",
            "current_order",
            "initial_prompt",
            "target_duration_sec",
            "approved_attempt_id",
            "deleted_at",
            "created_at",
            "updated_at",
        ],
        order_by="group_id, current_order, id",
    )
    for row in rows:
        _insert_if_missing(
            conn,
            "clip_drafts",
            "id = :id",
            {
                "id": row["id"],
                "group_id": row["group_id"],
                "client_image_id": row["client_image_id"],
                "source_image_url": row["source_image_url"],
                "end_image_url": row["end_image_url"],
                "generation_mode": row["generation_mode"] or "START_ONLY",
                "original_order": row["original_order"],
                "current_order": row["current_order"],
                "initial_prompt": row["initial_prompt"] or "",
                "target_duration_sec": row["target_duration_sec"] or 5,
                "version": 1,
                "is_active": 0 if row["deleted_at"] else 1,
                "approved_attempt_id": row["approved_attempt_id"],
                "deleted_at": row["deleted_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            },
            summary,
            apply=apply,
        )


def _migrate_generations_and_attempts(conn: Connection, summary: MigrationSummary, *, apply: bool) -> None:
    attempts = _fetch_rows(
        conn,
        LEGACY_ATTEMPTS,
        [
            "id",
            "group_id",
            "clip_id",
            "source_job_id",
            "source_job_item_index",
            "prompt",
            "duration_sec",
            "status",
            "source_video_url",
            "download_url",
            "error",
            "created_at",
            "updated_at",
        ],
        order_by="group_id, source_job_id, source_job_item_index, id",
    )
    drafts_by_id = {
        row["id"]: row
        for row in _fetch_rows(
            conn,
            LEGACY_CLIPS,
            [
                "id",
                "source_image_url",
                "end_image_url",
                "generation_mode",
            ],
        )
    }
    attempts_by_job: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for attempt in attempts:
        attempts_by_job.setdefault((attempt["group_id"], attempt["source_job_id"]), []).append(attempt)

    for (group_id, source_job_id), job_attempts in attempts_by_job.items():
        distinct_clip_ids = {attempt["clip_id"] for attempt in job_attempts}
        generation_id = _generation_id(group_id, source_job_id)
        _insert_if_missing(
            conn,
            "clip_generations",
            "id = :id",
            {
                "id": generation_id,
                "group_id": group_id,
                "generation_type": "INITIAL" if len(distinct_clip_ids) > 1 else "REGENERATE",
                "status": _generation_status(job_attempts),
                "source_job_id": source_job_id,
                "error": None,
                "created_at": min(attempt["created_at"] for attempt in job_attempts),
                "updated_at": max(attempt["updated_at"] for attempt in job_attempts),
            },
            summary,
            apply=apply,
        )

        first_attempt_by_clip: dict[str, dict[str, Any]] = {}
        for attempt in job_attempts:
            first_attempt_by_clip.setdefault(attempt["clip_id"], attempt)
        for clip_id, attempt in first_attempt_by_clip.items():
            _insert_if_missing(
                conn,
                "clip_generation_drafts",
                "clip_generation_id = :clip_generation_id AND clip_draft_id = :clip_draft_id",
                {
                    "clip_generation_id": generation_id,
                    "clip_draft_id": clip_id,
                    "request_order": attempt["source_job_item_index"],
                },
                summary,
                apply=apply,
            )

        for attempt in job_attempts:
            draft = drafts_by_id.get(attempt["clip_id"], {})
            _insert_if_missing(
                conn,
                "clip_attempts",
                "id = :id",
                {
                    "id": attempt["id"],
                    "group_id": attempt["group_id"],
                    "clip_id": attempt["clip_id"],
                    "clip_generation_id": generation_id,
                    "based_on_draft_version": 1,
                    "source_job_id": attempt["source_job_id"],
                    "source_job_item_index": attempt["source_job_item_index"],
                    "prompt": attempt["prompt"] or "",
                    "duration_sec": attempt["duration_sec"] or 5,
                    "source_image_url_snapshot": draft.get("source_image_url"),
                    "end_image_url_snapshot": draft.get("end_image_url"),
                    "generation_mode_snapshot": draft.get("generation_mode") or "START_ONLY",
                    "status": attempt["status"],
                    "source_video_url": attempt["source_video_url"],
                    "download_url": attempt["download_url"],
                    "error": attempt["error"],
                    "created_at": attempt["created_at"],
                    "updated_at": attempt["updated_at"],
                },
                summary,
                apply=apply,
            )


def _migrate_compositions(conn: Connection, summary: MigrationSummary, *, apply: bool) -> None:
    if not inspect(conn).has_table(LEGACY_FINALS):
        summary.warnings.append(f"{LEGACY_FINALS} not found; skipped final composition migration")
        return

    rows = _fetch_rows(
        conn,
        LEGACY_FINALS,
        [
            "id",
            "group_id",
            "compile_job_id",
            "status",
            "title",
            "final_video_url",
            "final_download_url",
            "compile_payload_json",
            "error",
            "created_at",
            "updated_at",
        ],
        order_by="group_id, created_at, id",
    )
    group_titles = {
        row["id"]: row["final_title"]
        for row in _fetch_rows(conn, LEGACY_GROUPS, ["id", "final_title"])
    }
    approved_attempts = conn.execute(
        text(
            f"""
            SELECT group_id, approved_attempt_id, current_order
            FROM {LEGACY_CLIPS}
            WHERE {_approved_attempt_filter(conn)}
            ORDER BY group_id, current_order, id
            """
        )
    ).mappings().all()
    approved_by_group: dict[str, list[dict[str, Any]]] = {}
    for row in approved_attempts:
        approved_by_group.setdefault(row["group_id"], []).append(dict(row))

    for row in rows:
        _insert_if_missing(
            conn,
            "clip_compositions",
            "id = :id",
            {
                "id": row["id"],
                "group_id": row["group_id"],
                "compile_job_id": row["compile_job_id"],
                "status": row["status"] or "COMPLETED",
                "title": row["title"] or group_titles.get(row["group_id"]),
                "final_video_url": row["final_video_url"] or "",
                "final_download_url": row["final_download_url"],
                "compile_payload_json": row["compile_payload_json"],
                "error": row["error"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            },
            summary,
            apply=apply,
        )
        for index, approved in enumerate(approved_by_group.get(row["group_id"], [])):
            _insert_if_missing(
                conn,
                "clip_composition_attempts",
                "clip_composition_id = :clip_composition_id AND clip_attempt_id = :clip_attempt_id",
                {
                    "clip_composition_id": row["id"],
                    "clip_attempt_id": approved["approved_attempt_id"],
                    "composition_order": index,
                },
                summary,
                apply=apply,
            )


def _approved_attempt_filter(conn: Connection) -> str:
    clip_columns = _table_columns(conn, LEGACY_CLIPS)
    if "approved_attempt_id" not in clip_columns:
        return "1 = 0"
    if "deleted_at" in clip_columns:
        return "approved_attempt_id IS NOT NULL AND deleted_at IS NULL"
    return "approved_attempt_id IS NOT NULL"


def migrate_marketing_reels(engine: Engine, *, apply: bool = False, create_missing: bool = False) -> MigrationSummary:
    if create_missing:
        from application.marketing.repository import metadata

        metadata.create_all(engine)
        _normalize_new_schema(engine)
    _require_tables(engine, include_finals=False)

    summary = MigrationSummary()
    with engine.begin() as conn:
        _migrate_groups(conn, summary, apply=apply)
        _migrate_drafts(conn, summary, apply=apply)
        _migrate_generations_and_attempts(conn, summary, apply=apply)
        _migrate_compositions(conn, summary, apply=apply)
    return summary


def _build_engine(database_url: str | None) -> Engine:
    if database_url:
        return create_engine(database_url, pool_pre_ping=True, future=True)
    from application.marketing.db import get_marketing_engine

    return get_marketing_engine()


def _print_summary(summary: MigrationSummary, *, apply: bool) -> None:
    mode = "applied" if apply else "dry-run"
    print(f"Marketing reels migration {mode} summary")
    for table in NEW_TABLES:
        print(f"- {table}: planned={summary.planned[table]} inserted={summary.inserted[table]}")
    for warning in summary.warnings:
        print(f"WARNING: {warning}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate marketing_video_* rows into the clip_* schema.")
    parser.add_argument("--database-url", help="Override MARKETING_REELS_DATABASE_URL/DATABASE_URL for this run.")
    parser.add_argument("--apply", action="store_true", help="Insert rows. Omit for a dry run.")
    parser.add_argument(
        "--create-missing",
        action="store_true",
        help="Create missing new clip_* tables before migrating. Legacy tables are never created or dropped.",
    )
    args = parser.parse_args()

    engine = _build_engine(args.database_url)
    summary = migrate_marketing_reels(engine, apply=args.apply, create_missing=args.create_missing)
    _print_summary(summary, apply=args.apply)


if __name__ == "__main__":
    main()

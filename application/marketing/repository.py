import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    create_engine,
    desc,
    insert,
    func,
    select,
    exists,
    update,
    inspect,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from application.marketing.schemas import (
    MarketingClipApprovalPayload,
    MarketingClipAttemptPayload,
    MarketingClipAttemptUpdate,
    MarketingClipSourceImagesUpdate,
    MarketingFinalResultPayload,
    MarketingReelGroupCreate,
)


metadata = MetaData()

marketing_video_groups = Table(
    "marketing_video_groups",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("status", String(24), nullable=False, default="DRAFT"),
    Column("global_prompt", Text, nullable=False, default=""),
    Column("platform", String(64), nullable=False, default=""),
    Column("tone", String(64), nullable=False, default=""),
    Column("goal", String(255), nullable=False, default=""),
    Column("final_title", String(255)),
    Column("final_video_url", Text),
    Column("final_download_url", Text),
    Column("current_final_attempt_id", String(36)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

marketing_video_clips = Table(
    "marketing_video_clips",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("group_id", String(36), ForeignKey("marketing_video_groups.id"), nullable=False, index=True),
    Column("client_image_id", String(120), nullable=False),
    Column("source_image_url", Text, nullable=False),
    Column("end_image_url", Text),
    Column("generation_mode", String(24), nullable=False, default="START_ONLY"),
    Column("original_order", Integer, nullable=False),
    Column("current_order", Integer, nullable=False),
    Column("initial_prompt", Text, nullable=False, default=""),
    Column("target_duration_sec", Integer, nullable=False, default=5),
    Column("approved_attempt_id", String(120)),
    Column("deleted_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

marketing_video_clip_attempts = Table(
    "marketing_video_clip_attempts",
    metadata,
    Column("id", String(120), primary_key=True),
    Column("group_id", String(36), ForeignKey("marketing_video_groups.id"), nullable=False, index=True),
    Column("clip_id", String(36), ForeignKey("marketing_video_clips.id"), nullable=False, index=True),
    Column("source_job_id", String(120), nullable=False),
    Column("source_job_item_index", Integer, nullable=False),
    Column("prompt", Text, nullable=False, default=""),
    Column("duration_sec", Integer, nullable=False, default=5),
    Column("status", String(24), nullable=False),
    Column("source_video_url", Text),
    Column("download_url", Text),
    Column("error", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

marketing_video_final_attempts = Table(
    "marketing_video_final_attempts",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("group_id", String(36), ForeignKey("marketing_video_groups.id"), nullable=False, index=True),
    Column("compile_job_id", String(120), nullable=False, unique=True),
    Column("status", String(24), nullable=False, default="COMPLETED"),
    Column("final_video_url", Text, nullable=False),
    Column("final_download_url", Text),
    Column("compile_payload_json", Text),
    Column("error", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class MarketingReelsRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def ensure_schema(self) -> None:
        metadata.create_all(self.engine)
        self._ensure_clip_frame_columns()
        self._ensure_group_final_title_column()

    def _ensure_clip_frame_columns(self) -> None:
        inspector = inspect(self.engine)
        if not inspector.has_table(marketing_video_clips.name):
            return

        existing_columns = {column["name"] for column in inspector.get_columns(marketing_video_clips.name)}
        statements = []
        if "end_image_url" not in existing_columns:
            statements.append("ALTER TABLE marketing_video_clips ADD COLUMN end_image_url TEXT NULL")
        if "generation_mode" not in existing_columns:
            statements.append(
                "ALTER TABLE marketing_video_clips "
                "ADD COLUMN generation_mode VARCHAR(24) NOT NULL DEFAULT 'START_ONLY'"
            )
        if not statements:
            return

        with self.engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))

    def _ensure_group_final_title_column(self) -> None:
        inspector = inspect(self.engine)
        if not inspector.has_table(marketing_video_groups.name):
            return

        existing_columns = {column["name"] for column in inspector.get_columns(marketing_video_groups.name)}
        if "final_title" in existing_columns:
            return

        with self.engine.begin() as conn:
            conn.execute(text("ALTER TABLE marketing_video_groups ADD COLUMN final_title VARCHAR(255) NULL"))

    def _require_group(self, conn, group_id: str) -> None:
        group = conn.execute(
            select(marketing_video_groups.c.id).where(marketing_video_groups.c.id == group_id)
        ).first()
        if not group:
            raise KeyError("Group not found")

    def _require_clip(self, conn, group_id: str, clip_id: str, *, include_deleted: bool = False) -> Any:
        query = select(marketing_video_clips.c.id, marketing_video_clips.c.deleted_at).where(
            and_(marketing_video_clips.c.id == clip_id, marketing_video_clips.c.group_id == group_id)
        )
        clip = conn.execute(query).mappings().first()
        if not clip:
            raise KeyError("Clip not found")
        if clip["deleted_at"] and not include_deleted:
            raise ValueError("Deleted clips cannot be modified")
        return clip

    def _require_attempt(self, conn, group_id: str, attempt_id: str, *, clip_id: str | None = None) -> Any:
        conditions = [
            marketing_video_clip_attempts.c.id == attempt_id,
            marketing_video_clip_attempts.c.group_id == group_id,
        ]
        if clip_id is not None:
            conditions.append(marketing_video_clip_attempts.c.clip_id == clip_id)
        attempt = conn.execute(
            select(marketing_video_clip_attempts).where(and_(*conditions))
        ).mappings().first()
        if not attempt:
            raise KeyError("Attempt not found")
        return attempt

    def create_group(self, payload: MarketingReelGroupCreate) -> dict[str, Any]:
        now = utcnow()
        group_id = new_id()
        clip_rows = []
        with self.engine.begin() as conn:
            conn.execute(
                insert(marketing_video_groups).values(
                    id=group_id,
                    status="DRAFT",
                    global_prompt=payload.global_prompt,
                    platform=payload.platform,
                    tone=payload.tone,
                    goal=payload.goal,
                    created_at=now,
                    updated_at=now,
                )
            )
            for clip in payload.clips:
                clip_id = new_id()
                clip_rows.append({"clip_id": clip_id, "client_image_id": clip.client_image_id})
                conn.execute(
                    insert(marketing_video_clips).values(
                        id=clip_id,
                        group_id=group_id,
                        client_image_id=clip.client_image_id,
                        source_image_url=clip.source_image_url,
                        end_image_url=clip.end_image_url,
                        generation_mode=clip.generation_mode,
                        original_order=clip.order,
                        current_order=clip.order,
                        initial_prompt=clip.prompt,
                        target_duration_sec=clip.duration_sec,
                        created_at=now,
                        updated_at=now,
                    )
                )
        return {"group_id": group_id, "clips": clip_rows}

    def mark_group_failed(self, group_id: str) -> None:
        with self.engine.begin() as conn:
            self._require_group(conn, group_id)
            conn.execute(
                update(marketing_video_groups)
                .where(marketing_video_groups.c.id == group_id)
                .values(status="FAILED", updated_at=utcnow())
            )

    def update_clip_source_images(self, group_id: str, payload: MarketingClipSourceImagesUpdate) -> dict[str, Any]:
        now = utcnow()
        with self.engine.begin() as conn:
            self._require_group(conn, group_id)
            updated_clips = []
            for clip in payload.clips:
                self._require_clip(conn, group_id, clip.clip_id)
                result = conn.execute(
                    update(marketing_video_clips)
                    .where(and_(marketing_video_clips.c.id == clip.clip_id, marketing_video_clips.c.group_id == group_id))
                    .values(
                        source_image_url=clip.source_image_url,
                        end_image_url=clip.end_image_url,
                        generation_mode=clip.generation_mode,
                        updated_at=now,
                    )
                )
                if result.rowcount == 0:
                    raise KeyError("Clip not found")
                updated_clips.append(
                    {
                        "clip_id": clip.clip_id,
                        "source_image_url": clip.source_image_url,
                        "end_image_url": clip.end_image_url,
                        "generation_mode": clip.generation_mode,
                    }
                )
            conn.execute(
                update(marketing_video_groups)
                .where(marketing_video_groups.c.id == group_id)
                .values(status="GENERATING", updated_at=now)
            )
        return {"group_id": group_id, "clips": updated_clips}

    def upsert_clip_attempt(self, group_id: str, payload: MarketingClipAttemptPayload) -> dict[str, Any]:
        now = utcnow()
        values = {
            "id": payload.attempt_id,
            "group_id": group_id,
            "clip_id": payload.clip_id,
            "source_job_id": payload.source_job_id,
            "source_job_item_index": payload.source_job_item_index,
            "prompt": payload.prompt,
            "duration_sec": payload.duration_sec,
            "status": payload.status,
            "source_video_url": payload.source_video_url,
            "download_url": payload.download_url,
            "error": payload.error,
            "created_at": now,
            "updated_at": now,
        }
        with self.engine.begin() as conn:
            self._require_group(conn, group_id)
            self._require_clip(conn, group_id, payload.clip_id)
            existing = conn.execute(
                select(
                    marketing_video_clip_attempts.c.id,
                    marketing_video_clip_attempts.c.group_id,
                    marketing_video_clip_attempts.c.clip_id,
                ).where(marketing_video_clip_attempts.c.id == payload.attempt_id)
            ).mappings().first()
            if existing:
                if existing["group_id"] != group_id or existing["clip_id"] != payload.clip_id:
                    raise ValueError("Attempt id already belongs to another clip")
                conn.execute(
                    update(marketing_video_clip_attempts)
                    .where(marketing_video_clip_attempts.c.id == payload.attempt_id)
                    .values(**{key: value for key, value in values.items() if key != "created_at"})
                )
            else:
                conn.execute(insert(marketing_video_clip_attempts).values(**values))
        return self.get_attempt(payload.attempt_id)

    def update_clip_attempt(self, group_id: str, attempt_id: str, payload: MarketingClipAttemptUpdate) -> dict[str, Any]:
        values = payload.model_dump(exclude_unset=True)
        values["updated_at"] = utcnow()
        with self.engine.begin() as conn:
            existing = self._require_attempt(conn, group_id, attempt_id)
            next_status = values.get("status", existing["status"])
            next_source_video_url = values.get("source_video_url", existing["source_video_url"])
            if next_status == "COMPLETED" and not (next_source_video_url or "").strip():
                raise ValueError("COMPLETED attempts require source_video_url")
            result = conn.execute(
                update(marketing_video_clip_attempts)
                .where(
                    and_(
                        marketing_video_clip_attempts.c.id == attempt_id,
                        marketing_video_clip_attempts.c.group_id == group_id,
                    )
                )
                .values(**values)
            )
            if result.rowcount == 0:
                raise KeyError("Attempt not found")
        return self.get_attempt(attempt_id, group_id=group_id)

    def approve_clip_attempt(self, group_id: str, clip_id: str, payload: MarketingClipApprovalPayload) -> dict[str, Any]:
        with self.engine.begin() as conn:
            self._require_clip(conn, group_id, clip_id)
            attempt = self._require_attempt(conn, group_id, payload.attempt_id, clip_id=clip_id)
            if attempt["status"] != "COMPLETED":
                raise ValueError("Only completed attempts can be approved")
            if not attempt["source_video_url"]:
                raise ValueError("Completed attempts require source_video_url before approval")
            result = conn.execute(
                update(marketing_video_clips)
                .where(and_(marketing_video_clips.c.id == clip_id, marketing_video_clips.c.group_id == group_id))
                .values(approved_attempt_id=payload.attempt_id, updated_at=utcnow())
            )
            if result.rowcount == 0:
                raise KeyError("Clip not found")
            conn.execute(
                update(marketing_video_groups)
                .where(marketing_video_groups.c.id == group_id)
                .values(status="REVIEWING", updated_at=utcnow())
            )
        return {"group_id": group_id, "clip_id": clip_id, "approved_attempt_id": payload.attempt_id}

    def mark_clip_deleted(self, group_id: str, clip_id: str) -> dict[str, Any]:
        now = utcnow()
        with self.engine.begin() as conn:
            self._require_group(conn, group_id)
            self._require_clip(conn, group_id, clip_id)
            result = conn.execute(
                update(marketing_video_clips)
                .where(and_(marketing_video_clips.c.id == clip_id, marketing_video_clips.c.group_id == group_id))
                .values(deleted_at=now, approved_attempt_id=None, updated_at=now)
            )
            if result.rowcount == 0:
                raise KeyError("Clip not found")
            conn.execute(
                update(marketing_video_groups)
                .where(marketing_video_groups.c.id == group_id)
                .values(status="REVIEWING", updated_at=now)
            )
        return {"group_id": group_id, "clip_id": clip_id, "deleted_at": _iso(now)}

    def patch_final(self, group_id: str, payload: MarketingFinalResultPayload) -> dict[str, str]:
        now = utcnow()
        with self.engine.begin() as conn:
            self._require_group(conn, group_id)
            approved_source_exists = conn.execute(
                select(marketing_video_clips.c.id)
                .select_from(
                    marketing_video_clips.join(
                        marketing_video_clip_attempts,
                        and_(
                            marketing_video_clip_attempts.c.id == marketing_video_clips.c.approved_attempt_id,
                            marketing_video_clip_attempts.c.clip_id == marketing_video_clips.c.id,
                            marketing_video_clip_attempts.c.group_id == group_id,
                        ),
                    )
                )
                .where(
                    and_(
                        marketing_video_clips.c.group_id == group_id,
                        marketing_video_clips.c.deleted_at.is_(None),
                        marketing_video_clip_attempts.c.status == "COMPLETED",
                        marketing_video_clip_attempts.c.source_video_url.is_not(None),
                        marketing_video_clip_attempts.c.source_video_url != "",
                    )
                )
                .limit(1)
            ).first()
            if not approved_source_exists:
                raise ValueError("Final results require at least one approved source clip")
            existing = conn.execute(
                select(marketing_video_final_attempts.c.id, marketing_video_final_attempts.c.group_id).where(
                    marketing_video_final_attempts.c.compile_job_id == payload.compile_job_id
                )
            ).mappings().first()
            if existing and existing["group_id"] != group_id:
                raise ValueError("Compile job id already belongs to another group")
            final_id = existing["id"] if existing else new_id()
            final_values = {
                "id": final_id,
                "group_id": group_id,
                "compile_job_id": payload.compile_job_id,
                "status": "COMPLETED",
                "final_video_url": payload.final_video_url,
                "final_download_url": payload.final_download_url,
                "compile_payload_json": json.dumps(payload.compile_payload_summary, ensure_ascii=False),
                "updated_at": now,
            }
            final_title = (payload.final_title or "").strip() or None
            if existing:
                conn.execute(
                    update(marketing_video_final_attempts)
                    .where(marketing_video_final_attempts.c.id == final_id)
                    .values(**{key: value for key, value in final_values.items() if key != "id"})
                )
            else:
                conn.execute(insert(marketing_video_final_attempts).values(**final_values, created_at=now))
            conn.execute(
                update(marketing_video_groups)
                .where(marketing_video_groups.c.id == group_id)
                .values(
                    status="COMPLETED",
                    final_title=final_title,
                    final_video_url=payload.final_video_url,
                    final_download_url=payload.final_download_url,
                    current_final_attempt_id=final_id,
                    updated_at=now,
                )
            )
        return {"group_id": group_id}

    def update_group_title(self, group_id: str, final_title: str) -> dict[str, str]:
        title = final_title.strip()
        if not title:
            raise ValueError("final_title is required")
        with self.engine.begin() as conn:
            self._require_group(conn, group_id)
            conn.execute(
                update(marketing_video_groups)
                .where(marketing_video_groups.c.id == group_id)
                .values(final_title=title, updated_at=utcnow())
            )
        return {"group_id": group_id, "final_title": title}

    def list_groups(self, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(100, int(limit or 20)))
        with self.engine.begin() as conn:
            has_completed_source_video = exists(
                select(marketing_video_clip_attempts.c.id).where(
                    and_(
                        marketing_video_clip_attempts.c.group_id == marketing_video_groups.c.id,
                        marketing_video_clip_attempts.c.status == "COMPLETED",
                        marketing_video_clip_attempts.c.source_video_url.is_not(None),
                        marketing_video_clip_attempts.c.source_video_url != "",
                    )
                )
            )
            rows = conn.execute(
                select(
                    marketing_video_groups.c.id,
                    marketing_video_groups.c.status,
                    marketing_video_groups.c.created_at,
                    marketing_video_groups.c.final_title,
                    marketing_video_groups.c.final_video_url,
                    marketing_video_clips.c.source_image_url,
                )
                .select_from(
                    marketing_video_groups.outerjoin(
                        marketing_video_clips,
                        and_(
                            marketing_video_groups.c.id == marketing_video_clips.c.group_id,
                            marketing_video_clips.c.current_order == 1,
                            marketing_video_clips.c.deleted_at.is_(None),
                        ),
                    )
                )
                .where(marketing_video_groups.c.status.in_(["GENERATING", "REVIEWING", "COMPILING", "COMPLETED"]))
                .where(
                    (marketing_video_groups.c.final_video_url.is_not(None)) |
                    has_completed_source_video
                )
                .order_by(desc(marketing_video_groups.c.created_at))
                .limit(safe_limit)
            ).mappings().all()
            result = []
            for row in rows:
                clip_count = conn.execute(
                    select(func.count(marketing_video_clips.c.id)).where(
                        and_(
                            marketing_video_clips.c.group_id == row["id"],
                            marketing_video_clips.c.deleted_at.is_(None),
                        )
                    )
                ).scalar_one()
                result.append(
                    {
                        "group_id": row["id"],
                        "status": row["status"],
                        "created_at": _iso(row["created_at"]),
                        "final_title": row["final_title"],
                        "final_video_url": row["final_video_url"],
                        "representative_image_url": row["source_image_url"],
                        "clip_count": clip_count,
                    }
                )
            return result

    def get_group_detail(self, group_id: str) -> dict[str, Any]:
        with self.engine.begin() as conn:
            group = conn.execute(
                select(marketing_video_groups).where(marketing_video_groups.c.id == group_id)
            ).mappings().first()
            if not group:
                raise KeyError("Group not found")
            clips = conn.execute(
                select(marketing_video_clips)
                .where(and_(marketing_video_clips.c.group_id == group_id, marketing_video_clips.c.deleted_at.is_(None)))
                .order_by(marketing_video_clips.c.current_order)
            ).mappings().all()
            attempts = conn.execute(
                select(marketing_video_clip_attempts)
                .where(marketing_video_clip_attempts.c.group_id == group_id)
                .order_by(marketing_video_clip_attempts.c.created_at)
            ).mappings().all()
        attempts_by_clip: dict[str, list[dict[str, Any]]] = {}
        for attempt in attempts:
            attempts_by_clip.setdefault(attempt["clip_id"], []).append(_attempt_to_dict(attempt))
        return {
            "group_id": group["id"],
            "status": group["status"],
            "created_at": _iso(group["created_at"]),
            "updated_at": _iso(group["updated_at"]),
            "final_video_url": group["final_video_url"],
            "final_download_url": group["final_download_url"],
            "final_title": group["final_title"],
            "global_prompt": group["global_prompt"],
            "platform": group["platform"],
            "tone": group["tone"],
            "goal": group["goal"],
            "clips": [_clip_to_dict(clip, attempts_by_clip.get(clip["id"], [])) for clip in clips],
        }

    def get_attempt(self, attempt_id: str, *, group_id: str | None = None) -> dict[str, Any]:
        with self.engine.begin() as conn:
            conditions = [marketing_video_clip_attempts.c.id == attempt_id]
            if group_id is not None:
                conditions.append(marketing_video_clip_attempts.c.group_id == group_id)
            attempt = conn.execute(
                select(marketing_video_clip_attempts).where(and_(*conditions))
            ).mappings().first()
        if not attempt:
            raise KeyError("Attempt not found")
        return _attempt_to_dict(attempt)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _attempt_to_dict(row: Any) -> dict[str, Any]:
    return {
        "attempt_id": row["id"],
        "clip_id": row["clip_id"],
        "source_job_id": row["source_job_id"],
        "source_job_item_index": row["source_job_item_index"],
        "prompt": row["prompt"],
        "duration_sec": row["duration_sec"],
        "status": row["status"],
        "source_video_url": row["source_video_url"],
        "download_url": row["download_url"],
        "error": row["error"],
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


def _clip_to_dict(row: Any, attempts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "clip_id": row["id"],
        "client_image_id": row["client_image_id"],
        "source_image_url": row["source_image_url"],
        "end_image_url": row["end_image_url"],
        "generation_mode": row["generation_mode"],
        "original_order": row["original_order"],
        "current_order": row["current_order"],
        "initial_prompt": row["initial_prompt"],
        "target_duration_sec": row["target_duration_sec"],
        "approved_attempt_id": row["approved_attempt_id"],
        "deleted_at": _iso(row["deleted_at"]),
        "attempts": attempts,
    }


def create_sqlite_memory_repository() -> MarketingReelsRepository:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    repo = MarketingReelsRepository(engine)
    repo.ensure_schema()
    return repo

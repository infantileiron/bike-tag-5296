from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.constants.enums import RunStatus
from src.domain.ledger import DEFAULT_REVISION
from src.models.base import Base, utc_now


class Observation(Base):
    __tablename__ = 'observations'
    __table_args__ = (Index('ix_observations_district_batch', 'district', 'batch'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(String(96), unique=True)
    district: Mapped[str] = mapped_column(String(300), default='')
    batch: Mapped[str] = mapped_column(String(300), default='')
    revision: Mapped[int] = mapped_column(Integer, default=DEFAULT_REVISION)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class AnalysisRun(Base):
    __tablename__ = 'analysis_runs'
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    owner: Mapped[str] = mapped_column(String(60))
    parameters: Mapped[dict] = mapped_column(JSON)
    last_observation_id: Mapped[int] = mapped_column(Integer)
    sample_size: Mapped[int] = mapped_column(Integer)
    observation_ids: Mapped[list] = mapped_column(JSON)
    # 发起时固化的那一版认账台账：更正入账后旧分析仍按旧版计算
    ledger_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.QUEUED, index=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Review(Base):
    __tablename__ = 'reviews'
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('analysis_runs.id'), unique=True)
    decision: Mapped[str] = mapped_column(String(16))
    note: Mapped[str] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Schedule(Base):
    __tablename__ = 'schedules'
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('analysis_runs.id'), index=True)
    recipient: Mapped[str] = mapped_column(String(100))
    interval_minutes: Mapped[int] = mapped_column(Integer)
    next_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Delivery(Base):
    __tablename__ = 'deliveries'
    __table_args__ = (UniqueConstraint('schedule_id', 'scheduled_at'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey('schedules.id'))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime)
    filename: Mapped[str] = mapped_column(String(120))
    delivered_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class AuditEvent(Base):
    __tablename__ = 'audit_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(60))
    detail: Mapped[str] = mapped_column(String(200))
    at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

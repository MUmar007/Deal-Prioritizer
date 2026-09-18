import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class BuyBoxRun(Base):
    """One search. The headcount band is saved for later enrichment, not scored."""

    __tablename__ = "buybox_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    vertical: Mapped[str] = mapped_column(String(100), index=True)
    market: Mapped[str] = mapped_column(String(120), index=True)
    headcount_min: Mapped[int] = mapped_column(default=5)
    headcount_max: Mapped[int] = mapped_column(default=50)
    source_status: Mapped[str] = mapped_column(
        String(20), default="live", server_default="live"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    targets: Mapped[list["TargetCompany"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="TargetCompany.fit_score.desc()",
    )


class TargetCompany(Base):
    __tablename__ = "target_companies"
    __table_args__ = (UniqueConstraint("run_id", "dedupe_hash", name="uq_run_dedupe"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("buybox_runs.id", ondelete="CASCADE"), index=True
    )
    dedupe_hash: Mapped[str] = mapped_column(String(200))
    legal_name: Mapped[str] = mapped_column(String(260))
    street_line: Mapped[str | None] = mapped_column(String(400))
    locality: Mapped[str | None] = mapped_column(String(100))
    region: Mapped[str | None] = mapped_column(String(40))
    main_phone: Mapped[str | None] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(254))
    web_url: Mapped[str | None] = mapped_column(String(500))
    fit_score: Mapped[float] = mapped_column(default=0.0)
    tier: Mapped[str] = mapped_column(String(20), default="watch")
    rationale: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    run: Mapped[BuyBoxRun] = relationship(back_populates="targets")

    @property
    def skipped(self) -> bool:
        """Rejected outright by the ranker (currently only chains)."""
        return bool(self.meta.get("skip"))

    @property
    def source(self) -> str:
        """Either "osm" or "sample" (placeholder rows used when Overpass is down)."""
        return self.meta.get("origin", "osm")

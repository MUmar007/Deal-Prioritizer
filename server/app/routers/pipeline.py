import csv
import io
import re
import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.models import BuyBoxRun, TargetCompany
from app.schemas import RunCreate, RunOut
from app.services.rank_engine import (
    CompanyCandidate,
    dedupe_hash,
    meta_payload,
    rank_candidate,
)
from app.services.source_fetcher import MarketNotFoundError, pull_from_osm

router = APIRouter(prefix="/v1/pipeline", tags=["pipeline"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

CSV_COLUMNS = [
    "fit_score",
    "tier",
    "company",
    "phone",
    "email",
    "website",
    "address",
    "city",
    "state",
    "notes",
    "vertical",
    "market",
]


def _build_targets(candidates: list[CompanyCandidate]) -> list[TargetCompany]:
    seen: set[str] = set()
    rows: list[TargetCompany] = []
    for cand in candidates:
        dhash = dedupe_hash(cand.legal_name, cand.locality)
        if dhash in seen:
            continue
        seen.add(dhash)
        ranked = rank_candidate(cand)
        rows.append(
            TargetCompany(
                dedupe_hash=dhash,
                legal_name=cand.legal_name,
                street_line=cand.street_line,
                locality=cand.locality,
                region=cand.region,
                main_phone=cand.main_phone,
                email=cand.email,
                web_url=cand.web_url,
                fit_score=ranked.fit_score,
                tier=ranked.tier,
                rationale=ranked.rationale,
                meta=meta_payload(ranked),
            )
        )
    rows.sort(key=lambda t: t.fit_score, reverse=True)
    return rows


async def _get_run_or_404(session: AsyncSession, run_id: uuid.UUID) -> BuyBoxRun:
    run = await session.get(
        BuyBoxRun, run_id, options=[selectinload(BuyBoxRun.targets)]
    )
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return run


@router.post("/runs", status_code=status.HTTP_201_CREATED)
async def create_run(payload: RunCreate, session: SessionDep) -> RunOut:
    """Find businesses for the vertical and market, rank them and save the run."""
    try:
        discovery = await pull_from_osm(payload.vertical, payload.market, payload.limit)
    except MarketNotFoundError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Couldn't find the market '{payload.market}'. Try 'City, ST'.",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Market lookup is unavailable, try again shortly",
        ) from exc

    run = BuyBoxRun(
        vertical=payload.vertical,
        market=payload.market,
        headcount_min=payload.headcount_min,
        headcount_max=payload.headcount_max,
        source_status=discovery.source_status,
    )
    run.targets = _build_targets(discovery.candidates)
    session.add(run)
    await session.commit()
    return RunOut.model_validate(run)


@router.get("/runs/{run_id}")
async def fetch_run(run_id: uuid.UUID, session: SessionDep) -> RunOut:
    """Get a saved run. Targets are sorted by score, highest first."""
    return RunOut.model_validate(await _get_run_or_404(session, run_id))


@router.get("/runs/{run_id}/export")
async def export_run(
    run_id: uuid.UUID,
    session: SessionDep,
    min_score: Annotated[float, Query(ge=0, le=100)] = 0,
    include_rejects: bool = False,
) -> Response:
    """Export a run as CSV using the same filters as the UI.

    `min_score` applies to ranked companies and `include_rejects` adds the
    chains back in. Sample rows are always left out.
    """
    run = await _get_run_or_404(session, run_id)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for t in run.targets:
        if t.source == "sample":
            continue
        if t.skipped:
            if not include_rejects:
                continue
        elif t.fit_score < min_score:
            continue
        writer.writerow(
            [
                t.fit_score,
                t.tier,
                t.legal_name,
                t.main_phone or "",
                t.email or "",
                t.web_url or "",
                t.street_line or "",
                t.locality or "",
                t.region or "",
                t.rationale,
                run.vertical,
                run.market,
            ]
        )

    slug = re.sub(r"[^a-z0-9]+", "-", run.vertical.lower()).strip("-")[:12]
    filename = f"deal-prioritizer-{slug}-{str(run.id)[:8]}.csv"
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

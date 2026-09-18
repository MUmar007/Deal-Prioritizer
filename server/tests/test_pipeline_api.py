import csv
import io

import httpx
import pytest

from app.routers import pipeline
from app.services.rank_engine import CompanyCandidate
from app.services.source_fetcher import (
    DiscoveryResult,
    MarketNotFoundError,
    sample_companies,
)

pytestmark = pytest.mark.anyio

RUN_BODY = {"vertical": "Plumbing", "market": "Austin, TX"}

LIVE = [
    CompanyCandidate(
        legal_name="Summit Plumbing & Sons",
        street_line="1 Main St",
        locality="Austin",
        main_phone="512-555-0101",
        email="office@summit.test",
        has_opening_hours=True,
        founded_year=1998,
    ),
    CompanyCandidate(
        legal_name="Harbor Plumbing",
        street_line="2 Main St",
        locality="Austin",
        main_phone="512-555-0102",
    ),
    CompanyCandidate(legal_name="Lone Star Plumbing", locality="Austin"),
    CompanyCandidate(legal_name="Roto-Rooter", locality="Austin", brand="Roto-Rooter"),
]


def use_source(monkeypatch, result: DiscoveryResult) -> None:
    async def fake_pull(vertical: str, market: str, cap: int) -> DiscoveryResult:
        return result

    monkeypatch.setattr(pipeline, "pull_from_osm", fake_pull)


def csv_rows(res: httpx.Response) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(res.text)))


async def test_create_run_ranks_and_persists(client, monkeypatch):
    use_source(monkeypatch, DiscoveryResult(LIVE, "live"))
    res = await client.post("/v1/pipeline/runs", json=RUN_BODY)
    assert res.status_code == 201
    run = res.json()

    assert run["source_status"] == "live"
    assert {t["source"] for t in run["targets"]} == {"osm"}
    scores = [t["fit_score"] for t in run["targets"]]
    assert scores == sorted(scores, reverse=True)
    assert run["created_at"].endswith("Z")

    chain = next(t for t in run["targets"] if t["legal_name"] == "Roto-Rooter")
    assert chain["tier"] == "reject"
    assert chain["skipped"] is True

    fetched = await client.get(f"/v1/pipeline/runs/{run['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == run


async def test_duplicate_candidates_are_collapsed(client, monkeypatch):
    dupes = [
        CompanyCandidate(legal_name="Acme Plumbing", locality="Austin"),
        CompanyCandidate(legal_name="ACME plumbing.", locality="austin"),
    ]
    use_source(monkeypatch, DiscoveryResult(dupes, "live"))
    res = await client.post("/v1/pipeline/runs", json=RUN_BODY)
    assert res.status_code == 201
    assert len(res.json()["targets"]) == 1


async def test_export_filters_by_min_score(client, monkeypatch):
    use_source(monkeypatch, DiscoveryResult(LIVE, "live"))
    run = (await client.post("/v1/pipeline/runs", json=RUN_BODY)).json()

    res = await client.get(f"/v1/pipeline/runs/{run['id']}/export?min_score=80")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    rows = csv_rows(res)
    assert [r["company"] for r in rows] == ["Summit Plumbing & Sons"]
    assert rows[0]["email"] == "office@summit.test"


async def test_export_includes_rejects_only_on_request(client, monkeypatch):
    use_source(monkeypatch, DiscoveryResult(LIVE, "live"))
    run = (await client.post("/v1/pipeline/runs", json=RUN_BODY)).json()
    url = f"/v1/pipeline/runs/{run['id']}/export"

    hidden = csv_rows(await client.get(url, params={"min_score": 0}))
    assert "reject" not in {r["tier"] for r in hidden}

    shown = csv_rows(
        await client.get(url, params={"min_score": 80, "include_rejects": "true"})
    )
    assert any(r["tier"] == "reject" for r in shown)
    assert all(float(r["fit_score"]) >= 80 for r in shown if r["tier"] != "reject")


async def test_sample_runs_are_labeled_and_never_exported(client, monkeypatch):
    samples = sample_companies("Plumbing", "Austin, TX")
    use_source(monkeypatch, DiscoveryResult(samples, "unavailable"))
    run = (await client.post("/v1/pipeline/runs", json=RUN_BODY)).json()

    assert run["source_status"] == "unavailable"
    assert {t["source"] for t in run["targets"]} == {"sample"}

    res = await client.get(
        f"/v1/pipeline/runs/{run['id']}/export",
        params={"min_score": 0, "include_rejects": "true"},
    )
    assert csv_rows(res) == []


async def test_unknown_market_is_422(offline_client, monkeypatch):
    async def no_market(vertical: str, market: str, cap: int) -> DiscoveryResult:
        raise MarketNotFoundError(market)

    monkeypatch.setattr(pipeline, "pull_from_osm", no_market)
    res = await offline_client.post("/v1/pipeline/runs", json=RUN_BODY)
    assert res.status_code == 422
    assert "Couldn't find the market" in res.json()["detail"]


async def test_unknown_run_is_404(client):
    res = await client.get("/v1/pipeline/runs/00000000-0000-0000-0000-000000000000")
    assert res.status_code == 404


async def test_upstream_outage_is_503(offline_client, monkeypatch):
    async def failing_pull(vertical: str, market: str, cap: int) -> DiscoveryResult:
        raise httpx.ConnectError("geocoder down")

    monkeypatch.setattr(pipeline, "pull_from_osm", failing_pull)
    res = await offline_client.post("/v1/pipeline/runs", json=RUN_BODY)
    assert res.status_code == 503


@pytest.mark.parametrize(
    "body",
    [
        {"vertical": "P", "market": "Austin, TX"},
        {"vertical": "Plumbing"},
        {**RUN_BODY, "headcount_min": "abc"},
        {**RUN_BODY, "limit": "ten"},
        {**RUN_BODY, "headcount_min": 50, "headcount_max": 10},
    ],
)
async def test_invalid_body_is_422(offline_client, body):
    res = await offline_client.post("/v1/pipeline/runs", json=body)
    assert res.status_code == 422


async def test_malformed_run_id_is_422(offline_client):
    res = await offline_client.get("/v1/pipeline/runs/not-a-uuid")
    assert res.status_code == 422


async def test_cors_preflight_allows_configured_origin(offline_client):
    res = await offline_client.options(
        "/v1/pipeline/runs",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "http://localhost:3000"

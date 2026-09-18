from urllib.parse import urlparse

import httpx
import pytest

from app.config import settings
from app.services import cache
from app.services import source_fetcher as sf

pytestmark = pytest.mark.anyio

REAL_HTTPX_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def empty_cache():
    cache._memory.clear()
    yield
    cache._memory.clear()


@pytest.fixture
def known_market(monkeypatch):
    async def fake_resolve(market: str) -> tuple[float, float]:
        return 39.7, -104.9

    monkeypatch.setattr(sf, "resolve_market", fake_resolve)


def fake_network(monkeypatch, overpass=(), nominatim=()) -> dict[str, int]:
    """Fake Overpass and Nominatim that answer from these queues in order.

    Queue items are responses, or exceptions to raise (e.g. a ConnectError).
    Returns a dict counting requests per service.
    """
    overpass_host = urlparse(settings.overpass_url).hostname
    queues = {"overpass": list(overpass), "nominatim": list(nominatim)}
    calls = {"overpass": 0, "nominatim": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        service = "overpass" if request.url.host == overpass_host else "nominatim"
        calls[service] += 1
        item = queues[service].pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def client(**kwargs) -> httpx.AsyncClient:
        return REAL_HTTPX_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(sf.httpx, "AsyncClient", client)
    return calls


def unreachable() -> httpx.ConnectError:
    return httpx.ConnectError("All connection attempts failed")


PLUMBER = {
    "tags": {
        "name": "Heating & Plumbing Engineers",
        "addr:housenumber": "715",
        "addr:street": "Vallejo Street",
        "addr:city": "Denver",
        "phone": "+1 303 555 0100",
        "start_date": "2006-04",
        "opening_hours": "Mo-Fr 08:00-17:00",
    }
}

NOMINATIM_ROW = {
    "name": "Time Plumbing Heating and Electric",
    "address": {
        "house_number": "1150",
        "road": "West 8th Avenue",
        "city": "Denver",
        "state": "Colorado",
        "ISO3166-2-lvl4": "US-CO",
    },
    "extratags": {
        "phone": "+1 303 555 0142",
        "opening_hours": "Mo-Fr 07:00-17:00",
    },
}


@pytest.mark.parametrize(
    ("raw", "year"),
    [
        ("2006", 2006),
        ("2006-05-01", 2006),
        ("~1998", 1998),
        ("C19", None),
        (None, None),
    ],
)
def test_parse_founded_year(raw, year):
    assert sf.parse_founded_year(raw) == year


def test_market_state():
    assert sf.market_state("Denver, CO") == "CO"
    assert sf.market_state("Denver") == ""


def test_candidate_from_tags_uses_only_published_fields():
    c = sf.candidate_from_tags(PLUMBER["tags"], "Denver, CO")
    assert c.street_line == "715 Vallejo Street"
    assert c.region == "CO"
    assert c.founded_year == 2006
    assert c.has_opening_hours is True
    assert c.web_url == ""
    assert c.email == ""
    assert c.brand == ""


def test_brand_wikidata_marks_chain():
    tags = {"name": "Jiffy Lube", "brand": "Jiffy Lube", "brand:wikidata": "Q6192247"}
    assert sf.candidate_from_tags(tags, "Chicago, IL").brand == "Jiffy Lube"


def test_samples_are_labeled_and_fictional():
    samples = sf.sample_companies("Landscaping", "Denver, CO")
    assert {s.origin for s in samples} == {"sample"}
    assert all(s.main_phone.startswith("555-01") for s in samples)
    assert all("{" not in s.web_url for s in samples)
    assert {s.region for s in samples} == {"CO"}


async def test_live_results_are_cached(monkeypatch, known_market):
    calls = fake_network(
        monkeypatch, overpass=[httpx.Response(200, json={"elements": [PLUMBER]})]
    )
    first = await sf.pull_from_osm("Plumbing", "Denver, CO", 10)
    second = await sf.pull_from_osm("Plumbing", "Denver, CO", 10)
    assert first.source_status == second.source_status == "live"
    assert first.source_detail is None
    assert [c.legal_name for c in second.candidates] == ["Heating & Plumbing Engineers"]
    assert calls == {"overpass": 1, "nominatim": 0}


async def test_busy_overpass_is_retried_once(monkeypatch, known_market):
    calls = fake_network(
        monkeypatch,
        overpass=[
            httpx.Response(429),
            httpx.Response(200, json={"elements": [PLUMBER]}),
        ],
    )
    result = await sf.pull_from_osm("Plumbing", "Denver, CO", 10)
    assert result.source_status == "live"
    assert calls == {"overpass": 2, "nominatim": 0}


async def test_unreachable_overpass_falls_back_to_nominatim(monkeypatch, known_market):
    calls = fake_network(
        monkeypatch,
        overpass=[unreachable()],
        nominatim=[httpx.Response(200, json=[NOMINATIM_ROW])],
    )
    result = await sf.pull_from_osm("Plumbing", "Denver, CO", 10)

    assert result.source_status == "live"
    assert "All connection attempts failed" in result.source_detail
    assert "used Nominatim search instead" in result.source_detail
    [company] = result.candidates
    assert company.origin == "osm"
    assert company.street_line == "1150 West 8th Avenue"
    assert company.region == "CO"
    assert company.main_phone == "+1 303 555 0142"
    assert company.has_opening_hours is True

    cached = await sf.pull_from_osm("Plumbing", "Denver, CO", 10)
    assert cached.source_detail == result.source_detail
    assert calls == {"overpass": 1, "nominatim": 1}


async def test_old_cache_shape_is_treated_as_a_miss(monkeypatch, known_market):
    ck = f"osm:{settings.cache_version}:plumbing:denver, co:10"
    await cache.put_cached(ck, [{"legal_name": "Stale Row"}], 60)
    calls = fake_network(
        monkeypatch, overpass=[httpx.Response(200, json={"elements": [PLUMBER]})]
    )
    result = await sf.pull_from_osm("Plumbing", "Denver, CO", 10)
    assert [c.legal_name for c in result.candidates] == ["Heating & Plumbing Engineers"]
    assert calls["overpass"] == 1


async def test_both_sources_down_returns_uncached_samples(monkeypatch, known_market):
    calls = fake_network(
        monkeypatch,
        overpass=[unreachable(), unreachable()],
        nominatim=[httpx.Response(503), httpx.Response(503)],
    )
    first = await sf.pull_from_osm("Plumbing", "Denver, CO", 10)
    assert first.source_status == "unavailable"
    assert {c.origin for c in first.candidates} == {"sample"}
    assert "Nominatim: HTTP 503" in first.source_detail

    await sf.pull_from_osm("Plumbing", "Denver, CO", 10)
    assert calls == {"overpass": 2, "nominatim": 2}


async def test_filters_nominatim_cannot_express_skip_the_fallback(
    monkeypatch, known_market
):
    calls = fake_network(monkeypatch, overpass=[unreachable()])
    result = await sf.pull_from_osm("Cleaning", "Denver, CO", 10)
    assert result.source_status == "unavailable"
    assert calls == {"overpass": 1, "nominatim": 0}


async def test_no_results_are_not_padded(monkeypatch, known_market):
    calls = fake_network(
        monkeypatch, overpass=[httpx.Response(200, json={"elements": []})]
    )
    result = await sf.pull_from_osm("Plumbing", "Denver, CO", 10)
    assert result.source_status == "no_results"
    assert result.candidates == []
    assert calls["nominatim"] == 0


def test_nominatim_tag():
    assert sf.nominatim_tag("[craft=plumber]") == "craft=plumber"
    assert sf.nominatim_tag('[shop=trade]["name"~Clean,i]') is None


def test_tags_from_nominatim_matches_overpass_shape():
    tags = sf.tags_from_nominatim(NOMINATIM_ROW)
    candidate = sf.candidate_from_tags(tags, "Denver, CO")
    assert candidate.legal_name == "Time Plumbing Heating and Electric"
    assert candidate.locality == "Denver"
    assert candidate.region == "CO"


async def test_unknown_market_raises(monkeypatch):
    async def no_match(market: str) -> None:
        return None

    monkeypatch.setattr(sf, "resolve_market", no_match)
    with pytest.raises(sf.MarketNotFoundError):
        await sf.pull_from_osm("Plumbing", "Nowhereville, ZZ", 10)

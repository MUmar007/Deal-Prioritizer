import httpx
import pytest

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


def overpass_returning(monkeypatch, *responses: httpx.Response) -> list[int]:
    """Fake Overpass that returns these responses in order.

    The returned list gets one entry per request, so tests can count calls.
    """
    calls: list[int] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return queue.pop(0)

    def client(**kwargs) -> httpx.AsyncClient:
        return REAL_HTTPX_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(sf.httpx, "AsyncClient", client)
    monkeypatch.setattr(sf, "OVERPASS_RETRY_SECONDS", 0)
    return calls


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
    calls = overpass_returning(
        monkeypatch, httpx.Response(200, json={"elements": [PLUMBER]})
    )
    first = await sf.pull_from_osm("Plumbing", "Denver, CO", 10)
    second = await sf.pull_from_osm("Plumbing", "Denver, CO", 10)
    assert first.source_status == second.source_status == "live"
    assert [c.legal_name for c in second.candidates] == ["Heating & Plumbing Engineers"]
    assert len(calls) == 1


async def test_busy_overpass_is_retried_once(monkeypatch, known_market):
    calls = overpass_returning(
        monkeypatch,
        httpx.Response(429),
        httpx.Response(200, json={"elements": [PLUMBER]}),
    )
    result = await sf.pull_from_osm("Plumbing", "Denver, CO", 10)
    assert result.source_status == "live"
    assert len(calls) == 2


async def test_unavailable_overpass_returns_uncached_samples(monkeypatch, known_market):
    calls = overpass_returning(
        monkeypatch,
        httpx.Response(504),
        httpx.Response(504),
        httpx.Response(504),
        httpx.Response(504),
    )
    first = await sf.pull_from_osm("Plumbing", "Denver, CO", 10)
    assert first.source_status == "unavailable"
    assert {c.origin for c in first.candidates} == {"sample"}

    await sf.pull_from_osm("Plumbing", "Denver, CO", 10)
    assert len(calls) == 4


async def test_no_results_are_not_padded(monkeypatch, known_market):
    overpass_returning(monkeypatch, httpx.Response(200, json={"elements": []}))
    result = await sf.pull_from_osm("Plumbing", "Denver, CO", 10)
    assert result.source_status == "no_results"
    assert result.candidates == []


async def test_unknown_market_raises(monkeypatch):
    async def no_match(market: str) -> None:
        return None

    monkeypatch.setattr(sf, "resolve_market", no_match)
    with pytest.raises(sf.MarketNotFoundError):
        await sf.pull_from_osm("Plumbing", "Nowhereville, ZZ", 10)

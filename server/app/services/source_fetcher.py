import asyncio
import logging
import math
import re
from dataclasses import asdict, dataclass
from typing import Literal

import httpx

from app.config import settings
from app.services.cache import get_cached, put_cached
from app.services.rank_engine import CompanyCandidate, dedupe_hash

logger = logging.getLogger(__name__)

YEAR = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")

VERTICAL_TAGS = {
    "hvac": "[craft=hvac]",
    "plumbing": "[craft=plumber]",
    "electrical": "[craft=electrician]",
    "landscaping": "[craft=gardener]",
    "cleaning": '[shop=trade]["name"~Clean,i]',
    "auto": "[shop=car_repair]",
    "dental": "[amenity=dentist]",
}

SourceStatus = Literal["live", "no_results", "unavailable"]


class MarketNotFoundError(Exception):
    """Nominatim found nothing for the market string."""


@dataclass
class DiscoveryResult:
    """Candidates plus where they came from.

    When source_status is "unavailable", neither Overpass nor the Nominatim
    fallback answered and the candidates are sample rows, not real companies.
    source_detail says what went wrong, if anything did.
    """

    candidates: list[CompanyCandidate]
    source_status: SourceStatus
    source_detail: str | None = None


def _tag_for_vertical(vertical: str) -> str:
    key = vertical.lower().strip()
    for k, tag in VERTICAL_TAGS.items():
        if k in key:
            return tag
    return '[shop=trade]["name"]'


def market_state(market: str) -> str:
    """Pull "CO" out of "Denver, CO". Returns "" when there's no state."""
    parts = [p.strip() for p in market.split(",")]
    tail = parts[-1] if len(parts) > 1 else ""
    return tail.upper() if len(tail) == 2 and tail.isalpha() else ""


def parse_founded_year(value: str | None) -> int | None:
    """Get a year out of OSM's free-text start_date ("2006-05", "~1998", ...)."""
    if not value:
        return None
    match = YEAR.search(value)
    return int(match.group(1)) if match else None


def candidate_from_tags(tags: dict[str, str], market: str) -> CompanyCandidate:
    """Copy only what's in the tags.

    OSM marks chain locations with brand:wikidata, which is how chains get
    caught even when the name looks independent.
    """
    street = " ".join(
        p for p in (tags.get("addr:housenumber"), tags.get("addr:street")) if p
    )
    brand = ""
    if tags.get("brand:wikidata"):
        brand = tags.get("brand") or tags.get("name", "")
    return CompanyCandidate(
        legal_name=tags.get("name", ""),
        street_line=street,
        locality=tags.get("addr:city") or market.split(",")[0].strip(),
        region=tags.get("addr:state") or market_state(market),
        main_phone=tags.get("phone") or tags.get("contact:phone") or "",
        web_url=tags.get("website") or tags.get("contact:website") or "",
        email=tags.get("email") or tags.get("contact:email") or "",
        founded_year=parse_founded_year(tags.get("start_date")),
        has_opening_hours=bool(tags.get("opening_hours")),
        brand=brand,
        origin="osm",
    )


async def resolve_market(market: str) -> tuple[float, float] | None:
    """Geocode a US market to (lat, lon), or None if Nominatim has no match.

    HTTP errors are left to the caller so the route can answer 503.
    """
    ck = f"market:{market.lower()}"
    hit = await get_cached(ck)
    if hit:
        return float(hit[0]), float(hit[1])

    headers = {"User-Agent": settings.nominatim_ua}
    params = {"q": market, "format": "json", "limit": 1, "countrycodes": "us"}
    async with httpx.AsyncClient(
        timeout=settings.http_timeout, headers=headers
    ) as client:
        res = await client.get(settings.geocode_url, params=params)
        res.raise_for_status()
        rows = res.json()
    if not rows:
        return None
    lat, lon = float(rows[0]["lat"]), float(rows[0]["lon"])
    await put_cached(ck, [lat, lon], settings.cache_seconds)
    return lat, lon


def _bounding_box(
    lat: float, lon: float, radius_km: float = 10.0
) -> tuple[float, float, float, float]:
    d = radius_km * 0.009
    cos_lat = max(0.4, abs(math.cos(math.radians(lat))))
    return lat - d, lon - d / cos_lat, lat + d, lon + d / cos_lat


def sample_companies(vertical: str, market: str) -> list[CompanyCandidate]:
    """Build placeholder rows for when Overpass is down.

    They're tagged origin="sample" so the UI can badge them and the CSV
    export can skip them.
    """
    town = market.split(",")[0].strip()
    slug = re.sub(r"\W+", "", vertical.lower())[:10] or "co"
    samples = [
        ("{town} {v} Partners", "", 2009, True),
        ("{town} Legacy {v}", "", 2003, True),
        ("Metro {v} Group", "https://facebook.com/metro{slug}", 2017, False),
        ("{town} {v} LLC", "https://{slug}.example.net", 2007, True),
        ("Bright {v} Co-op #88", "https://chain.example", 2020, False),
        ("Harbor {v} Services", "", 2012, True),
        ("Summit {v} & Sons", "", 1998, False),
    ]
    out: list[CompanyCandidate] = []
    for idx, (pattern, site, founded, hours) in enumerate(samples):
        fields = {"town": town, "v": vertical.title(), "slug": slug}
        out.append(
            CompanyCandidate(
                legal_name=pattern.format(**fields),
                street_line=f"{220 + idx} Commerce St",
                locality=town,
                region=market_state(market),
                main_phone=f"555-01{idx:02d}",
                web_url=site.format(**fields),
                founded_year=founded,
                has_opening_hours=hours,
                origin="sample",
            )
        )
    return out


def _describe(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return str(exc) or type(exc).__name__


async def _query_overpass(query: str) -> tuple[list[dict] | None, str]:
    """Run an Overpass query. Returns (elements, "") or (None, reason).

    The public instance throws 429s and 504s under load, so a busy response
    gets one retry after a short pause.
    """
    headers = {"User-Agent": settings.nominatim_ua}
    timeout = httpx.Timeout(25.0, connect=10.0)
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
                resp = await client.post(settings.overpass_url, data={"data": query})
                resp.raise_for_status()
                return resp.json().get("elements", []), ""
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.warning("Overpass returned %s (attempt %d)", status, attempt + 1)
            if attempt == 0 and status in settings.overpass_retry_status:
                await asyncio.sleep(settings.overpass_retry_seconds)
                continue
            return None, f"Overpass: {_describe(exc)}"
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Overpass query failed: %s", exc)
            return None, f"Overpass: {_describe(exc)}"
    return None, "Overpass: no answer"


def nominatim_tag(overpass_filter: str) -> str | None:
    """Turn "[craft=plumber]" into "craft=plumber".

    Returns None for filters Nominatim's search can't express, like the
    name regex used for cleaning companies.
    """
    match = re.fullmatch(r"\[(\w+)=(\w+)\]", overpass_filter)
    return f"{match.group(1)}={match.group(2)}" if match else None


def tags_from_nominatim(row: dict) -> dict[str, str]:
    """Reshape a Nominatim search result into the OSM tags Overpass would return."""
    address = row.get("address") or {}
    iso_state = address.get("ISO3166-2-lvl4", "")
    tags = {
        **(row.get("extratags") or {}),
        "name": row.get("name") or "",
        "addr:housenumber": address.get("house_number", ""),
        "addr:street": address.get("road", ""),
        "addr:city": address.get("city")
        or address.get("town")
        or address.get("village")
        or "",
        "addr:state": iso_state.removeprefix("US-"),
    }
    return {k: v for k, v in tags.items() if v}


async def _search_nominatim(
    tag: str, market: str, cap: int
) -> tuple[list[dict] | None, str]:
    """Fallback listing search for when Overpass can't be reached.

    overpass-api.de blocks some cloud IP ranges (Render's among them), while
    Nominatim still answers. It returns fewer results (40 max) but the same OSM
    data, tags included. Returns (elements, "") or (None, reason).
    """
    await asyncio.sleep(settings.nominatim_pause_seconds)
    params = {
        "q": f"[{tag}] {market}",
        "format": "jsonv2",
        "limit": min(cap, settings.nominatim_max_results),
        "extratags": 1,
        "addressdetails": 1,
        "countrycodes": "us",
    }
    headers = {"User-Agent": settings.nominatim_ua}
    try:
        async with httpx.AsyncClient(
            timeout=settings.http_timeout, headers=headers
        ) as client:
            resp = await client.get(settings.geocode_url, params=params)
            resp.raise_for_status()
            rows = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Nominatim search failed: %s", exc)
        return None, f"Nominatim: {_describe(exc)}"
    return [{"tags": tags_from_nominatim(row)} for row in rows], ""


async def pull_from_osm(vertical: str, market: str, cap: int) -> DiscoveryResult:
    """Find up to `cap` businesses for a vertical in a market.

    Raises MarketNotFoundError for an unknown market and httpx.HTTPError when
    the geocoder is down. Samples are never cached, otherwise a single bad
    minute on Overpass would stick around for the whole cache TTL.
    """
    ck = f"osm:{settings.cache_version}:{vertical.lower()}:{market.lower()}:{cap}"
    cached = await get_cached(ck)
    # Anything that isn't this dict shape is from an older CACHE_VERSION; treat
    # it as a miss rather than crash if the env var wasn't bumped yet.
    if isinstance(cached, dict):
        rows = [CompanyCandidate(**row) for row in cached["candidates"]]
        status = "live" if rows else "no_results"
        return DiscoveryResult(rows, status, cached.get("detail"))

    coords = await resolve_market(market)
    if coords is None:
        raise MarketNotFoundError(market)

    lat, lon = coords
    s, w, n, e = _bounding_box(lat, lon)
    tag = _tag_for_vertical(vertical)
    query = f"""
    [out:json][timeout:20];
    (
      node{tag}({s},{w},{n},{e});
      way{tag}({s},{w},{n},{e});
    );
    out center tags {cap};
    """
    elements, detail = await _query_overpass(query)
    fallback_tag = nominatim_tag(tag)
    if elements is None and fallback_tag:
        elements, fallback_detail = await _search_nominatim(fallback_tag, market, cap)
        detail = f"{detail}; " + (fallback_detail or "used Nominatim search instead")
    if elements is None:
        samples = sample_companies(vertical, market)
        return DiscoveryResult(samples, "unavailable", detail)

    found: list[CompanyCandidate] = []
    seen = set()
    for el in elements:
        tags = el.get("tags") or {}
        if not tags.get("name"):
            continue
        candidate = candidate_from_tags(tags, market)
        h = dedupe_hash(candidate.legal_name, candidate.locality)
        if h in seen:
            continue
        seen.add(h)
        found.append(candidate)

    found = found[:cap]
    payload = {"candidates": [asdict(c) for c in found], "detail": detail or None}
    await put_cached(ck, payload, settings.cache_seconds)
    return DiscoveryResult(found, "live" if found else "no_results", detail or None)

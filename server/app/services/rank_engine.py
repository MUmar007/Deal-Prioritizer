import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

NATIONAL_BRANDS = re.compile(
    r"(mcdonald|burger king|starbucks|subway|servpro|jiffy lube|"
    r"great clips|walmart|target|home depot|lowes|marriott|hilton|franchise)",
    re.IGNORECASE,
)
STORE_NUMBER = re.compile(r"(#\s*\d+|store\s+\d+)", re.IGNORECASE)
FAMILY_NAME = re.compile(
    r"(&\s*sons?\b|\band sons?\b|\bfamily\b|\bbros\b|\bbrothers\b)", re.IGNORECASE
)
WEAK_SITE_HOSTS = ("facebook.com", "instagram.com", "wixsite.com", "godaddysites.com")


@dataclass
class CompanyCandidate:
    """What a source told us about a company.

    Blank or None means the listing didn't say. Don't backfill these with
    guesses, because the score assumes every signal is real.
    """

    legal_name: str
    street_line: str = ""
    locality: str = ""
    region: str = ""
    main_phone: str = ""
    web_url: str = ""
    email: str = ""
    founded_year: int | None = None
    has_opening_hours: bool = False
    brand: str = ""
    origin: str = "osm"


@dataclass
class RankedTarget:
    candidate: CompanyCandidate
    fit_score: float
    tier: str
    rationale: str
    skip: bool = False
    skip_code: str = ""


def dedupe_hash(name: str, city: str) -> str:
    """Collapse "Acme Plumbing" and "ACME plumbing." in the same city to one key."""
    n = re.sub(r"\W+", "", (name or "").lower())
    c = re.sub(r"\W+", "", (city or "").lower())
    return f"{n}:{c}"


def is_weak_site(url: str) -> bool:
    """Treat Facebook/Instagram pages and Wix/GoDaddy subdomains as a weak site."""
    try:
        host = urlparse(url if "://" in url else f"http://{url}").hostname or ""
    except ValueError:
        return False
    return host.endswith(WEAK_SITE_HOSTS)


def rank_candidate(c: CompanyCandidate, this_year: int | None = None) -> RankedTarget:
    """Score 0-100 from what the listing contains. Weights are in the README."""
    label = c.legal_name or ""
    if c.brand or NATIONAL_BRANDS.search(label) or STORE_NUMBER.search(label):
        reason = (
            f"Chain or franchise ({c.brand})" if c.brand else "Chain or franchise name"
        )
        return RankedTarget(c, 0.0, "reject", reason, skip=True, skip_code="chain")

    fit = 50.0
    notes = []

    if c.main_phone:
        fit += 12
        notes.append("Phone listed")
    if c.email:
        fit += 5
        notes.append("Email listed")
    if c.street_line:
        fit += 8
        notes.append("Street address")
    if c.has_opening_hours:
        fit += 5
        notes.append("Hours listed")
    if c.web_url:
        if is_weak_site(c.web_url):
            fit += 8
            notes.append("Social or site-builder page only")
        else:
            fit += 5
            notes.append("Website listed")
    if c.founded_year:
        age = (this_year or datetime.now(UTC).year) - c.founded_year
        if age >= 10:
            fit += 12
        elif age >= 5:
            fit += 6
        notes.append(f"Founded {c.founded_year} (~{max(age, 0)}y)")
    if FAMILY_NAME.search(label):
        fit += 10
        notes.append("Family-named business")
    if not (c.main_phone or c.email or c.web_url):
        notes.append("No contact details in OSM, enrich first")

    fit = round(max(0.0, min(100.0, fit)), 1)
    if fit >= 78:
        tier = "prime"
    elif fit >= 58:
        tier = "solid"
    elif fit >= 40:
        tier = "watch"
    else:
        tier = "low"

    return RankedTarget(c, fit, tier, " · ".join(notes))


def meta_payload(ranked: RankedTarget) -> dict[str, Any]:
    return {
        "skip": ranked.skip,
        "skip_code": ranked.skip_code,
        "origin": ranked.candidate.origin,
    }

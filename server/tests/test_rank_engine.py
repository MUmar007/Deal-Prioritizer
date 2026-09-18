from app.services.rank_engine import CompanyCandidate, rank_candidate

THIS_YEAR = 2026


def test_chain_name_rejected():
    r = rank_candidate(CompanyCandidate(legal_name="Bright HVAC Co-op #88"))
    assert r.skip and r.tier == "reject"


def test_brand_tagged_chain_rejected():
    c = CompanyCandidate(legal_name="Firestone Complete Auto Care", brand="Firestone")
    r = rank_candidate(c)
    assert r.skip and r.tier == "reject"
    assert "Firestone" in r.rationale


def test_well_evidenced_independent_is_prime():
    c = CompanyCandidate(
        legal_name="Summit Plumbing & Sons",
        street_line="1 Main St",
        main_phone="512-555-0100",
        email="office@summitplumbing.test",
        has_opening_hours=True,
        founded_year=THIS_YEAR - 20,
    )
    r = rank_candidate(c, this_year=THIS_YEAR)
    assert r.tier == "prime"
    assert "Founded 2006 (~20y)" in r.rationale
    assert "Family-named business" in r.rationale


def test_bare_listing_flags_enrichment_and_claims_nothing():
    r = rank_candidate(CompanyCandidate(legal_name="Lone Star Plumbing"))
    assert r.fit_score == 50
    assert r.tier == "watch"
    assert r.rationale == "No contact details in OSM, enrich first"


def test_unknown_founding_year_is_not_scored():
    known = rank_candidate(
        CompanyCandidate(legal_name="A Plumbing", founded_year=THIS_YEAR - 15),
        this_year=THIS_YEAR,
    )
    unknown = rank_candidate(CompanyCandidate(legal_name="A Plumbing"))
    assert known.fit_score - unknown.fit_score == 12
    assert "Founded" not in unknown.rationale


def test_social_only_site_noted():
    c = CompanyCandidate(
        legal_name="Metro HVAC", web_url="https://facebook.com/metrohvac"
    )
    assert "Social or site-builder page only" in rank_candidate(c).rationale

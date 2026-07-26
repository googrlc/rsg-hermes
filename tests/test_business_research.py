from hermes.commands import business_research


def test_clean_query_removes_save_words() -> None:
    # The trailing "and save to crm" goes entirely — an earlier version left a
    # dangling "and" on the business name, which is what this used to assert.
    assert business_research._clean_query("research business Acme Plumbing Atlanta and save to crm") == "Acme Plumbing Atlanta"


def test_spine_defaults_fill_missing_codes() -> None:
    """A CONFIDENT top candidate fills a missing code.

    _relevance is required: _apply_spine_defaults gained a confidence gate, so a
    candidate without a score reads as 0 and is deliberately not written.
    """
    research = {"business_name": "Acme", "naics": None, "sic": None}
    confident = business_research._CONFIDENT_SCORE
    business_research._apply_spine_defaults(
        research,
        {
            "naics": [{"naics_code": "236220", "naics_title": "Commercial Construction",
                       "_relevance": confident}],
            "sic": [{"sic_code": "8711", "sic_description": "Engineering Services",
                     "_relevance": confident}],
        },
    )
    assert research["naics"] == "236220"
    assert research["sic"] == "8711"


def test_a_weak_candidate_is_left_for_a_human() -> None:
    """The point of the gate: silently writing a loose class-code guess onto an
    account is worse than leaving it blank. GL 92478 was already mislabelled once."""
    research = {"business_name": "Acme", "naics": None, "sic": None}
    business_research._apply_spine_defaults(
        research,
        {
            "naics": [{"naics_code": "236220", "naics_title": "Commercial Construction",
                       "_relevance": business_research._CONFIDENT_SCORE - 1}],
            "sic": [{"sic_code": "8711", "sic_description": "Engineering Services"}],
        },
    )
    assert research["naics"] is None
    assert research["sic"] is None


def test_note_body_includes_spine_candidates() -> None:
    body = business_research._note_body(
        {
            "business_name": "Acme",
            "short_summary": "Does plumbing work.",
            "claimed_services": ["plumbing"],
            "confidence": "medium",
            "classification_spine": {
                "naics": [{"naics_code": "236220", "naics_title": "Commercial Construction"}],
                "sic": [{"sic_code": "8711", "sic_description": "Engineering Services"}],
            },
        },
        "Acme Plumbing",
    )
    assert "Supabase classification spine candidates" in body
    assert "NAICS 236220" in body
    assert "SIC 8711" in body

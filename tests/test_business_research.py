from hermes.commands import business_research


def test_clean_query_removes_save_words() -> None:
    assert business_research._clean_query("research business Acme Plumbing Atlanta and save to crm") == "Acme Plumbing Atlanta and"


def test_spine_defaults_fill_missing_codes() -> None:
    research = {"business_name": "Acme", "naics": None, "sic": None}
    business_research._apply_spine_defaults(
        research,
        {
            "naics": [{"naics_code": "236220", "naics_title": "Commercial Construction"}],
            "sic": [{"sic_code": "8711", "sic_description": "Engineering Services"}],
        },
    )
    assert research["naics"] == "236220"
    assert research["sic"] == "8711"


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

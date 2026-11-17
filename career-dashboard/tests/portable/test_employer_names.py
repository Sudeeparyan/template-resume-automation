"""Conservative name matching cannot attribute another employer's permit history."""

import pytest

from backend.permits import employer_names


@pytest.mark.parametrize(
    "name,key",
    [
        ("Example Ireland Limited", "example"),
        ("Example (Ireland) Designated Activity Company", "example"),
        ("Example Europe UC", "example"),
        ("Example EMEA Company Limited by Guarantee", "example"),
        ("Example Teoranta", "example"),
        ("Example irl plc", "example"),
        ("O'Brien & Partners Ltd", "obrien and partners"),
    ],
)
def test_legal_forms_and_geography_normalize_without_changing_brand_tokens(name, key):
    assert employer_names.normalize_ie(name) == key


def test_trading_names_keep_both_legal_and_brand_keys():
    assert employer_names.keys_for(
        "Example Holdings Ltd t/a Otherbrand Ireland Limited"
    ) == ["example holdings", "otherbrand"]
    assert employer_names.keys_for("Example Ltd Trading As Example Ireland Ltd") == [
        "example"
    ]
    assert employer_names.keys_for("") == []
    assert employer_names.keys_for("   ") == []


@pytest.mark.parametrize(
    "brand,legal,matches",
    [
        ("alpha", "alpha payments europe", True),
        ("citi", "citi financial services", True),
        ("abc", "abc technologies", False),
        ("abc holdings", "abc holdings ireland", False),
        ("apple", "applegreen services", False),
        ("alpha", "alpha bus", False),
        ("alpha", "alpha", False),
        ("global", "global technology", False),
        ("global holdings", "global holdings ireland", False),
        ("", "alpha", False),
    ],
)
def test_prefix_requires_a_distinctive_first_token_and_word_boundary(
    brand, legal, matches
):
    assert employer_names.prefix_match(brand, legal) is matches


def test_more_than_five_legal_entity_candidates_is_ambiguous():
    assert employer_names.prefix_match("alpha", "alpha payments europe", entity_count=5)
    assert not employer_names.prefix_match(
        "alpha", "alpha payments europe", entity_count=6
    )
    assert not employer_names.prefix_match(
        "alpha", "alpha payments europe", entity_count=0
    )


def test_curated_aliases_normalize_trading_names(monkeypatch):
    monkeypatch.setattr(
        employer_names,
        "_alias_file",
        lambda: {
            "aliases": {
                "Otherbrand": ["Example Holdings Ltd t/a Otherbrand Ireland Ltd"]
            }
        },
    )
    employer_names.aliases.cache_clear()
    try:
        assert employer_names.aliases() == {
            "otherbrand": ["example holdings", "otherbrand"]
        }
    finally:
        employer_names.aliases.cache_clear()

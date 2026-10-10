from typing import Any

import pytest

from tests.conftest import ROOT, load_module

calibrate = load_module(ROOT / "scripts" / "calibrate_atp_tags.py")

SPIDER = "registry"
NO_IDENTIFIERS: dict[str, dict[str, str]] = {}


def pair(
    properties: dict[str, Any], tags: dict[str, str]
) -> tuple[str, dict[str, Any], dict[str, str]]:
    return SPIDER, properties, tags


def measured(
    pairs: list[tuple[str, dict[str, Any], dict[str, str]]],
    patterns: list[str],
    identifier_tags: dict[str, dict[str, str]] = NO_IDENTIFIERS,
    denied: list[str] | None = None,
) -> dict[tuple[str, str], dict[str, int]]:
    return calibrate.measure(pairs, patterns, identifier_tags, denied or [])


def test_agreement_counts_only_the_objects_carrying_the_tag() -> None:
    measures = measured(
        [
            pair({"fuel:diesel": "yes"}, {"fuel:diesel": "yes"}),
            pair({"fuel:diesel": "yes"}, {"fuel:diesel": "no"}),
            pair({"fuel:diesel": "yes"}, {}),
        ],
        ["fuel:*"],
    )
    assert measures == {(SPIDER, "fuel:diesel"): {"exported": 3, "observed": 2, "agreeing": 1}}


def test_a_prefix_pattern_keeps_each_key_apart_and_an_exact_one_only_itself() -> None:
    measures = measured(
        [pair({"fuel:lpg": "yes", "fuel:diesel": "yes", "name": "A", "self_service": "yes"}, {})],
        ["fuel:*", "self_service"],
    )
    assert sorted(key for _, key in measures) == ["fuel:diesel", "fuel:lpg", "self_service"]


def test_a_value_the_source_exports_as_json_compares_as_the_loader_writes_it() -> None:
    measures = measured([pair({"fuel:diesel": True}, {"fuel:diesel": "true"})], ["fuel:*"])
    assert measures[(SPIDER, "fuel:diesel")]["agreeing"] == 1


def test_spiders_are_measured_separately() -> None:
    measures = measured(
        [
            ("a", {"fuel:diesel": "yes"}, {"fuel:diesel": "yes"}),
            ("b", {"fuel:diesel": "yes"}, {"fuel:diesel": "no"}),
        ],
        ["fuel:*"],
    )
    assert measures[("a", "fuel:diesel")]["agreeing"] == 1
    assert measures[("b", "fuel:diesel")]["agreeing"] == 0


def test_without_patterns_every_key_is_measured() -> None:
    """No list to seed: the spider's own keys are the candidates."""
    measures = measured(
        [pair({"fuel:lpg": "yes", "self_service": "yes", "wheelchair": "no"}, {})], []
    )
    assert sorted(key for _, key in measures) == ["fuel:lpg", "self_service", "wheelchair"]


@pytest.mark.parametrize(
    "key",
    [
        "@spider",
        "@source_uri",
        "opening_hours",
        "email",
        "phone",
        "website",
        "contact:phone",
        "name",
        "name:it",
        "alt_name",
        "official_name",
        "short_name",
        "brand",
        "brand:wikidata",
        "addr:street",
        "addr:postcode",
        "shop",
        "amenity",
        "ref",
        "ref:vat",
    ],
)
def test_a_key_another_mechanism_owns_is_never_measured(key: str) -> None:
    assert measured([pair({key: "x"}, {key: "x"})], []) == {}


@pytest.mark.parametrize("pattern", ["name", "addr:*", "ref"])
def test_a_pattern_narrows_the_search_but_never_past_the_denylist(pattern: str) -> None:
    properties = {"name": "A", "addr:city": "B", "ref": "1"}
    assert measured([pair(properties, properties)], [pattern]) == {}


def test_what_the_country_denies_is_never_measured_and_nothing_else_is_lost() -> None:
    """charge:* is the Italian fuel spider's prices: a country's knowledge, not the tool's."""
    properties = {"charge:diesel": "1.8", "fuel:diesel": "yes", "self_service": "yes"}

    assert sorted(key for _, key in measured([pair(properties, {})], [])) == [
        "charge:diesel",
        "fuel:diesel",
        "self_service",
    ]
    assert sorted(key for _, key in measured([pair(properties, {})], [], denied=["charge:*"])) == [
        "fuel:diesel",
        "self_service",
    ]
    # A pattern cannot widen past it either.
    assert measured([pair(properties, {})], ["charge:*"], denied=["charge:*"]) == {}


def test_a_key_the_configuration_maps_to_an_identifier_is_never_measured() -> None:
    """Identifier keys are per spider: `licence` names one for this spider only."""
    identifier_tags = {"*": {"vat": "ref:vat"}, SPIDER: {"licence": "ref:licence"}}
    properties = {"vat": "1", "licence": "2", "fuel:lpg": "yes"}

    assert sorted(key for _, key in measured([pair(properties, {})], [], identifier_tags)) == [
        "fuel:lpg"
    ]
    # Another spider's `licence` is an ordinary property.
    other: tuple[str, dict[str, Any], dict[str, str]] = ("other", properties, {})
    assert sorted(key for _, key in measured([other], [], identifier_tags)) == [
        "fuel:lpg",
        "licence",
    ]

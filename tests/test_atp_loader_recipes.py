"""The `identifiers` and `writable_tags` recipes of the ATP loader (specs/07).

They are DuckDB expressions composed from the configuration, run here on an
in-memory table shaped like the parquet — `id` and `properties` as JSON — so
no database is involved.
"""

import dataclasses
import json
from typing import Any

import duckdb
import pytest

from src.config import Country, get_country
from src.pipeline.atp import identifiers_sql, writable_tags_sql

FUEL = "gov_osservaprezzi_carburanti_it"

POIS = {
    "fuel": {
        "@spider": FUEL,
        "ref": 49195,
        "ref:vat": "IT123",
        "fuel:diesel": "yes",
        "fuel:octane_95": "yes",
        "self_service": "yes",
        "charge:diesel": "1.8",
    },
    "shop": {"@spider": "babylone_fr", "ref": "S-7", "ref:vat": "IT456", "fuel:diesel": "no"},
    "blank": {"@spider": "babylone_fr", "ref:vat": ""},
    "bare": {"@spider": "babylone_fr"},
    "unspidered": {"ref:vat": "IT789"},
}


def country(
    identifiers: dict[str, dict[str, str]], writables: dict[str, tuple[str, ...]]
) -> Country:
    return dataclasses.replace(
        get_country(), identifier_tags=identifiers, atp_writable_tags=writables
    )


def run(expression: str) -> dict[str, dict[str, Any]]:
    """The expression's value for each POI, parsed back from its JSON."""
    con = duckdb.connect()
    con.execute("CREATE TABLE t (id VARCHAR, properties JSON)")
    con.executemany("INSERT INTO t VALUES (?, ?)", [(i, json.dumps(p)) for i, p in POIS.items()])
    rows = con.execute(f"SELECT id, {expression}::VARCHAR FROM t").fetchall()  # noqa: S608
    return {i: json.loads(v) for i, v in rows}


def test_no_configuration_reads_nothing() -> None:
    empty = country({}, {})

    assert all(v == {} for v in run(identifiers_sql(empty)).values())
    assert all(v == {} for v in run(writable_tags_sql(empty)).values())


def test_an_identifier_follows_the_spider_that_exports_it() -> None:
    ids = run(
        identifiers_sql(country({"*": {"ref:vat": "ref:vat"}, FUEL: {"ref": "ref:mise"}}, {}))
    )

    # `ref` is the MISE code for the fuel spider only; `ref:vat` names itself
    # everywhere, spiders the configuration does not list included. The value
    # arrives as text, whatever the JSON type was.
    assert ids == {
        "fuel": {"ref:vat": "IT123", "ref:mise": "49195"},
        "shop": {"ref:vat": "IT456"},
        "blank": {},
        "bare": {},
        "unspidered": {"ref:vat": "IT789"},
    }


def test_a_spider_entry_wins_over_the_universal_one_on_a_property_key() -> None:
    ids = run(
        identifiers_sql(
            country({"*": {"ref:vat": "ref:vat"}, FUEL: {"ref:vat": "ref:operator"}}, {})
        )
    )

    assert ids["fuel"] == {"ref:operator": "IT123"}
    assert ids["shop"] == {"ref:vat": "IT456"}


def test_a_property_key_may_name_two_osm_tags() -> None:
    ids = run(identifiers_sql(country({FUEL: {"ref": "ref:mise", "ref:vat": "ref:vat"}}, {})))

    assert ids["fuel"] == {"ref:mise": "49195", "ref:vat": "IT123"}
    assert ids["shop"] == {}


def test_a_quote_in_the_configuration_stays_data() -> None:
    ids = run(identifiers_sql(country({"*": {"ref:vat": "it's"}}, {})))

    assert ids["fuel"] == {"it's": "IT123"}


@pytest.mark.parametrize(
    ("writables", "expected"),
    [
        # A prefix, for every spider.
        (
            {"*": ("fuel:*",)},
            {
                "fuel": {"fuel:diesel": "yes", "fuel:octane_95": "yes"},
                "shop": {"fuel:diesel": "no"},
                "blank": {},
                "unspidered": {},
            },
        ),
        # A spider's own patterns come on top of "*"'s, for that spider only;
        # an exact key is no prefix.
        (
            {"*": ("fuel:*",), FUEL: ("self_service",)},
            {
                "fuel": {"fuel:diesel": "yes", "fuel:octane_95": "yes", "self_service": "yes"},
                "shop": {"fuel:diesel": "no"},
            },
        ),
        # No "*": only the spider that is listed reads anything.
        (
            {FUEL: ("fuel:*",)},
            {"fuel": {"fuel:diesel": "yes", "fuel:octane_95": "yes"}, "shop": {}},
        ),
    ],
)
def test_a_writable_is_a_property_a_pattern_of_its_spider_matches(
    writables: dict[str, tuple[str, ...]], expected: dict[str, dict[str, str]]
) -> None:
    tags = run(writable_tags_sql(country({}, writables)))

    assert {poi: tags[poi] for poi in expected} == expected
    assert all("charge:diesel" not in t for t in tags.values())

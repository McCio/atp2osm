"""The wave-2 comparison, run as SQL.

MATCHED_POI_SQL decides on its own which tags ATP would replace — the brand
list and /validate both read that decision, so it is worth executing rather
than reading. The tables here are shaped like mv_places and atp_places, with
only the columns the expression touches.
"""

import json
from collections.abc import Iterator

import psycopg
import pytest
from psycopg.rows import dict_row

from src.config import Database
from src.matching import Tags, matched_poi_sql
from src.phone import ensure_normalize_phone
from tests.conftest import Connection

SCHEMA = """
    DROP TABLE IF EXISTS mv_places;
    DROP TABLE IF EXISTS atp_places;
    CREATE TABLE mv_places (
        osm_id BIGINT, node_type TEXT, tags JSONB, name TEXT,
        brand_wikidata TEXT, brand_wikidata_source TEXT, nsi_tags JSONB,
        brand TEXT, city TEXT, postcode TEXT, opening_hours TEXT,
        website TEXT, phone TEXT, email TEXT, version INT,
        osm_timestamp TIMESTAMPTZ, members JSONB, geom geometry(Point, 4326)
    );
    CREATE TABLE atp_places (
        id TEXT, brand TEXT, brand_wikidata TEXT, name TEXT, email TEXT,
        phone TEXT, website TEXT, opening_hours TEXT, country TEXT,
        city TEXT, source_uri TEXT, source_type TEXT, spider_id TEXT,
        postcode TEXT, subdivision_code TEXT, subdivision_name TEXT,
        category TEXT[], geom TEXT, identifiers JSONB DEFAULT '{}'::jsonb,
        writable_tags JSONB DEFAULT '{}'::jsonb
    );
"""

POINT = "ST_SetSRID(ST_MakePoint(2.35, 48.85), 4326)"
GEOJSON = '{"type":"Point","coordinates":[2.35,48.85]}'


@pytest.fixture
def places(_migrated: Database) -> Iterator[Connection]:
    # The migrations define the SQL functions the query calls.
    with psycopg.connect(_migrated.conninfo) as conn:
        ensure_normalize_phone(conn)
        conn.execute(SCHEMA)
        conn.commit()
        yield conn


def modifiable(
    conn: Connection, osm_tags: dict[str, str], **atp: str
) -> tuple[dict[str, str] | None, bool | None]:
    """What wave 2 would replace on an object carrying *osm_tags*."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("TRUNCATE mv_places, atp_places")
        cur.execute(
            f"""INSERT INTO mv_places (osm_id, node_type, tags, name,
                    brand_wikidata, brand, opening_hours, website, phone, email,
                    version, geom)
                VALUES (1, 'node', %s::jsonb, %s, 'Q1', 'Babylone',
                    %s, COALESCE(%s, %s), COALESCE(%s, %s), COALESCE(%s, %s),
                    1, {POINT})""",  # noqa: S608 — a constant of the test
            (
                json.dumps(osm_tags),
                osm_tags.get("name"),
                osm_tags.get("opening_hours"),
                osm_tags.get("website"),
                osm_tags.get("contact:website"),
                osm_tags.get("phone"),
                osm_tags.get("contact:phone"),
                osm_tags.get("email"),
                osm_tags.get("contact:email"),
            ),
        )
        cur.execute(
            """INSERT INTO atp_places (id, brand, brand_wikidata, name, email,
                   phone, website, opening_hours, subdivision_code, geom)
               VALUES ('a1', 'Babylone', 'Q1', 'Babylone', %s, %s, %s, %s, '75', %s)""",
            (
                atp.get("email"),
                atp.get("phone"),
                atp.get("website"),
                atp.get("opening_hours"),
                GEOJSON,
            ),
        )
        row = cur.execute(matched_poi_sql()).fetchone()
    return (row or {}).get("modifiable_tags"), (row or {}).get("is_modifiable")


def test_a_differing_value_is_modifiable(places: Connection) -> None:
    tags, flag = modifiable(places, {"phone": "01 23 45 67 89"}, phone="+33 8 20 33 22 11")
    assert flag is True
    assert tags == {"phone": "+33 8 20 33 22 11"}


def test_the_same_number_written_differently_is_not_a_difference(places: Connection) -> None:
    """normalize_phone is what makes the two writings meet."""
    tags, flag = modifiable(places, {"phone": "01 23 45 67 89"}, phone="+33 1 23 45 67 89")
    assert (tags, flag) == ({}, False)


def test_a_contact_writing_is_compared_too(places: Connection) -> None:
    tags, _ = modifiable(
        places, {"contact:website": "https://old.example"}, website="https://babylone.fr"
    )
    assert tags == {"website": "https://babylone.fr"}


def test_a_stale_contact_writing_is_seen_behind_an_up_to_date_one(places: Connection) -> None:
    """mv_places.phone COALESCEs one over the other; the raw tags do not."""
    tags, _ = modifiable(
        places,
        {"phone": "01 23 45 67 89", "contact:phone": "01 00 00 00 00"},
        phone="01 23 45 67 89",
    )
    assert tags == {"phone": "01 23 45 67 89"}


def test_a_missing_tag_is_wave_1s_business(places: Connection) -> None:
    tags, flag = modifiable(places, {"name": "Babylone"}, phone="0123456789")
    assert (tags, flag) == ({}, False)


def test_whitespace_around_separators_is_not_a_difference(places: Connection) -> None:
    """node/12625718605: the same hours, ATP's without the spaces after commas."""
    tags, flag = modifiable(
        places,
        {"opening_hours": "Mo-Th 08:00-12:00, 14:00-18:00; Fr 08:00-12:00, 14:00-17:00"},
        opening_hours="Mo-Th 08:00-12:00,14:00-18:00; Fr 08:00-12:00,14:00-17:00",
    )
    assert (tags, flag) == ({}, False)


def test_a_spelling_is_not_a_difference(places: Connection) -> None:
    """`Su closed`, a rule per day, a split midnight: ATP's dialect, same hours."""
    tags, flag = modifiable(
        places,
        {"opening_hours": "Mo-We,Fr 09:00-02:00; Th 09:00-12:00; PH off"},
        opening_hours="Mo 09:00-24:00; Tu-We 00:00-02:00,09:00-24:00; "
        "Th 00:00-02:00,09:00-12:00; Fr 09:00-24:00; Sa 00:00-02:00; Su closed",
    )
    assert (tags, flag) == ({}, False)


def test_the_replacement_is_written_the_osm_way(places: Connection) -> None:
    tags, _ = modifiable(
        places,
        {"opening_hours": "Mo-Fr 08:00-18:00"},
        opening_hours="Mo-Fr 08:00-19:00; Sa closed",
    )
    assert tags == {"opening_hours": "Mo-Fr 08:00-19:00; Sa off"}


def test_a_closed_day_alone_is_not_worth_a_changeset(places: Connection) -> None:
    tags, flag = modifiable(
        places,
        {"opening_hours": "Mo-Fr 08:00-18:00"},
        opening_hours="Mo-Fr 08:00-18:00; Sa closed",
    )
    assert (tags, flag) == ({}, False)


def test_different_hours_still_are(places: Connection) -> None:
    tags, _ = modifiable(
        places,
        {"opening_hours": "Mo-Fr 08:00-18:00"},
        opening_hours="Mo-Fr 08:00-19:00",
    )
    assert tags == {"opening_hours": "Mo-Fr 08:00-19:00"}


def test_a_week_the_comparison_cannot_read_is_never_proposed(places: Connection) -> None:
    """Seasonal or commented hours: overwriting them is a loss, so they are left to humans."""
    for old in (
        "Jan-Mar Mo-Fr 09:00-12:00; Apr-Dec Mo-Fr 09:00-18:00",
        'Mo-Fr 09:00-12:00 "sur rendez-vous"',
        "PH off",
    ):
        tags, flag = modifiable(places, {"opening_hours": old}, opening_hours="Mo-Fr 08:00-19:00")
        assert (tags, flag) == ({}, False), old


def nsi_case_fix(conn: Connection, osm_tags: dict[str, str], *items: tuple[str, str, str]) -> Tags:
    """What wave 2 proposes from NSI, given its (brand, name, shop) items for Q1."""
    conn.execute("TRUNCATE nsi_brands")
    for brand, name, shop in items:
        conn.execute(
            "INSERT INTO nsi_brands VALUES ('Q1', %s, %s, 'shop', %s, '{}'::jsonb)",
            (brand, name, shop),
        )
    tags, _ = modifiable(conn, osm_tags)
    return tags or {}


@pytest.mark.parametrize(
    ("osm_tags", "items", "expected"),
    [
        pytest.param(
            {"shop": "supermarket", "brand": "lidl", "name": "LIDL"},
            [("Lidl", "Lidl", "supermarket")],
            {"brand": "Lidl", "name": "Lidl"},
            id="both-differ-by-case",
        ),
        pytest.param(
            {"shop": "supermarket", "brand": "Lidl", "name": "lidl"},
            [("Lidl", "Lidl", "supermarket")],
            {"name": "Lidl"},
            id="only-the-name-differs",
        ),
        pytest.param(
            {"shop": "supermarket", "brand": "Lidl", "name": "Lidl"},
            [("Lidl", "Lidl", "supermarket")],
            {},
            id="already-the-nsi-spelling",
        ),
        pytest.param(
            {"shop": "bank", "brand": "SG", "name": "SG"},
            [("Société Générale", "Société Générale", "bank")],
            {},
            id="another-word-is-a-divergence-not-a-spelling",
        ),
        pytest.param(
            {"shop": "supermarket", "brand": "lidl"},
            [("Lidl", "Lidl", "supermarket"), ("LIDL", "LIDL", "supermarket")],
            {},
            id="two-competing-spellings-decide-nothing",
        ),
        pytest.param(
            {"shop": "supermarket", "brand": "Lidl"},
            [("Lidl", "Lidl", "supermarket"), ("LIDL", "LIDL", "supermarket")],
            {},
            id="a-spelling-nsi-knows-is-left-alone",
        ),
        pytest.param(
            {"shop": "convenience", "brand": "lidl"},
            [("Lidl", "Lidl", "supermarket")],
            {},
            id="another-category-is-not-this-brand",
        ),
        pytest.param(
            {"brand": "lidl"},
            [("Lidl", "Lidl", "supermarket")],
            {},
            id="no-primary-tag-no-category-to-read",
        ),
    ],
)
def test_nsi_corrects_a_spelling_that_differs_by_case_only(
    places: Connection,
    osm_tags: dict[str, str],
    items: list[tuple[str, str, str]],
    expected: dict[str, str],
) -> None:
    assert nsi_case_fix(places, osm_tags, *items) == expected

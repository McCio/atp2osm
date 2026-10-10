"""An identifier the source assigns joins an ATP POI to its OSM object.

`atp.identifiers` is {OSM tag: value}; any pair equal to the object's tag
joins, and an identifier the object lacks makes the match importable.
"""

import json
from collections.abc import Iterator

import psycopg
import pytest
from psycopg.rows import dict_row

from src.config import Database
from src.matching import matched_poi_sql
from src.phone import ensure_normalize_phone
from tests.conftest import Connection
from tests.test_modifiable_tags import GEOJSON, POINT, SCHEMA


@pytest.fixture
def places(_migrated: Database) -> Iterator[Connection]:
    # The migrations define the SQL functions the query calls.
    with psycopg.connect(_migrated.conninfo) as conn:
        ensure_normalize_phone(conn)
        conn.execute(SCHEMA)
        conn.commit()
        yield conn


def match(
    conn: Connection,
    osm_tags: dict[str, str],
    identifiers: dict[str, str],
    writable_tags: dict[str, str] | None = None,
) -> dict[str, bool] | None:
    """The match flags of an object carrying *osm_tags* against an ATP POI
    carrying *identifiers*, or None when they do not join.

    Nothing else can join them: the ATP row has no brand, no contact detail,
    and its name is not the object's.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("TRUNCATE mv_places, atp_places")
        cur.execute(
            f"""INSERT INTO mv_places (osm_id, node_type, tags, version, geom)
                VALUES (1, 'node', %s::jsonb, 1, {POINT})""",  # noqa: S608 — a constant of the test
            (json.dumps(osm_tags),),
        )
        cur.execute(
            """INSERT INTO atp_places (id, name, subdivision_code, identifiers, writable_tags, geom)
               VALUES ('a1', 'Q8 Via Roma', '75', %s::jsonb, %s::jsonb, %s)""",
            (json.dumps(identifiers), json.dumps(writable_tags or {}), GEOJSON),
        )
        row = cur.execute(matched_poi_sql()).fetchone()
        return None if row is None else {"is_importable": row["is_importable"]}


def test_an_equal_identifier_joins(places: Connection) -> None:
    assert match(places, {"ref:mise": "49195"}, {"ref:mise": "49195"}) is not None


def test_one_pair_among_several_is_enough(places: Connection) -> None:
    osm = {"ref:vat": "IT123", "ref:mise": "49195"}
    assert match(places, osm, {"ref:mise": "49195", "ref:vat": "IT999"}) is not None


def test_a_divergent_identifier_does_not_join(places: Connection) -> None:
    assert match(places, {"ref:mise": "11111"}, {"ref:mise": "49195"}) is None


def test_a_poi_with_no_identifier_joins_nothing(places: Connection) -> None:
    assert match(places, {"ref:mise": "49195"}, {}) is None


def test_the_tag_must_be_the_one_the_identifier_names(places: Connection) -> None:
    assert match(places, {"ref": "49195"}, {"ref:mise": "49195"}) is None


def test_an_identifier_the_object_lacks_is_importable(places: Connection) -> None:
    """Joined on the other pair, the object still lacks the MISE code."""
    flags = match(places, {"ref:vat": "IT123"}, {"ref:vat": "IT123", "ref:mise": "49195"})
    assert flags == {"is_importable": True}


def test_an_object_that_has_them_all_is_not_importable(places: Connection) -> None:
    flags = match(places, {"ref:mise": "49195"}, {"ref:mise": "49195"})
    assert flags == {"is_importable": False}


def test_a_matched_object_lacking_a_writable_tag_is_importable(places: Connection) -> None:
    """The station has its ref:mise; it still receives the fuel it lacks."""
    flags = match(places, {"ref:mise": "49195"}, {"ref:mise": "49195"}, {"fuel:diesel": "yes"})
    assert flags == {"is_importable": True}


def test_a_writable_tag_the_object_has_is_not_importable(places: Connection) -> None:
    flags = match(
        places,
        {"ref:mise": "49195", "fuel:diesel": "no"},
        {"ref:mise": "49195"},
        {"fuel:diesel": "yes"},
    )
    assert flags == {"is_importable": False}


def test_a_writable_tag_never_joins(places: Connection) -> None:
    assert match(places, {"fuel:diesel": "yes"}, {}, {"fuel:diesel": "yes"}) is None

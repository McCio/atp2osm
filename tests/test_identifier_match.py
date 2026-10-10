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


def test_a_writable_tag_the_object_has_equal_is_not_importable(places: Connection) -> None:
    flags = match(
        places,
        {"ref:mise": "49195", "fuel:diesel": "yes"},
        {"ref:mise": "49195"},
        {"fuel:diesel": "yes"},
    )
    assert flags == {"is_importable": False}


def test_a_writable_tag_the_object_has_different_is_importable(places: Connection) -> None:
    """The source's value replaces the object's."""
    flags = match(
        places,
        {"ref:mise": "49195", "fuel:diesel": "no"},
        {"ref:mise": "49195"},
        {"fuel:diesel": "yes"},
    )
    assert flags == {"is_importable": True}


def test_a_writable_tag_never_joins(places: Connection) -> None:
    assert match(places, {"fuel:diesel": "yes"}, {}, {"fuel:diesel": "yes"}) is None


NAME = "Q8 Via Roma"
# About 70 m of longitude at this latitude: inside the radius, apart from the origin.
STEP = 0.001


OsmRow = tuple[int, dict[str, str], str | None, float]
AtpRow = tuple[str, str, dict[str, str], float]


def pairs(
    conn: Connection,
    osm: list[OsmRow],
    atp: list[AtpRow],
    with_website: tuple[str, ...] = (),
) -> set[tuple[int, str]]:
    """The (osm_id, atp_id) pairs the query keeps.

    *osm* is (id, tags, name, longitude offset), *atp* is (id, name, identifiers,
    longitude offset): the offsets place the candidates relative to each other.
    The ATP rows named in *with_website* carry a website the objects lack, so
    they have something to add.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("TRUNCATE mv_places, atp_places")
        for osm_id, tags, name, dx in osm:
            cur.execute(
                """INSERT INTO mv_places (osm_id, node_type, tags, name, version, geom)
                   VALUES (%s, 'node', %s::jsonb, %s, 1,
                           ST_SetSRID(ST_MakePoint(2.35 + %s, 48.85), 4326))""",
                (osm_id, json.dumps(tags), name, dx),
            )
        for atp_id, name, identifiers, dx in atp:
            geom = json.dumps({"type": "Point", "coordinates": [2.35 + dx, 48.85]})
            cur.execute(
                """INSERT INTO atp_places (id, name, subdivision_code, identifiers, geom)
                   VALUES (%s, %s, '75', %s::jsonb, %s)""",
                (atp_id, name, json.dumps(identifiers), geom),
            )
        cur.execute(
            "UPDATE atp_places SET website = 'https://q8.example' WHERE id = ANY(%s)",
            (list(with_website),),
        )
        return {(row["osm_id"], row["id"]) for row in cur.execute(matched_poi_sql()).fetchall()}


def test_the_name_does_not_outweigh_a_conflicting_identifier(places: Connection) -> None:
    """The object says it is station 11111: it is not station 49195, whatever the name."""
    osm: list[OsmRow] = [(1, {"ref:mise": "11111"}, NAME, 0.0)]
    assert pairs(places, osm, [("a", NAME, {"ref:mise": "49195"}, 0.0)]) == set()


def test_the_name_still_joins_an_object_lacking_the_identifier(places: Connection) -> None:
    osm: list[OsmRow] = [(1, {"amenity": "fuel"}, NAME, 0.0)]
    assert pairs(places, osm, [("a", NAME, {"ref:mise": "49195"}, 0.0)]) == {(1, "a")}


def test_one_equal_pair_outweighs_a_conflicting_one(places: Connection) -> None:
    osm: list[OsmRow] = [(1, {"ref:mise": "49195", "ref:vat": "IT111"}, None, 0.0)]
    atp: list[AtpRow] = [("a", NAME, {"ref:mise": "49195", "ref:vat": "IT999"}, 0.0)]
    assert pairs(places, osm, atp) == {(1, "a")}


def test_the_identifier_beats_a_nearer_candidate_of_the_same_object(places: Connection) -> None:
    """The nearest ATP POI shares the object's name; the one that names it by ref is farther."""
    osm: list[OsmRow] = [(1, {"ref:mise": "49195"}, NAME, 0.0)]
    atp: list[AtpRow] = [
        ("near", NAME, {}, 0.0),
        ("named", "Other", {"ref:mise": "49195"}, 2 * STEP),
    ]
    assert pairs(places, osm, atp) == {(1, "named")}


def test_a_registry_row_with_nothing_left_does_not_shadow_a_row_with_something_to_add(
    places: Connection,
) -> None:
    osm: list[OsmRow] = [(1, {"ref:mise": "49195"}, NAME, 0.0)]
    atp: list[AtpRow] = [("gov", "Other", {"ref:mise": "49195"}, 0.0), ("brand", NAME, {}, STEP)]
    assert pairs(places, osm, atp, with_website=("brand",)) == {(1, "brand")}


def test_the_identifier_wins_between_two_rows_with_something_to_add(places: Connection) -> None:
    osm: list[OsmRow] = [(1, {"ref:mise": "49195"}, NAME, 0.0)]
    atp: list[AtpRow] = [("gov", "Other", {"ref:mise": "49195"}, STEP), ("brand", NAME, {}, 0.0)]
    assert pairs(places, osm, atp, with_website=("gov", "brand")) == {(1, "gov")}


def test_an_identifier_match_is_not_dropped_as_ambiguous(places: Connection) -> None:
    """Two objects carry the POI's name; the one that carries its ref is the match."""
    osm: list[OsmRow] = [
        (1, {"ref:mise": "49195"}, NAME, 0.0),
        (2, {"amenity": "fuel"}, NAME, STEP),
    ]
    assert pairs(places, osm, [("a", NAME, {"ref:mise": "49195"}, 0.0)]) == {(1, "a")}

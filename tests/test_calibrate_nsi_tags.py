import copy
import datetime
from typing import Any

from src import config
from tests.conftest import CONFIG, ROOT, load_module

calibrate = load_module(ROOT / "scripts" / "calibrate_nsi_tags.py")

Item = dict[str, Any]


def nsi(*items: Item) -> dict[str, Any]:
    return {"nsi": {"brands/shop/supermarket": {"items": list(items)}}}


def item(**tags: str) -> Item:
    return {"locationSet": {"include": ["fr"]}, "tags": {"brand:wikidata": "Q1", **tags}}


def test_keeps_every_tag_not_only_the_writable_ones() -> None:
    (row,) = calibrate.calibration_rows(nsi(item(brand="Lidl", cuisine="none")))
    assert row[5] == '{"brand:wikidata": "Q1", "brand": "Lidl", "cuisine": "none"}'


def test_drops_the_tags_a_group_disagrees_on_and_keeps_the_others() -> None:
    """Measuring a tag whose value depends on the item picked measures nothing."""
    (row,) = calibrate.calibration_rows(
        nsi(item(brand="Lidl", name="Lidl"), item(brand="Lidl", name="Lidl Drive"))
    )
    assert row[5] == '{"brand:wikidata": "Q1", "brand": "Lidl"}'


def test_a_tag_missing_from_one_item_of_the_group_is_a_disagreement() -> None:
    (row,) = calibrate.calibration_rows(nsi(item(brand="Lidl"), item()))
    assert row[5] == '{"brand:wikidata": "Q1"}'


def test_categories_stay_separate_groups() -> None:
    rows = calibrate.calibration_rows(
        {
            "nsi": {
                "brands/shop/supermarket": {"items": [item(brand="A")]},
                "brands/amenity/fuel": {"items": [item(brand="B")]},
            }
        }
    )
    assert sorted(row[4] for row in rows) == ["fuel", "supermarket"]


def test_the_written_calibration_is_a_configuration_the_loader_accepts() -> None:
    """The script once wrote counts per tag at the top level: the schema refuses both."""
    document = copy.deepcopy(CONFIG)
    calibrate.write_calibration(
        document,
        ["brand:wikidata", "amenity"],
        {
            "brand:wikidata": {"observed": 900, "agreeing": 900, "rate": 100.0},
            "amenity": {"observed": 800, "agreeing": 797, "rate": 99.63},
            "cuisine": {"observed": 60, "agreeing": 30, "rate": 50.0},
        },
        "8.0.20260729",
        datetime.date(2026, 7, 30),
    )

    country = config.load(document).country

    assert country.nsi_writable_tags == {"brand:wikidata", "amenity"}
    assert country.nsi_calibration == {
        "nsi_version": "8.0.20260729",
        "measured_at": "2026-07-30",
        "agreement": {"brand:wikidata": 1.0, "amenity": 0.9963, "cuisine": 0.5},
    }


def test_regional_location_share_counts_the_country_own_regions() -> None:
    """fr-ara.geojson is not dropped, it is applied to the whole country."""
    assert calibrate.regional_location_share(
        {
            "nsi": {
                "brands/shop/supermarket": {
                    "items": [
                        item(),
                        {"locationSet": {"include": ["fr-ara.geojson"]}, "tags": {}},
                        {"locationSet": {"include": ["fr-75"]}, "tags": {}},
                        {"locationSet": {"include": ["us-tx.geojson"]}, "tags": {}},
                        {"locationSet": {"include": ["001"]}, "tags": {}},
                    ]
                }
            }
        }
    ) == (2, 4)

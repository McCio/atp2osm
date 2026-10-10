"""Measure, tag by tag, how often an ATP spider agrees with the OSM objects it matches.

`atp_writable_tags` is not a list anybody guesses: a registry's `fuel:*` may
agree with OSM almost everywhere, a crowd-sourced spider's with little, and the
write replaces what the object carries (specs/07_gov-ref-matching.md §6). So the
list is measured, per spider, on the pairs the matcher itself produces.

    ATP2OSM_CONFIG=… OSM_DB_PASSWORD=… [other secrets] \
        uv run python scripts/calibrate_atp_tags.py \
        [--spider gov_osservaprezzi_carburanti_it] [--pattern 'fuel:*']

Without `--spider` every spider with a match is measured, which is the whole
matching query over the country: meant for a calibration run, not a quick look.
Every property the spider exports is measured, bar the ones DENIED names, the
ones `country.atp_denied_tags` adds and the identifier keys the configuration
maps: those belong to other mechanisms, or to what the country knows about its
data. `--pattern` (a key, or a prefix with a trailing `*`) narrows the search, it
never widens it past the denylist. The rates are per concrete key, and the keys above
the threshold are what gets written to `country.atp_writable_tags` of the
configuration file, replacing the entry of each spider measured and leaving the
others alone. A key the spider starts exporting later is not covered until this
is rerun. The ATP parquet is read for the properties: `atp_places` keeps only the
ones already configured, so the list needs no seeding.

Agreement is the share of the objects carrying the tag that carry the source's
value; an object lacking it counts for nothing, since that is the tag being
added, not disputed. Read-only on the database.
"""

import argparse
import json
import os
import pathlib
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

import duckdb
import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from src.config import get_country, get_database
from src.matching import matched_poi_sql
from src.pipeline.constants import PARQUET_PATH

# What the source exports and OSM carries, per tag: the source's value as text.
Properties = dict[str, Any]
Tags = dict[str, str]
# (spider, ATP properties of the POI, tags of the OSM object it matched)
Pair = tuple[str, Properties, Tags]


# Keys a measured agreement must never make writable, in the configuration's
# pattern syntax: what the tool's own mechanisms own, whatever the country. A
# registry agrees with OSM on its name or its address nearly everywhere, and a
# replacing write of either is not what a stable tag is. What only a country
# knows (a source's prices, an internal id) is `country.atp_denied_tags`.
DENIED = (
    # The spider's own metadata, not a tag.
    "@*",
    # The founding four: always writable in MATCHED_POI_SQL and apply_on_node,
    # with their own comparison (spelling, normalised phone) and wave.
    "opening_hours",
    "email",
    "phone",
    "website",
    "contact:*",
    # What names the place; the brand is NSI's and the join key.
    "name",
    "name:*",
    "alt_name",
    "alt_name:*",
    "official_name",
    "official_name:*",
    "short_name",
    "short_name:*",
    "brand",
    "brand:*",
    # The address.
    "addr:*",
    # What kind of place it is: replacing one retypes the object. The loader
    # reads the same keys as the POI's category.
    "shop",
    "amenity",
    "tourism",
    "office",
    "leisure",
    "healthcare",
    "craft",
    "landuse",
    # Identifiers are identifier_tags' business, never a stable tag.
    "ref",
    "ref:*",
)


def matches(key: str, patterns: Iterable[str]) -> bool:
    """A key the way the configuration names them: exact, or a trailing-`*` prefix."""
    return any(key.startswith(p[:-1]) if p.endswith("*") else key == p for p in patterns)


def is_candidate(
    spider: str,
    key: str,
    patterns: Iterable[str],
    identifier_tags: Mapping[str, Mapping[str, str]],
    denied: Iterable[str] = (),
) -> bool:
    """Whether `key` is worth measuring for `spider`: not denied (built in, or by
    the configuration), not an identifier the configuration maps for it, and
    inside `patterns` when there are any.
    """
    identifiers = {**identifier_tags.get("*", {}), **identifier_tags.get(spider, {})}
    if key in identifiers or matches(key, [*DENIED, *denied]):
        return False
    return matches(key, patterns) if patterns else True


def as_text(value: object) -> str:
    """The loader reads a property with JSON_EXTRACT_STRING: strings unquoted, the rest JSON."""
    return value if isinstance(value, str) else json.dumps(value)


def measure(
    pairs: Iterable[Pair],
    patterns: Iterable[str],
    identifier_tags: Mapping[str, Mapping[str, str]],
    denied: Iterable[str] = (),
) -> dict[tuple[str, str], dict[str, int]]:
    """(spider, key) -> how many pairs the source exports it, OSM carries it, both agree."""
    patterns, denied = list(patterns), list(denied)
    counts: dict[tuple[str, str], Counter[str]] = {}
    for spider, properties, tags in pairs:
        for key, raw in properties.items():
            if not is_candidate(spider, key, patterns, identifier_tags, denied):
                continue
            count = counts.setdefault((spider, key), Counter())
            count["exported"] += 1
            if key in tags:
                count["observed"] += 1
                count["agreeing"] += tags[key] == as_text(raw)
    return {k: dict(c) for k, c in counts.items()}


def matched_pairs(spiders: list[str]) -> list[tuple[str, str, Tags]]:
    """(ATP id, spider, OSM tags) for every match of those spiders (every spider
    when none is named), as /validate sees them.
    """
    query = matched_poi_sql("atp.spider_id = ANY(%s)" if spiders else "TRUE")
    with (
        psycopg.connect(get_database().conninfo) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        cur.execute(query, (spiders,) if spiders else None)
        return [(row["id"], row["spider_id"], row["old_tags"]) for row in cur]


def properties_of(ids: list[str]) -> dict[str, Properties]:
    rows = duckdb.connect().execute(
        "SELECT id, properties::VARCHAR FROM read_parquet(?) WHERE id IN (SELECT unnest(?))",
        [str(PARQUET_PATH), ids],
    )
    return {str(atp_id): json.loads(properties) for atp_id, properties in rows.fetchall()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spider",
        action="append",
        default=[],
        help="ATP spider to measure (repeatable); default: every spider with a match",
    )
    parser.add_argument(
        "--pattern",
        action="append",
        default=[],
        help="narrow the search to this property key, or prefix ending in `*` (repeatable);"
        " default: every key the denylist leaves",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=98.0,
        help="agreement rate a tag needs to become writable",
    )
    parser.add_argument(
        "--min-observed",
        type=int,
        default=50,
        help="objects carrying a tag before its rate means anything",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the table without touching the configuration"
    )
    args = parser.parse_args()

    found = matched_pairs(args.spider)
    properties = properties_of([atp_id for atp_id, _, _ in found])
    measures = measure(
        (
            (spider, properties[atp_id], tags)
            for atp_id, spider, tags in found
            if atp_id in properties
        ),
        args.pattern,
        get_country().identifier_tags,
        get_country().atp_denied_tags,
    )

    writable: dict[str, list[str]] = {}
    print(f"{'spider':<36} {'tag':<28} {'exported':>9} {'observed':>9} {'agreeing':>9} {'rate':>7}")
    for (spider, key), count in sorted(measures.items()):
        observed, agreeing = count.get("observed", 0), count.get("agreeing", 0)
        # A key OSM never carries on a matched object has no rate to show.
        if not observed:
            continue
        rate = 100 * agreeing / observed
        keep = observed >= args.min_observed and rate >= args.threshold
        if keep:
            writable.setdefault(spider, []).append(key)
        print(
            f"{spider:<36} {key:<28} {count['exported']:>9} {observed:>9} {agreeing:>9}"
            f" {rate:>6.2f}%  {'yes' if keep else ''}"
        )

    if args.dry_run:
        return
    path = pathlib.Path(os.environ["ATP2OSM_CONFIG"])
    config: dict[str, Any] = json.loads(path.read_text())
    entries: dict[str, list[str]] = config["country"].setdefault("atp_writable_tags", {})
    # A spider without a match was not measured: its entry stays as it is.
    spiders = {spider for _, spider, _ in found}
    for spider in spiders:
        # An empty entry would not validate (minItems 1): no tag left, no entry.
        if spider in writable:
            entries[spider] = writable[spider]
        else:
            entries.pop(spider, None)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    print(f"\nWrote atp_writable_tags for {len(spiders)} spider(s) to {path}")


if __name__ == "__main__":
    main()

# Registry identifiers and stable source tags — ref matching

Specification for matching ATP entries on **source-assigned identifiers**
instead of wikidata only, and for writing the other **stable** tags a source
exports. Working case: the Italian fuel-station registry (Osservaprezzi /
MIMIT, ex-MISE) carried inside ATP by the spider
`gov_osservaprozzi_carburanti_it`.

Numbers cited are measured on the MIMIT `anagrafica_impianti_attivi.csv` of
2026-10-09 (23 933 active stations) and on the French development parquet
(3 389 755 POIs carrying a `ref`). They serve as a receipt: a significant
gap signals a bug.

---

## 1. What the feature is, and what it is not

The tool matches an ATP POI to an OSM object on an equality of attributes:
brand QID, brand, name+primary tag, email, website, phone. None of these
identifies *the place itself* — precision comes from the 500 m radius plus
the dedup, not from the join key.

Registries carry identifiers that do name the place. Crucially, **what an
ATP `ref` means is a property of the spider, not of the country**: the same
plain `ref` property is the MISE station code for the Italian fuel spider,
a store number for a retail chain, the VAT number elsewhere. A mapping at
country level would mis-join every other source, so the mapping is
**per source** (§2).

Nothing is downloaded here: the government data is already inside ATP. The
spider scrapes the Osservaprezzi API, maps `Bandiera` → brand QIDs itself
(Api-Ip, Esso, Q8, Tamoil, AgipEni, Shell, Europam, Giap, Energas,
Keropetrol, SanMarcoPetroli, Retitalia, Beyfin, Costantin, Lukoil — 17k of
24k stations), and exports the station id as plain `ref` (verified: the
API's `id` equals the registry's `idImpianto`, e.g. 49195 both sides).
OSM imports of that registry write it as `ref:mise` (~20 000 live objects,
wiki-documented). Matching on identifiers is exact wherever OSM carries
the tag, and writing it where it does not makes the link durable for the
next runs.

The feature is therefore two config-driven mechanisms, sharing one load:

1. **Identifier matching** — join and write, per-source (§2–§5).
2. **Stable writables** — `fuel:*` and other tags a source exports that
   are stable facts of the place, written wave 1, never a join key (§6).

**Out of scope:** `charge:*` prices (daily churn), any source outside ATP.

---

## 2. Configuration — two keys, two roles

```json
"identifier_tags": {
    "*": {"ref:vat": "ref:vat"},
    "gov_osservaprezzi_carburanti_it": {"ref": "ref:mise"}
},
"atp_writable_tags": ["fuel:*"]
```

**`identifier_tags`** — spider name → {ATP property key → OSM tag}, with
`"*"` as the entry that applies to every spider. The merge is one rule:
the spider's own map, overlaid on `"*"`, spider wins on a property key.
The split matches where the meaning lives:

- A property key that names its own OSM tag (`ref:vat` means `ref:vat`
  wherever it appears, in any spider) is **universal** — it goes in
  `"*"`, written once, trusted everywhere.
- A property key whose meaning depends on who exported it (plain `ref`:
  MISE code, store number, VAT) is **per source** — it goes under the
  spider, and a spider absent from the config exports `ref` to no one.
- A map, not one entry, because **both sides can carry several
  identifiers**: one ATP POI may export both `ref` (the MISE code) and
  `ref:vat` (the operator's VAT number — a spider may emit both), and one
  OSM object may carry `ref:mise`, `ref:vat`, plain `ref`. A POI joins on
  **any** pair where OSM's tag equals ATP's property (§4).
- The OSM tag values are read from `osm.tags` directly; exposing them in
  `mv_places` is unnecessary (§4d).

**`atp_writable_tags`** — spider → property-key patterns (a prefix with
trailing `*`, or an exact key) the wave-1 write may add, per source like
`identifier_tags` because a source's claims are only as good as the source
(a registry's fuel tags beat a crowd-sourced spider's). `"*"` applies to
every spider and adds to each spider's own list. `fuel:*` in the example:
which fuels a station offers is a stable fact, unlike prices (`charge:*`
stays out). NOT a join key, and never merged with `identifier_tags`:
`self_service=yes` is true of every nearby station too — an identifier
names one place, a stable tag describes it. Joining on the latter would
multiply false matches; the two keys keep the roles apart.

This key gates only tags **beyond the founding four** — `opening_hours`,
`email`, `phone`, `website` are hardcoded as always-writable in
`MATCHED_POI_SQL` and `apply_on_node`, the tool's founding contract: no
spider needs vouching for them. `atp_writable_tags` is the gate for
everything else a spider exports.

Both live on `country`, validated by the schema. Absent/empty = mechanism
off, behaviour identical to today.

---

## 3. The loader — identifiers as jsonb, writables as jsonb

`import_atp` (`src/pipeline/atp.py`) builds `atp_places` from the parquet
with a fixed SELECT. It gains two columns, both `jsonb`, both built by
DuckDB from the configuration — `identifiers_sql` and `writable_tags_sql`
compose the expressions, `tests/test_atp_loader_recipes.py` runs them:

```sql
-- identifiers: {osm_tag: value} for the merged ("*" + spider) properties
MAP_FROM_ENTRIES(LIST_FILTER([
    {'k': 'ref:vat',  'v': properties->>'$."ref:vat"'},
    {'k': 'ref:mise', 'v': properties->>'$."ref"'}
], t -> t.v IS NOT NULL AND t.v <> ''))::JSON     AS identifiers

-- stable writables: {tag: value} for every property matching a pattern
MAP_FROM_ENTRIES(LIST_TRANSFORM(
    LIST_FILTER(JSON_KEYS(properties), k -> starts_with(k, 'fuel:')),
    k -> {'k': k, 'v': JSON_EXTRACT_STRING(properties, '$."' || k || '"')}
))::JSON                                          AS writable_tags
```

- DuckDB has no `JSONB` type: the columns are `json` when it creates the
  table, and `import_atp` converts them with `ALTER COLUMN ... TYPE JSONB`
  before the indexes are built. A blank value is no identifier (it would
  equal an OSM tag left empty).
- The pairs come from the config in Python — `"*"` overlaid by the
  spider's own entry, on the ATP property key (`{"ref:vat": "ref:vat"}`
  merged with `{"ref": "ref:mise"}` for the fuel spider: two pairs,
  `ref:vat` universal plus `ref`→MISE); the SQL is composed with them
  (`sql.Literal`-quoted), one `CASE` arm per spider the configuration lists.
  The same for the writable patterns: the spider's own plus `"*"`'s.
- Rows of spiders absent from the config still pick up the `"*"` pairs; a
  property key no entry names is simply not read.
- `ATP_PLACES_INDEXES` gains a GIN index on `identifiers`.
- **Bridging migration required** (AGENTS.md, "Pipeline tables and the
  site"), `033_atp_places_identifiers.sql`: `ALTER TABLE IF EXISTS atp_places
  ADD COLUMN IF NOT EXISTS identifiers JSONB DEFAULT '{}'::jsonb;` and the
  same for `writable_tags`. `IF EXISTS` because a fresh instance migrates
  before its first refresh has built the table. The matching SQL tolerates
  the default from day one.

---

## 4. The matcher — any identifier joins

**a. `MATCHED_POI_SQL`** (`src/matching.py`), one OR arm, keyed on the
whole configured map (not per spider — the row itself says what it
carries):

```sql
OR EXISTS (
    SELECT 1 FROM jsonb_each(atp.identifiers) i(tag, value)
    WHERE osm.tags->>i.tag = i.value
)
```

`jsonb_each` on an empty map yields no rows, `EXISTS` is false, un-
configured POIs are untouched. OSM side needs no column: `osm.tags` is
already in `mv_places`, and the tags a config may name (`ref:mise`,
`ref:vat`, `ref`) are all in `tags` — the filter below keeps them (§4d).

**b. `is_importable`** counts a missing identifier — otherwise gov rows
(no hours, no website, no phone for most stations) propose nothing and the
feature is silent:

```sql
OR EXISTS (
    SELECT 1 FROM jsonb_each(atp.identifiers) i(tag, value)
    WHERE NOT osm.tags ? i.tag
)
```

**c. `apply_on_node` wave 1** writes each identifier the object lacks,
through the existing `apply_tag` (no-overwrite, `not:`-respect) — plus the
writables (§6). Wave 2 never replaces an identifier: a station whose OSM
object carries a *different* `ref:mise` is a review-worthy anomaly, not a
correction. No entry in `modifiable_tags`.

**d. The pre-filters** — or tagged objects die before the join sees them.
The set of OSM tags any config may name is `identifier_tags`' values (all
sources) plus the writable patterns' concrete keys is *not* needed (the
writables never join). To keep one list per place:

- `generic.lua` `matchable_keys` and `mv_places`'s `tags ?|` list: add the
  OSM tags named by `identifier_tags` (the config, again — the Lua file
  reads it through the same env var osm2pgsql already receives).
- A country with no config writes the same SQL/Lua as today (the lists
  are empty), so France is byte-identical.

All four (a–d) ship in one commit, or the counts the brand list shows and
what the join finds diverge.

---

## 5. Multiple identifiers, both sides — the semantics

- **Join**: any pair equal. A POI with both MISE code and VAT joins an
  OSM object carrying either — and if the OSM object carries one of them
  with a *different* value, no identifier pair matches, the POI falls
  back on the attribute arms as before. Divergent identifiers never
  produce a write (§4c writes only what is *absent*).
- **Dedup unchanged**: `DISTINCT ON (osm_id, node_type, atp_brand_wikidata)`
  keeps the nearest candidate. An identifier match and a brand match to
  the same object collapse into one; to different objects, both stand and
  the human reviews. Exactness raises the match rate, not the ambiguity
  resolution — the reviewer is the ambiguity resolution.
- **Pompe Bianche** (3 666 unbranded stations): no QID from the spider,
  so they stay off the brand list (§7) but still join by identifier
  wherever OSM has the tag, riding the ref arm into `nsi_tags`-less,
  brand-less proposals — `amenity=fuel` comes from the spider's category
  and from `writable_tags` handling only if configured.

---

## 6. Stable writables — wave 1 only

`atp_writable_tags` opens the wave-1 write to the matched source tags,
with the same rules every wave-1 write already follows:

- **Only what the object lacks** (`apply_tag`), never an overwrite, never
  `not:key`-insulting: `fuel:diesel=yes` on an object that a contributor
  surveyed as no-diesel stays untouched — their tag wins, ours sleeps.
- Values pass through as strings (the recipes unwrap JSON quoting).
- `self_service` and friends are *deliberately possible* here — a country
  that trusts a source's `self_service` can configure it and own that
  decision; the join key separation (§2) is what keeps it safe.
- Wave 2 (`modifiable_tags`) does not grow: replacing a contributor's
  fuel list is a different argument than filling an absent one.

Like `nsi_writable_tags`, the list is **measured, not guessed** — the
calibration script is the twin of `scripts/calibrate_nsi_tags.py` and
lands after the machinery it measures (§9): it matches the country's
`atp_places` against OSM (the existing `MATCHED_POI_SQL`, no upload),
joins `writable_tags` with `osm.tags` on matched pairs, and reports, per
(spider, tag), the share of pairs where OSM already carries the tag with
the same value — agreement, the same statistic the NSI script computes.
A registry's `fuel:*` is expected to measure high; a crowd-sourced
spider's to land where it lands, which is the point of measuring. The
script writes `atp_writable_tags` above a threshold, nobody copies a
rate by hand, and a new ATP release is re-evaluated by rerunning it.

---

## 7. Cooldown, history, brand list — unchanged

`import_history` and the cooldown keep keying on `brand_wikidata`;
`mv_places_brand` groups on it. Unbranded registry rows (Pompe Bianche)
have no QID and stay off the brand list — the branded 17k are the
integration surface, the unbranded join into already-branded objects.
Accepted for v1; the day it matters, the list key widens — a change this
spec does not make.

---

## 8. Receipt

After the first refresh of an Italian instance:

| control | expected |
|---|---|
| `SELECT count(*) FROM atp_places WHERE identifiers <> '{}'` | ≥ 20 000 (spider coverage) |
| `writable_tags <> '{}'` rows (fuel spider) | thousands |
| identifier-joined matches | thousands, bounded by OSM's `ref:mise` coverage |
| diff proposals adding `ref:mise` | > 0 on the first waves |
| diff proposals adding `fuel:*` | > 0, and zero overwriting an existing `fuel:*` |
| France config: brand-list totals | identical before/after deploy |

The last-but-one is the acceptance for §6 (a write that overwrites is a
bug); the last is the no-config no-change guarantee.

---

## 9. Implementation slices

One reviewable step each, each green (ruff, `pyright --strict`, suite):

1. **Config**: schema keys + descriptions (§2), `Country` fields, no
   consumer. Example updated.
2. **Loader**: the two jsonb recipes, indexes, bridging migration, tests
   on a parquet-shaped table (the recipes run in the test's DuckDB).
3. **Matcher**: SQL arms a–b + `is_importable`, config-gated; tests with
   staged `identifiers`.
4. **Writer + filters**: `apply_on_node` (§4c/§6), `generic.lua` and
   `mv_places` lists.
5. **Receipt run** on dev data: §8 controls.
6. **Calibration**: `scripts/calibrate_atp_tags.py` — per-(spider, tag)
   agreement over matched pairs, writing `atp_writable_tags` (§6). Needs
   slices 2–4 to have run once; measures what they produce.

Each slice MERGE-worthy independently; none leaves the site broken
mid-sequence (columns bridged before they are read).

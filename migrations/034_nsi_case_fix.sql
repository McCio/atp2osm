-- The NSI spelling of an object's brand or name when the object's own differs
-- from it by case alone (`lidl` -> `Lidl`), or NULL. specs/02_source-nsi.md §5.1.
--
-- Read from the labels nsi_brands keeps beside `tags`, among the items of the
-- object's QID and primary tag: items sharing both carry the same tags but may
-- carry different labels (Intermarché, Intermarché Drive), so the one nsi_match
-- picks is not the one to compare with. Nothing is proposed when two spellings
-- compete, or when the object already carries one NSI knows.
CREATE OR REPLACE FUNCTION nsi_case_fix(qid text, prim text[], label text, osm_value text)
RETURNS text AS $$
    SELECT NULLIF(CASE WHEN count(DISTINCT spelling) = 1 THEN min(spelling) END, osm_value)
    FROM (
        SELECT CASE label WHEN 'brand' THEN brand ELSE name END AS spelling
        FROM nsi_brands
        WHERE brand_wikidata = qid AND ARRAY[primary_key, primary_value] = prim
    ) items
    WHERE LOWER(spelling) = LOWER(osm_value);
$$ LANGUAGE sql STABLE SET search_path = public, pg_temp;

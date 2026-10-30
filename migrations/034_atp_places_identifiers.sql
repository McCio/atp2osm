-- Registry identifiers and stable source tags of an ATP POI, `{osm_tag: value}`
-- each (specs/07). atp_places is rebuilt by the pipeline, so a live table
-- predating the loader lacks both until the next ATP import: the empty map is
-- the tolerant value, "this POI carries nothing the configuration names".
ALTER TABLE IF EXISTS atp_places ADD COLUMN IF NOT EXISTS identifiers JSONB DEFAULT '{}'::jsonb;
ALTER TABLE IF EXISTS atp_places ADD COLUMN IF NOT EXISTS writable_tags JSONB DEFAULT '{}'::jsonb;

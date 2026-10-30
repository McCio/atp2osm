-- The aliases NSI lists to *recognise* an item (`matchNames`, specs/07 §7a).
-- Read only to recover the QID of an ATP row that names its brand without one;
-- nsi_match and nsi_case_fix never see them. nsi_brands is rebuilt by the
-- pipeline, so the empty array is the tolerant value until the next NSI import.
ALTER TABLE IF EXISTS nsi_brands ADD COLUMN IF NOT EXISTS match_names TEXT[] NOT NULL DEFAULT '{}';

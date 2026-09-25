# Eval

## Read

4/4 passing

- client_01_clean `sources_read` PASS
- client_02_medium `sources_read` PASS
- client_03_hard `sources_read` PASS
- client_04_stretch `sources_read` PASS

## Facts

4/4 passing

- client_01_clean `fact_shape` PASS
- client_02_medium `fact_shape` PASS
- client_03_hard `fact_shape` PASS
- client_04_stretch `fact_shape` PASS

## Section

20/20 passing

- client_01_clean `fca_line` PASS
- client_01_clean `risk_warning` PASS
- client_01_clean `action_items` PASS
- client_01_clean `no_pounds_in_background_summary` PASS
- client_01_clean `body_review_subset_footer` PASS
- client_02_medium `fca_line` PASS
- client_02_medium `risk_warning` PASS
- client_02_medium `action_items` PASS
- client_02_medium `no_pounds_in_background_summary` PASS
- client_02_medium `body_review_subset_footer` PASS
- client_03_hard `fca_line` PASS
- client_03_hard `risk_warning` PASS
- client_03_hard `action_items` PASS
- client_03_hard `no_pounds_in_background_summary` PASS
- client_03_hard `body_review_subset_footer` PASS
- client_04_stretch `fca_line` PASS
- client_04_stretch `risk_warning` PASS
- client_04_stretch `action_items` PASS
- client_04_stretch `no_pounds_in_background_summary` PASS
- client_04_stretch `body_review_subset_footer` PASS

## Story

12/12 passing

- client_01_clean `tax_iff_selling` PASS selling=False, tax_section_present=False
- client_01_clean `table_account_ids` PASS
- client_01_clean `conflicts_surfaced` PASS No account_value conflicts in facts.json
- client_02_medium `tax_iff_selling` PASS selling=True, tax_section_present=True
- client_02_medium `table_account_ids` PASS
- client_02_medium `conflicts_surfaced` PASS
- client_03_hard `tax_iff_selling` PASS selling=True, tax_section_present=True
- client_03_hard `table_account_ids` PASS
- client_03_hard `conflicts_surfaced` PASS
- client_04_stretch `tax_iff_selling` PASS selling=True, tax_section_present=True
- client_04_stretch `table_account_ids` PASS
- client_04_stretch `conflicts_surfaced` PASS

## Case

24/24 passing

- client_01_clean `narrative_not_action` PASS
- client_01_clean `action_amount_numeric` PASS
- client_01_clean `contingent_not_action` PASS
- client_01_clean `money_kinds_distinct` PASS
- client_01_clean `meeting_pounds_covered` PASS
- client_01_clean `selling_has_dispose` PASS
- client_02_medium `narrative_not_action` PASS
- client_02_medium `action_amount_numeric` PASS
- client_02_medium `contingent_not_action` PASS
- client_02_medium `money_kinds_distinct` PASS
- client_02_medium `meeting_pounds_covered` PASS
- client_02_medium `selling_has_dispose` PASS
- client_03_hard `narrative_not_action` PASS
- client_03_hard `action_amount_numeric` PASS
- client_03_hard `contingent_not_action` PASS
- client_03_hard `money_kinds_distinct` PASS
- client_03_hard `meeting_pounds_covered` PASS
- client_03_hard `selling_has_dispose` PASS
- client_04_stretch `narrative_not_action` PASS
- client_04_stretch `action_amount_numeric` PASS
- client_04_stretch `contingent_not_action` PASS
- client_04_stretch `money_kinds_distinct` PASS
- client_04_stretch `meeting_pounds_covered` PASS
- client_04_stretch `selling_has_dispose` PASS

## Usage

Estimated USD using the 2026-09-01 rate card. Not a billing record.

- client_01_clean.usage.json: 4 calls, 3311 prompt tokens, 693 completion tokens, pipeline 8703.0 ms, $0.0008
- client_02_medium.usage.json: 4 calls, 3504 prompt tokens, 694 completion tokens, pipeline 8295.3 ms, $0.0008
- client_03_hard.usage.json: 4 calls, 3892 prompt tokens, 877 completion tokens, pipeline 9810.2 ms, $0.0009
- client_04_stretch.usage.json: 4 calls, 5383 prompt tokens, 1188 completion tokens, pipeline 13136.3 ms, $0.0013

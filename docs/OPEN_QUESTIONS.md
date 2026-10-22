# Open questions for the AgTrail integration

Anything here is a guess or a gap that a person needs to settle. Nothing marked
UNVERIFIED should be shown to users as fact.

## What AgTrail sends today (production snapshot, 22 Oct 2026)

Counted from `farm_images` (106 photos) and `farms` (264 farms).

**Operation names on photos:** planting 32, land_preparation 24, harvesting 22,
irrigation 6, general 6, weeding 6, fertilizer 5, pest_control 4, pruning 1.

**Crop cycle status values:** completed 56, planted 18, planning 16,
growing 14, harvesting 2.

**Crop text on farms** is free text typed by users. 112 of 264 farms have no
crop at all. Spellings seen include `Maize`, `maizw`, `MaiE`, `Maize 2`,
`Soyabean`, `Soya Beans`, `Soya beans`, `Soybean`, `Soya`, `Soy beans`, plus farm
names typed into the crop box (`Tomato Farm`, `Yam Nusery`,
`Hayin Alkali 2 - Farm White Beans`) and a two-crop entry (`Maize and Cassava`).

## Decisions needed

| # | Question | Owner | Why it matters |
| --- | --- | --- | --- |
| 1 | AgTrail does log a real operation date (`operationDate`, required when an operation is created), but its webhook does not send it. `captured_at` is the time the record was saved, so an operation logged late is dated wrongly. The hub now accepts an optional `operation_date` in `operation_context` and uses it when present, otherwise the photo's date in Nigerian time (UTC+1, no daylight saving). Next: AgTrail's `webhookService.ts` must add `operation_date: op.operationDate`. | AgTrail backend (Victor) | Signals are dated by it. Until the backend change is deployed, dates are the photo's date. |
| 1b | AgTrail sends `general` when an operation has neither a category nor a type. So `general` means "unknown type", not a real operation. It is mapped to `other` for now. | AgTrail team | Decide whether such operations should appear on the map at all. |
| 2 | Approve the operation name mapping in `operation_type_aliases` (all UNVERIFIED). In particular: `fertilizer` becomes `fertilizer_application`; `irrigation`, `pruning` and `general` become `other`. | Agronomist | Decides which signal category each logged operation gets. |
| 3 | Soybean is the second most common crop (about 47 farms across 10 spellings) but has no Knowledge Base file. Same for potato, plantain, sugarcane and finger millet. | Agronomist, Knowledge Base team | Without a crop file there are no rules, no due dates and no yield range for these farms. PIS does have a soybean price series. |
| 4 | `white beans` is mapped to no Knowledge Base crop. Is it cowpea? | Agronomist | The Knowledge Base has cowpea only. |
| 5 | `Maize and Cassava` (and any other two-crop text) is left unmapped. Should a cycle be allowed to have two crops? | AgTrail team | One cycle currently maps to one crop. |
| 6 | The crop box in AgTrail is free text. Should it become a list of crops, with farm names kept in the farm-name field? | AgTrail frontend (Victor) | 112 farms have no crop and the rest need alias rows to be understood. |
| 7 | Cycle status values are `planning`, `planted`, `growing`, `harvesting`, `completed`. Is that the full list? | AgTrail team | Stored as text for now. |

## Aliases to review

`operation_type_aliases` and `crop_aliases` are seeded by the migration with
every row marked `UNVERIFIED`. Review them with:

```sql
SELECT * FROM operation_type_aliases ORDER BY logged_type;
SELECT * FROM crop_aliases ORDER BY canonical_name, alias;
```

When a name appears that has no alias, the operation is still saved with an
empty `signal_category` (and the crop with an empty `crop_id`), and the backfill
job lists them. Add a row to the alias table to map it. Nothing needs a deploy.

## Deploy notes

1. Run the migration: `alembic upgrade head` (reversible: `alembic downgrade c3d4e5f6a7b8`).
2. Fill in the older photos once: `make backfill-operations`. It is safe to run again.
3. Neon is at 80% of its monthly compute allowance, so run the backfill once
   rather than repeatedly. It reads 106 photos and writes a few hundred rows.

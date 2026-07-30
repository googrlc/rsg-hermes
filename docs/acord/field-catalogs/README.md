# ACORD field catalogs

Full AcroForm field inventories extracted from RSG's **licensed** ACORD
templates. Each JSON maps a fully-qualified field name → its field type
(`/Tx` text, `/Btn` checkbox/radio). These are the **reconciliation source** for
the fillers in `hermes/deliverables/acord125.py`, `acord126.py`, and
`acord140.py`.

| File | Template | Fields |
|---|---|---|
| `acord_125_126.fields.json` | Combined ACORD 125 (application) + 126 (GL), 8 pages | 864 |
| `acord_140_property.fields.json` | ACORD 140 (property) | 185 |
| `acord_140_details.fields.json` | ACORD 140 details page | 166 |
| `acord_140_signature.fields.json` | ACORD 140 signature page | 7 |

## Why these exist

ACORD PDFs nest widgets under a `/Parent` hierarchy, so `pypdf.get_fields()`
surfaces only a small subset (93 of 864 on the 125/126). The real, fully-qualified
names — e.g. `F[0].P1[0].NamedInsured_FullName_A[0]` — come from walking each
page's widget annotations (`acord_pdf.all_field_names`). The fillers target those
names; `fill_pdf` skips and reports any name not present, so a different template
revision degrades to a partial draft rather than nothing.

## How to use

- The fillers hard-code the field names for the subset the intake
  `SubmissionObject` can populate. To map more fields, look them up here.
- To reconcile against a **different** licensed template revision, run
  `acord_pdf.all_field_names(<template.pdf>)`, diff against the relevant catalog,
  and supply corrections via the `HERMES_ACORD125_FIELDMAP` /
  `HERMES_ACORD126_FIELDMAP` / `HERMES_ACORD140_FIELDMAP` json override — no code
  change required.

## Note

The **templates themselves are copyrighted** and are NOT committed. Only these
field-name inventories (facts about the form structure) live here. Pull the
licensed PDFs from NowCerts / agency files at deploy time and point the fillers
at them.

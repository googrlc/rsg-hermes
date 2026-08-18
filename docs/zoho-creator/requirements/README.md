# Requirements package for Zia (PRD / BRD / RFP / diagrams)

Zia can attach **PRD, BRD, RFP, or process diagrams**. Use these four PDFs:

| Zia attach slot | File |
|-----------------|------|
| PRD | `PRD_RSG_Policy_Reconciliation.pdf` |
| BRD | `BRD_RSG_Policy_Reconciliation.pdf` |
| RFP | `RFP_RSG_Policy_Reconciliation.pdf` |
| Process diagrams | `PROCESS_DIAGRAMS_RSG_Policy_Reconciliation.pdf` |

If the dialog still only takes spreadsheets, upload `ZIA_REQUIREMENTS.xlsx` (PRD/BRD/RFP as sheets) plus `ZIA_UPLOAD.xlsx` (fields and Deluge).

Markdown sources in this folder are for git. Rebuild PDFs:

```bash
python3 docs/zoho-creator/scripts/render_requirements.py
```

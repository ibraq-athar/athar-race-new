# سباق أثر — Athar race board

Public board: https://ibraq-athar.github.io/athar-race-new/

GitHub Actions (`.github/workflows/update-board.yml`) rebuilds `index.html` from the Odoo BI API about
every 10 minutes and publishes it with GitHub Pages. The page shows percentages, ranks and employee
names only — never sales amounts or targets.

The API address, API token and the monthly target file are **repository secrets** (never files here):
`ODOO_API_URL`, `ODOO_API_TOKEN`, `TARGET_XLSX_B64`. Update `TARGET_XLSX_B64` every month.

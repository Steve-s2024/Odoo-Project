"""Restore the two curated sales dashboard payloads from validated snapshots.

Run with the Odoo shell.  Snapshot paths are intentionally fixed so the
deployment wrapper can stage them without accepting arbitrary filesystem input.
"""

from hashlib import sha256
import json
from pathlib import Path


snapshots = {
    "/tmp/internal-sales-dashboard.json": (
        "spreadsheet_dashboard_sale.spreadsheet_dashboard_sales"
    ),
    "/tmp/external-sales-dashboard.json": (
        "stock_subwarehouse_hierarchy.spreadsheet_dashboard_external_sales"
    ),
}

for path, xmlid in snapshots.items():
    payload = Path(path).read_text(encoding="utf-8")
    json.loads(payload)
    record = env.ref(xmlid)
    before = record.spreadsheet_data or ""
    print(
        "DASHBOARD_BEFORE|%s|%s|%s"
        % (xmlid, len(before), sha256(before.encode()).hexdigest())
    )
    record.write({"spreadsheet_data": payload})
    print(
        "DASHBOARD_AFTER|%s|%s|%s"
        % (xmlid, len(payload), sha256(payload.encode()).hexdigest())
    )

env.cr.commit()

#!/usr/bin/env python3
"""Re-grant internet access to still-active vouchers after a reboot.

The `voucher_allow` ipset lives only in kernel memory, so a reboot clears
it even though a voucher's expires_at in the database may still be in the
future. Run this once at boot (see cybersafe-reconcile.service) to bring
the firewall back in sync with the database.
"""
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import network_access  # noqa: E402  (path insert must happen first)

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database", "cybersafe.db"
)
DB_PATH = os.environ.get("CYBERSAFE_DB_PATH", DEFAULT_DB_PATH)


def main():
    if not os.path.exists(DB_PATH):
        print(f"No database at {DB_PATH}, nothing to reconcile.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT mac_address, expires_at FROM vouchers
           WHERE used_at IS NOT NULL AND expires_at IS NOT NULL
             AND mac_address IS NOT NULL"""
    ).fetchall()
    conn.close()

    now = datetime.utcnow()
    granted = 0
    for row in rows:
        try:
            expires = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue
        remaining = (expires - now).total_seconds()
        if remaining <= 0:
            continue
        if network_access.grant_internet_access(row["mac_address"], int(remaining)):
            granted += 1

    print(f"Reconciled {granted} active voucher(s) out of {len(rows)} checked.")


if __name__ == "__main__":
    main()

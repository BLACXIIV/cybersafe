#!/usr/bin/env python3
"""Tail the dnsmasq query log and record domains visited by voucher holders.

network/dnsmasq-ap.conf sets `log-queries=extra` and
`log-facility=/var/log/cybersafe-dns.log`. With =extra every log line for a
query carries a serial number and the requestor's ip/port:

    Jun 14 17:38:40 dnsmasq[30831]: 4 10.8.0.2/36989 query[A] google.de from 10.8.0.2
    Jun 14 17:38:40 dnsmasq[30831]: 4 10.8.0.2/36989 forwarded google.de to 127.0.0.1
    Jun 14 17:38:40 dnsmasq[30831]: 4 10.8.0.2/36989 reply google.de is 172.217.22.99

Only the `query[...]` line is needed. Forwarded/reply/cached lines describe
the same lookup. The source IP is matched against the `vouchers` table to
find which student owns the device. Runs continuously under
cybersafe-site-visits.service.
"""
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database", "cybersafe.db"
)
DB_PATH = os.environ.get("CYBERSAFE_DB_PATH", DEFAULT_DB_PATH)
DNS_LOG_PATH = os.environ.get("CYBERSAFE_DNS_LOG", "/var/log/cybersafe-dns.log")

POLL_SECONDS = 0.5
REOPEN_WAIT_SECONDS = 2
THROTTLE_SECONDS = 60  # one row per (user, domain) per window; a page load fires many repeat lookups
SESSION_GAP_SECONDS = 300  # quiet this long and the visit session is over (DNS sees no "leave" event)

# Page-load record types only: PTR/SRV/TXT/DNSKEY are resolver chatter, and
# HTTPS is the TLS-hint query browsers send alongside A/AAAA.
RECORD_TYPES = {"A", "AAAA", "HTTPS"}

# Noise floor, suffix-matched (www.doubleclick.net -> doubleclick.net): ads,
# analytics, OS connectivity probes, and the portal itself. Starter list:
# extend it when new junk shows up in the admin views.
DOMAIN_DENYLIST = (
    "cybersafe.local",
    "captive.apple.com",
    "connectivitycheck.gstatic.com",
    "msftconnecttest.com",
    "clients3.google.com",
    "doubleclick.net",
    "google-analytics.com",
    "googletagmanager.com",
    "googlesyndication.com",
    "googleadservices.com",
    "facebook.net",
    "scorecardresearch.com",
    "telemetry.mozilla.org",
    "data.microsoft.com",
)

# The (serial ip/port) prefix only exists under log-queries=extra; keeping it
# optional lets this also read a plain log-queries file.
QUERY_RE = re.compile(
    r"^(?P<month>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2})\s+dnsmasq(?:\[\d+\])?:\s+"
    r"(?:(?:\d+|\*)\s+\S+/\d+\s+)?"
    r"query\[(?P<rtype>[A-Z0-9]+)\]\s+(?P<domain>\S+)\s+from\s+(?P<client>\S+)"
)

_MONTHS = {
    name: number
    for number, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}


def _log_timestamp(match):
    """Parse the syslog-style stamp (no year, local time) into UTC, matching
    the CURRENT_TIMESTAMP convention used everywhere else in the database."""
    month = _MONTHS.get(match["month"])
    if month is None:
        return datetime.now(timezone.utc)
    now = datetime.now()
    hour, minute, second = (int(part) for part in match["time"].split(":"))
    stamp = datetime(now.year, month, int(match["day"]), hour, minute, second)
    if stamp - now > timedelta(days=1):
        stamp = stamp.replace(year=now.year - 1)  # Dec 31 line read on Jan 1
    return stamp.astimezone(timezone.utc)


def _is_denied(domain):
    return any(domain == d or domain.endswith("." + d) for d in DOMAIN_DENYLIST)


def _update_session(conn, user_id, domain, stamp):
    """Upsert the open session for (user, domain): a lookup within
    SESSION_GAP_SECONDS extends it, anything later starts a new one.
    Runs on every matched query — unlike the throttled site_visits log —
    so last_seen_at tracks real activity."""
    stamp_str = stamp.strftime("%Y-%m-%d %H:%M:%S")
    sess = conn.execute(
        """SELECT id, last_seen_at FROM site_sessions
           WHERE user_id = ? AND domain = ?
           ORDER BY last_seen_at DESC LIMIT 1""",
        (user_id, domain),
    ).fetchone()
    if sess is not None:
        last = datetime.strptime(sess["last_seen_at"], "%Y-%m-%d %H:%M:%S")
        if stamp - last <= timedelta(seconds=SESSION_GAP_SECONDS):
            conn.execute(
                "UPDATE site_sessions SET last_seen_at = MAX(last_seen_at, ?), lookups = lookups + 1 WHERE id = ?",
                (stamp_str, sess["id"]),
            )
            return
    conn.execute(
        "INSERT INTO site_sessions (user_id, domain, started_at, last_seen_at) VALUES (?, ?, ?, ?)",
        (user_id, domain, stamp_str, stamp_str),
    )


def _record_line(conn, line, last_logged):
    try:
        match = QUERY_RE.match(line)
        if match is None or match["rtype"] not in RECORD_TYPES:
            return
        domain = match["domain"].rstrip(".").lower()
        if not domain or _is_denied(domain):
            return
        voucher = conn.execute(
            """SELECT user_id FROM vouchers
               WHERE ip_address = ? AND used_at IS NOT NULL
                 AND (expires_at IS NULL OR expires_at > datetime('now'))
               ORDER BY used_at DESC
               LIMIT 1""",
            (match["client"],),
        ).fetchone()
        if voucher is None:
            return
        stamp = _log_timestamp(match).replace(tzinfo=None)  # naive UTC, matching stored text
        _update_session(conn, voucher["user_id"], domain, stamp)
        key = (voucher["user_id"], domain)
        now = time.monotonic()
        do_visit = now - last_logged.get(key, -THROTTLE_SECONDS) >= THROTTLE_SECONDS
        if do_visit:
            conn.execute(
                "INSERT INTO site_visits (user_id, domain, visited_at) VALUES (?, ?, ?)",
                (voucher["user_id"], domain, stamp.strftime("%Y-%m-%d %H:%M:%S")),
            )
        conn.commit()
        if do_visit:
            last_logged[key] = now
    except sqlite3.Error:
        # A locked/busy DB drops the line instead of killing the daemon.
        conn.rollback()


def _open_log(path):
    log = open(path, "r", errors="replace")
    log.seek(0, os.SEEK_END)
    return log


def _rotated(log, path):
    """True if `path` now names a different file (logrotate) or was truncated."""
    try:
        current = os.stat(path)
        opened = os.fstat(log.fileno())
    except OSError:
        return True
    return current.st_ino != opened.st_ino or current.st_size < log.tell()


def main():
    # Exit 0 here would NOT be retried: the unit is Restart=on-failure, so a
    # clean exit on boot (DB not created yet) stopped the daemon for good.
    if not os.path.exists(DB_PATH):
        print(f"Waiting for database at {DB_PATH}", flush=True)
    while not os.path.exists(DB_PATH):
        time.sleep(REOPEN_WAIT_SECONDS)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    log = None
    last_logged = {}
    print(f"Watching {DNS_LOG_PATH} -> {DB_PATH}", flush=True)
    while True:
        if log is None:
            try:
                log = _open_log(DNS_LOG_PATH)
            except OSError:
                time.sleep(REOPEN_WAIT_SECONDS)  # dnsmasq may not have logged yet
                continue
        pos = log.tell()
        line = log.readline()
        if not line.endswith("\n"):
            # EOF or a half-written line: wait for the rest of it (or for the
            # file to be rotated out from under us).
            log.seek(pos)
            if _rotated(log, DNS_LOG_PATH):
                log.close()
                log = None
                continue
            time.sleep(POLL_SECONDS)
            continue
        _record_line(conn, line, last_logged)


if __name__ == "__main__":
    main()

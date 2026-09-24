# Working plan

## Current goal: site visits on the admin Activity page

Show which sites (domains) each student visits while their voucher is
active, near-real-time, on `/admin/activity`.

Pipeline: student device -> dnsmasq on the Pi logs every DNS query to
`/var/log/cybersafe-dns.log` -> `network/log_site_visits.py` tails it,
matches the query source IP to `vouchers.ip_address` of an active
voucher -> inserts `site_visits` rows -> `/admin/activity` lists them.

### Done

- Activity page: Refresh button + auto-refresh every 10s (keeps search,
  period filter, and page; skips while tab hidden or typing in search).
- Activity page: "Live now" panel — every student holding an active
  voucher, shown with their current session: domain + running duration +
  start time ("12m · ongoing since Sep 24, 14:32"), or ended sessions as
  "4m · ended Sep 24, 14:44". Green dot while active, greyed once quiet
  >5 min (honest staleness; DNS never sees a "leave").
- New `site_sessions` table (schema.sql + ensure_admin_data): the logger
  upserts one row per (student, domain) activity window — started_at,
  last_seen_at, lookup count — on every matched query, independent of the
  60s site_visits throttle. This is what makes "time spent" persistable.
- `log_site_visits.py` now waits for the DB instead of exiting cleanly —
  a clean exit was never retried by `Restart=on-failure`, a likely cause
  of total silence.

### Remaining — get capture working on the Pi

1. `sudo systemctl status cybersafe-site-visits` — is it running/enabled?
   If missing: copy `network/systemd/cybersafe-site-visits.service` to
   `/etc/systemd/system/`, `daemon-reload`, `enable --now`.
2. `tail -f /var/log/cybersafe-dns.log` while browsing on a test device —
   if no file/no lines, the installed `/etc/dnsmasq.d/cybersafe-ap.conf`
   predates the logging options: copy `network/dnsmasq-ap.conf` over and
   `sudo systemctl restart dnsmasq`.
3. Verify attribution: `vouchers.ip_address` must equal the `from <ip>`
   in the DNS log and `expires_at` must be in the future (expired voucher
   = no logging, by design).
4. `git pull` the fixed `log_site_visits.py` on the Pi, restart the
   service, then end-to-end check: redeem voucher on a test device,
   browse, row appears in <=10s.

### Parked (decided not to build now)

- SNI capture via tshark on wlan0 (covers Private-DNS/DoH bypass).
- VPN-use detection: flag "vouchered device, sustained traffic, zero DNS,
  single datacenter-ASN destination". If wanted later: `suspended_until`
  column on users (pattern already exists via `cooldown_until`) +
  `revoke_internet_access`. Flag-only first — auto-block has false
  positives. "Server not in the Philippines" as the trigger was rejected:
  nearly all legit sites (YouTube, Facebook, TikTok, Cloudflare-hosted
  PH sites) are hosted abroad, so it would ban everyone.

### Earlier fixes this session

- Reconnect hook no longer clears sessions at all: it only redirects
  foreign-Host GETs to PORTAL_BASE_URL (any port-80 traffic is DNAT'd to
  Flask, so host-based reconnect detection kicked students mid-use).

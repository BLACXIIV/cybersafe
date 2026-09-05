# Making vouchers grant/block real internet access

This layers a real captive portal on top of the app from `DEPLOYMENT.md`.
Do that page first — this assumes the app already runs correctly over
ethernet and you can log in as a student and earn a voucher.

## How it works

```
Students' phones/laptops                 Your router / the internet
        |  WiFi                                    |
        |  (10.42.0.0/24)                           | eth0 (existing uplink,
        v                                            |  DHCP from your router)
   +-------------------------------------------------------+
   |                     Raspberry Pi                       |
   |  wlan0: hostapd (AP) + dnsmasq (DHCP/DNS)              |
   |  Flask app (gunicorn) on 0.0.0.0:8000                  |
   |  iptables: wlan0->eth0 traffic DROPPED unless the      |
   |    device's MAC is in the "voucher_allow" ipset        |
   +-------------------------------------------------------+
```

- Students join the Pi's own WiFi network (not your school WiFi) and get an
  IP from the Pi (10.42.0.x). The dashboard is reachable at that address
  whether or not their voucher is active — the gate only affects traffic
  headed *past* the Pi, out to eth0.
- When a student redeems a voucher, Flask looks up their device's MAC
  address from the Pi's own ARP table (no manual entry needed) and tells the
  firewall to allow that MAC through for 5 hours. It comes back out
  automatically — no cleanup job needed, the underlying `ipset` entry expires
  itself.
- Turning internet access off in the app (or the voucher's 5 hours running
  out) removes the MAC from the allow-list, and their traffic to eth0 is
  dropped again immediately.
- The Flask app itself never runs as root. It calls one tightly-scoped root
  helper script via `sudo`, and that script does nothing except add/remove a
  MAC address from the firewall's allow-list.

## What you need

- A Raspberry Pi 4 (built-in WiFi chip is used as the access point — no USB
  WiFi adapter required).
- Your existing ethernet connection to a router with real internet access
  (from `DEPLOYMENT.md`) stays exactly as it is; that's `eth0`, your uplink.
- Raspberry Pi OS Bookworm or newer (uses NetworkManager by default — this
  setup tells NetworkManager to ignore wlan0 so hostapd/dnsmasq can run it
  directly, which avoids fighting over DHCP/DNS).

## Step 1 — Pick a WiFi name and password

```bash
cd ~/cybersafe
nano network/hostapd.conf
```
Change `ssid=` and `wpa_passphrase=` to real values (8+ characters for the
passphrase). Leave everything else as-is unless you know you need to.

## Step 2 — Run the setup script

```bash
sudo bash network/setup_ap.sh
```

This installs `hostapd`, `dnsmasq`, `ipset`, and `iptables-persistent`;
configures a static IP (10.42.0.1) on wlan0; sets up DHCP/DNS for connecting
devices; enables IP forwarding; creates the firewall gate; and enables
everything to survive a reboot. It prints a summary and some status commands
when it's done.

## Step 3 — Connect and test

1. From a phone or laptop, join the WiFi network you named in Step 1.
2. Browse to `http://cybersafe.local:8000` (or `http://10.42.0.1:8000`) —
   the login page should load. This works whether or not you have an active
   voucher; only traffic leaving toward the real internet is gated.
3. Try loading any other website (e.g. `example.com`) — it should **fail to
   load**. That's the gate working.
4. Log in as a student, complete a mission, and redeem the voucher on
   `/internet-access` or the level's `/connect` page.
5. Reload `example.com` — it should now load. Check the flash message; if it
   says the voucher activated but you still can't reach the internet, see
   Troubleshooting below.
6. Turn internet access off in the app (or wait 5 hours) — `example.com`
   should stop loading again.

## Reference: what got installed where

| File in this repo | Installed to | Purpose |
| --- | --- | --- |
| `network/hostapd.conf` | `/etc/hostapd/hostapd.conf` | WiFi SSID/password |
| `network/dnsmasq-ap.conf` | `/etc/dnsmasq.d/cybersafe-ap.conf` | DHCP + DNS for wlan0 |
| `network/cybersafe-grant-access` | `/usr/local/sbin/cybersafe-grant-access` | Root helper the app calls via sudo |
| `network/sudoers-cybersafe` | `/etc/sudoers.d/cybersafe` | Lets `pi` run *only* that helper as root |
| `network/systemd/*.service` | `/etc/systemd/system/` | Static IP, ipset creation, boot reconciliation |
| `network/systemd/*-override.conf` | `/etc/systemd/system/{hostapd,netfilter-persistent}.service.d/override.conf` | Correct startup ordering |

The app's own changes (already applied in this repo):
- `vouchers` table gained `mac_address` and `ip_address` columns.
- `network_access.py` looks up a device's MAC and calls the sudo helper.
- `levels.py` (`_activate_voucher` / `_deactivate_voucher`) is now the single
  place that flips a voucher on/off and opens/closes the gate; `main.py`'s
  two internet-access routes and `levels.connect()` all call it instead of
  updating the database directly.

## Changing the voucher duration

It's 5 hours in three places, by design — grep for `+5 hours` (SQL) and
`VOUCHER_DURATION_SECONDS` (Python) in `levels.py`, `main.py`, and update all
of them together.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| WiFi network doesn't show up | `sudo systemctl status hostapd` — often a config typo; `sudo journalctl -u hostapd -n 50` |
| Devices can't get an IP | `sudo systemctl status dnsmasq`; confirm nothing else (NetworkManager) is also trying to manage wlan0: `nmcli device status` should show wlan0 as `unmanaged` |
| App loads but internet never unblocks after redeeming | Check the app's logs for "firewall grant failed" — usually means `setup_ap.sh` wasn't run, or `sudo -n` isn't working. Test manually: `sudo -n /usr/local/sbin/cybersafe-grant-access grant AA:BB:CC:DD:EE:FF 60` as the `pi` user |
| MAC not found / access never opens | The lookup only works for devices that are actually on the AP subnet (10.42.0.x) talking through the Pi — testing from the Pi itself (`localhost`) won't have an ARP entry. Test from a real phone/laptop on the WiFi |
| Internet works for everyone regardless of voucher | Check the gate exists and is first in FORWARD: `sudo iptables -L FORWARD -v --line-numbers` should show `CYBERSAFE_GATE` at line 1 for `wlan0 -> eth0` |
| Access doesn't expire after 5 hours | `sudo ipset list voucher_allow` — entries show a `timeout` countdown; if it shows `0`/no timeout, the grant call didn't pass a duration correctly |
| Rules gone after a reboot | `sudo systemctl status cybersafe-ipset cybersafe-reconcile netfilter-persistent` — the ipset must exist *before* the saved iptables rules are restored, which is what the override units in `network/systemd/` enforce |

## Security notes

- The helper script and sudoers rule are the only privilege-escalation path
  in this whole feature. Don't widen the sudoers rule or add arguments the
  script doesn't strictly validate.
- This gate only affects **wlan0 → eth0** forwarding. It doesn't stop
  connected devices from talking to each other on the AP subnet, and it
  doesn't inspect or filter what a device does once it's through the gate
  (no content filtering). If the school needs that, it's a separate,
  larger project (e.g. a filtering DNS resolver or a transparent proxy).
- Same reminder as `DEPLOYMENT.md`: change the default admin password
  (`admin`/`admin`) before this touches a real network.

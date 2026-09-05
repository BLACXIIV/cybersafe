# Deploying Cyber-S.A.F.E. to a Raspberry Pi

Target setup: Raspberry Pi connected by **wired ethernet**, serving the Flask app
to other devices on the same local network.

**Do I need the WiFi SSID and password?** No. With a wired ethernet connection the
Pi gets its network settings automatically over DHCP. You only need WiFi
credentials if the Pi has to *join* a wireless network, or *broadcast* its own
hotspot (see [Not yet implemented](#not-yet-implemented)).

## Requirements

- Raspberry Pi (3B+ or newer recommended) + power supply
- SD card, 16 GB recommended
- Ethernet cable and a free port on your router or switch
- SD card reader on your PC

---

## Step 0 — Push your current work

Cloning from GitHub is the simplest way to get code onto the Pi, so commit and
push anything outstanding first.

```powershell
cd C:\Users\argie\OneDrive\Documents\cybersafe
git add -A
git commit -m "Prepare for Raspberry Pi deployment"
git push origin main
```

## Step 1 — Flash the SD card

1. Install **Raspberry Pi Imager** from <https://www.raspberrypi.com/software/>.
2. Insert the SD card.
3. In Imager, choose:
   - **Device:** your Pi model
   - **OS:** *Raspberry Pi OS (64-bit)* — use **Lite** for SSH-only, or the
     Desktop version if you want a monitor and keyboard attached
   - **Storage:** your SD card
4. Click **Next → Edit Settings** and configure:
   - Hostname: `cybersafe`
   - Enable SSH → *Use password authentication*
   - Username `pi`, plus a real password
   - **Leave the WiFi section blank** — this deployment uses ethernet
   - Set your locale and timezone
5. Save → Write, and let it verify.

## Step 2 — Boot and connect

Insert the card, plug in the ethernet cable, then power on. Wait about a minute,
then from your PC:

```powershell
ssh pi@cybersafe.local
```

If `cybersafe.local` does not resolve, look up the Pi's address in your router's
DHCP client list and use `ssh pi@192.168.x.x` instead.

## Step 3 — Install system packages

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git python3-venv python3-pip
```

## Step 4 — Clone the app and install dependencies

```bash
cd ~
git clone https://github.com/BLACXIIV/cybersafe.git
cd cybersafe
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install gunicorn
```

Gunicorn is not in `requirements.txt` but is required here. `app.py` starts the
Flask development server with `debug=True`, which exposes an interactive
debugger and must never be reachable from a shared network.

## Step 5 — Generate a secret key

`config.py` falls back to the placeholder `"dev-secret-change-me"`, so create a
real one and keep the output for the next step:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

## Step 6 — Test run

```bash
cd ~/cybersafe
source .venv/bin/activate
gunicorn --bind 0.0.0.0:8000 app:app
```

From another device on the same network open <http://cybersafe.local:8000>.
Once it loads, stop the server with `Ctrl+C`.

If you hit a database error, initialise it once with
`flask --app app init-db`. This **wipes existing data**, so only run it on a
fresh install.

## Step 7 — Run automatically on boot

```bash
sudo nano /etc/systemd/system/cybersafe.service
```

Paste the following, replacing `PASTE_YOUR_KEY_HERE` with the key from Step 5:

```ini
[Unit]
Description=Cyber-S.A.F.E. Flask App
After=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/cybersafe
Environment="CYBERSAFE_SECRET_KEY=PASTE_YOUR_KEY_HERE"
ExecStart=/home/pi/cybersafe/.venv/bin/gunicorn --workers 2 --bind 0.0.0.0:8000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

Save with `Ctrl+O`, `Enter`, `Ctrl+X`, then enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cybersafe
sudo systemctl status cybersafe
```

Keep `--workers 2`. SQLite serialises writes, so more workers increases the risk
of `database is locked` errors rather than improving throughput.

## Step 8 — Pin the Pi's address

DHCP can hand the Pi a different IP after a reboot, breaking saved links. Set a
**DHCP reservation / static lease** in your router for the Pi's MAC address:

```bash
ip link show eth0
```

## Step 9 — Secure it

- **Change the default administrator password.** `/login` currently accepts
  `admin` / `admin`.
- Back up the database regularly:

  ```bash
  cp ~/cybersafe/database/cybersafe.db ~/backup-$(date +%F).db
  ```

- Consider adding CSRF protection and rate limiting (see the security notes in
  `README.md`) before this runs on a real school network.

## Updating after code changes

```bash
cd ~/cybersafe && git pull && sudo systemctl restart cybersafe
```

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `cybersafe.local` won't resolve | Use the raw IP from the router's client list |
| Service won't start | `sudo journalctl -u cybersafe -n 50 --no-pager` |
| Page loads on Pi but not other devices | Confirm gunicorn binds `0.0.0.0`, not `127.0.0.1` |
| `database is locked` | Reduce `--workers`, confirm only one service instance runs |
| Changes not appearing | `sudo systemctl restart cybersafe` after `git pull` |

## Real internet gating (captive portal)

The voucher system now does grant and block real internet access, not just a
flag in SQLite — see **[NETWORK_SETUP.md](NETWORK_SETUP.md)**. That's a
separate, bigger step on top of everything above: it turns the Pi into a WiFi
access point and a gateway that only forwards a device's traffic to the
internet while its voucher is active. Do the ethernet-only setup on this page
first, confirm the app itself works, and only then move on to
`NETWORK_SETUP.md`.

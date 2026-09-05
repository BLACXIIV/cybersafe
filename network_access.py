"""Bridge between the Flask app (unprivileged) and the Pi's firewall
(root-only) for the captive-portal internet gating feature.

How it fits together
---------------------
- The Pi's wlan0 interface is the school's access point. Every connected
  device shows up in the kernel's ARP table once it has talked to the Pi,
  so we can turn "the student sitting at 10.42.0.73" into a MAC address
  without asking them for it.
- Real internet access is controlled by an ipset named ``voucher_allow``.
  ``network/setup_ap.sh`` sets up an iptables rule that only forwards
  wlan0 -> eth0 traffic for MAC addresses in that set; everything else is
  dropped.
- gunicorn runs as an unprivileged user, so it cannot touch ipset/iptables
  directly. Instead it shells out to a tightly-scoped root helper,
  ``/usr/local/sbin/cybersafe-grant-access``, via a NOPASSWD sudoers rule
  that only allows that one script (see network/sudoers-cybersafe).

On a laptop with no sudoers rule and no ipset installed, `sudo -n` simply
fails fast and these functions return False — the rest of the app (quizzes,
vouchers, points) keeps working normally for local development.
"""
import re
import subprocess

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
GRANT_SCRIPT = "/usr/local/sbin/cybersafe-grant-access"


def get_mac_for_ip(ip_address):
    """Look up the MAC address currently associated with ip_address in the
    kernel ARP table (/proc/net/arp). Returns a lowercase MAC string, or
    None if it's not there (e.g. not on the AP subnet, or Linux-only
    feature unavailable on this OS)."""
    if not ip_address:
        return None
    try:
        with open("/proc/net/arp") as f:
            next(f, None)  # header line
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[0] == ip_address:
                    mac = parts[3].lower()
                    if mac != "00:00:00:00:00:00" and MAC_RE.match(mac):
                        return mac
    except OSError:
        pass
    return None


def _run_helper(*args):
    try:
        result = subprocess.run(
            ["sudo", "-n", GRANT_SCRIPT, *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def grant_internet_access(mac_address, seconds):
    """Allow mac_address through the FORWARD chain for `seconds` seconds.
    ipset expires the entry on its own after that, no cleanup job needed."""
    if not mac_address or not MAC_RE.match(mac_address):
        return False
    seconds = int(seconds)
    if seconds <= 0:
        return False
    return _run_helper("grant", mac_address, str(seconds))


def revoke_internet_access(mac_address):
    """Remove mac_address from the allow-list immediately (manual disconnect)."""
    if not mac_address or not MAC_RE.match(mac_address):
        return False
    return _run_helper("revoke", mac_address)

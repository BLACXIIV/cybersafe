"""Bridge between the Flask app (unprivileged) and the Pi's firewall
(root-only) for the captive-portal internet gating feature.

How it fits together
---------------------
- The Pi's wlan0 interface is the school's access point. dnsmasq hands
  each connected device a DHCP lease, so the caller's IP address
  (request.remote_addr) is a stable identity for the duration of a
  voucher.
- Real internet access is controlled by an ipset named ``voucher_allow``.
  ``network/setup_ap.sh`` sets up an iptables rule that only forwards
  wlan0 -> eth0 traffic for IP addresses in that set; everything else is
  dropped.

  NOTE: this was originally MAC-address based (hash:mac), but Raspberry
  Pi OS's stock kernel doesn't ship the ip_set_hash_mac module, so this
  was switched to hash:ip / IP-address gating instead. Equivalent
  security for this use case, since every device on the AP subnet gets
  its own DHCP-assigned IP (no NAT between students and the Pi).
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

IP_RE = re.compile(
    r"^(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)"
    r"(\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)){3}$"
)
GRANT_SCRIPT = "/usr/local/sbin/cybersafe-grant-access"


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


def grant_internet_access(ip_address, seconds):
    """Allow ip_address through the FORWARD chain for `seconds` seconds.
    ipset expires the entry on its own after that, no cleanup job needed."""
    if not ip_address or not IP_RE.match(ip_address):
        return False
    seconds = int(seconds)
    if seconds <= 0:
        return False
    return _run_helper("grant", ip_address, str(seconds))


def revoke_internet_access(ip_address):
    """Remove ip_address from the allow-list immediately (manual disconnect)."""
    if not ip_address or not IP_RE.match(ip_address):
        return False
    return _run_helper("revoke", ip_address)

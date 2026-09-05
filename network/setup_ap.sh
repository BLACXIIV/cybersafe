#!/bin/bash
# Sets up wlan0 as the school's access point and wires it to a real,
# per-device internet gate that the Flask app can open/close per voucher.
#
# Run ONCE on the Pi as root, from inside the cloned repo:
#   cd ~/cybersafe
#   sudo bash network/setup_ap.sh
#
# Prerequisites:
#   - Edit network/hostapd.conf first (ssid / wpa_passphrase).
#   - eth0 is already your uplink to the internet (per DEPLOYMENT.md).
#   - The Cyber-S.A.F.E. app itself is already installed under ~pi/cybersafe
#     (adjust APP_USER/APP_DIR below if you used a different user/path).
set -euo pipefail

APP_USER="pi"
APP_DIR="/home/pi/cybersafe"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "Run this with sudo: sudo bash network/setup_ap.sh" >&2
    exit 1
fi

echo "==> Installing packages (hostapd, dnsmasq, ipset, iptables-persistent)"
export DEBIAN_FRONTEND=noninteractive
apt update
apt install -y hostapd dnsmasq ipset iptables-persistent netfilter-persistent

echo "==> Stopping services while we configure them"
systemctl stop hostapd dnsmasq || true

echo "==> Telling NetworkManager to leave wlan0 alone (hostapd/dnsmasq own it instead)"
mkdir -p /etc/NetworkManager/conf.d
cat >/etc/NetworkManager/conf.d/unmanaged-wlan0.conf <<'EOF'
[keyfile]
unmanaged-devices=interface-name:wlan0
EOF

echo "==> Installing hostapd config"
install -m 644 "$REPO_DIR/network/hostapd.conf" /etc/hostapd/hostapd.conf
if grep -q '^ChangeThisPassword123$' /etc/hostapd/hostapd.conf 2>/dev/null || \
   grep -q 'wpa_passphrase=ChangeThisPassword123' /etc/hostapd/hostapd.conf; then
    echo "    WARNING: you're using the default WiFi password from the template."
    echo "    Edit network/hostapd.conf and re-run this script before going live."
fi
if ! grep -q '^DAEMON_CONF=' /etc/default/hostapd 2>/dev/null; then
    echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' >>/etc/default/hostapd
fi
systemctl unmask hostapd

echo "==> Installing dnsmasq config"
install -m 644 "$REPO_DIR/network/dnsmasq-ap.conf" /etc/dnsmasq.d/cybersafe-ap.conf

echo "==> Installing systemd units (static IP, ipset, boot reconciler, overrides)"
install -m 644 "$REPO_DIR/network/systemd/cybersafe-wlan0-ip.service" /etc/systemd/system/cybersafe-wlan0-ip.service
install -m 644 "$REPO_DIR/network/systemd/cybersafe-ipset.service" /etc/systemd/system/cybersafe-ipset.service
install -m 644 "$REPO_DIR/network/systemd/cybersafe-reconcile.service" /etc/systemd/system/cybersafe-reconcile.service

mkdir -p /etc/systemd/system/hostapd.service.d
install -m 644 "$REPO_DIR/network/systemd/hostapd.service.d-override.conf" /etc/systemd/system/hostapd.service.d/override.conf

mkdir -p /etc/systemd/system/netfilter-persistent.service.d
install -m 644 "$REPO_DIR/network/systemd/netfilter-persistent.service.d-override.conf" /etc/systemd/system/netfilter-persistent.service.d/override.conf

echo "==> Installing the privileged helper + sudoers rule"
install -m 755 -o root -g root "$REPO_DIR/network/cybersafe-grant-access" /usr/local/sbin/cybersafe-grant-access
install -m 440 -o root -g root "$REPO_DIR/network/sudoers-cybersafe" /etc/sudoers.d/cybersafe
visudo -c -f /etc/sudoers.d/cybersafe

echo "==> Enabling IP forwarding"
cat >/etc/sysctl.d/99-cybersafe-forward.conf <<'EOF'
net.ipv4.ip_forward=1
EOF
sysctl --system >/dev/null

echo "==> Creating the ipset now (systemd unit recreates it on every future boot)"
ipset create voucher_allow hash:mac timeout 0 -exist

echo "==> Configuring NAT + the per-MAC gate"
iptables -t nat -C POSTROUTING -o eth0 -j MASQUERADE 2>/dev/null || \
    iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE

iptables -N CYBERSAFE_GATE 2>/dev/null || true
iptables -F CYBERSAFE_GATE
iptables -A CYBERSAFE_GATE -m set --match-set voucher_allow src-mac -j ACCEPT
iptables -A CYBERSAFE_GATE -j DROP

iptables -C FORWARD -i wlan0 -o eth0 -j CYBERSAFE_GATE 2>/dev/null || \
    iptables -I FORWARD 1 -i wlan0 -o eth0 -j CYBERSAFE_GATE

echo "==> Saving the firewall rules so they survive a reboot"
netfilter-persistent save

echo "==> Enabling everything to start on boot"
systemctl daemon-reload
systemctl enable cybersafe-wlan0-ip.service cybersafe-ipset.service cybersafe-reconcile.service
systemctl enable hostapd dnsmasq netfilter-persistent

echo "==> Starting the access point now"
systemctl start cybersafe-wlan0-ip.service
systemctl start hostapd
systemctl restart dnsmasq

echo ""
echo "Done. wlan0 is now broadcasting the AP on 10.42.0.1/24."
echo "Point the app's gunicorn service at 0.0.0.0:8000 (already the case per"
echo "DEPLOYMENT.md) and it will be reachable at http://10.42.0.1:8000 and"
echo "http://cybersafe.local:8000 from any device that joins the WiFi."
echo ""
echo "Check status with:"
echo "  systemctl status hostapd dnsmasq cybersafe-ipset cybersafe-wlan0-ip"
echo "  sudo ipset list voucher_allow"
echo "  sudo iptables -L CYBERSAFE_GATE -v"

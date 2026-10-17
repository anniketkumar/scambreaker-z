#!/usr/bin/env bash
# One-time setup of a LinuxONE Community Cloud Ubuntu VM (run as linux1).
set -euo pipefail

echo "== platform =="
uname -a
grep -m1 '^processor 0' /proc/cpuinfo || true
grep -m1 '^features' /proc/cpuinfo || true
nproc; free -h; df -h /

echo "== packages =="
sudo apt-get update -y -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq cryptsetup jq git asciinema >/dev/null

echo "== docker =="
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sudo sh /tmp/get-docker.sh >/dev/null
fi
sudo usermod -aG docker "$USER"
sudo systemctl enable --now docker
sudo docker version --format 'docker {{.Server.Version}} ({{.Server.Arch}})'
sudo docker compose version

echo "== firewall: console 8080, jupyter 38888 =="
for p in 8080 38888; do
  sudo iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null || sudo iptables -I INPUT -p tcp --dport "$p" -j ACCEPT
done
sudo mkdir -p /etc/iptables && sudo sh -c 'iptables-save > /etc/iptables/rules.v4'
echo "bootstrap done"

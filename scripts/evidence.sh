#!/usr/bin/env bash
# Capture platform evidence from real commands (for docs/evidence and the live proof panel).
set -uo pipefail
OUT=${1:-$HOME/evidence}
mkdir -p "$OUT"
run() { local f="$1"; shift; { echo "\$ $*"; "$@" 2>&1; } > "$OUT/$f.txt"; }

run 01_uname uname -a
run 02_lscpu lscpu
run 03_cpu_features sh -c "grep -m1 '^features' /proc/cpuinfo; grep -m1 '^processor 0' /proc/cpuinfo"
run 04_memory_disk sh -c "free -h; df -h / /secure"
run 05_luks_status sudo cryptsetup status securedata
run 06_lsblk lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINT
run 07_cpacf_drivers sh -c "grep -E '^(name|driver|priority)' /proc/crypto | paste - - - | grep s390 | sort -u"
run 08_cryptsetup_benchmark sudo cryptsetup benchmark -c aes-xts -s 512
run 09_openssl_speed openssl speed -elapsed -seconds 2 -evp aes-256-gcm
run 10_docker_ps sudo docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
run 11_networks sh -c "sudo docker network inspect scambreaker_core --format 'core network internal={{.Internal}}'; sudo docker network ls"
run 12_genai_egress sh -c "sudo docker exec scambreaker-genai-1 python -c \"import socket;socket.create_connection(('1.1.1.1',443),timeout=3)\" && echo EGRESS_OPEN || echo EGRESS_BLOCKED"
run 13_audit_verify sudo docker exec scambreaker-web-1 python -m scambreaker.security.audit verify /secure/work/audit/decisions.jsonl
run 14_docker_stats sudo docker stats --no-stream

# Machine-readable facts for the live "Platform proof" panel
STATUS=$(sudo cryptsetup status securedata 2>/dev/null)
TYPE=$(echo "$STATUS" | awk -F': *' '/type:/{print $2}')
CIPHER=$(echo "$STATUS" | awk -F': *' '/cipher:/{print $2}')
KEYSIZE=$(echo "$STATUS" | awk -F': *' '/keysize:/{print $2}' | awk '{print $1}')
CONTAINERS=$(sudo docker ps --format '{{.Names}}  {{.Status}}' | jq -R . | jq -s .)
sudo mkdir -p /secure/work/proof
jq -n --arg type "$TYPE" --arg cipher "$CIPHER" --arg keysize "$KEYSIZE" --argjson containers "${CONTAINERS:-[]}" \
  --arg generated "$(date -Iseconds)" \
  '{luks: {type: $type, cipher: $cipher, keysize: ($keysize|tonumber? // $keysize), device: "/dev/mapper/securedata", mount: "/secure"},
    containers: $containers, generated: $generated}' | sudo tee /secure/work/proof/host.json >/dev/null
sudo chown 1000:1000 /secure/work/proof/host.json
echo "evidence written to $OUT"

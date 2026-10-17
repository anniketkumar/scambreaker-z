#!/usr/bin/env bash
# Ship the working tree to the LinuxONE VM (run from the repo root on the laptop).
#   scripts/deploy.sh <vm-ip|ssh-alias> [path/to/key.pem]
set -euo pipefail
IP=${1:?usage: deploy.sh <vm-ip|ssh-alias> [key]}
KEY=${2:-$HOME/.ssh/l1cc.pem}
if [[ "$IP" =~ ^[0-9.]+$ ]]; then
  SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ConnectTimeout=30 "linux1@$IP")
else
  SSH=(ssh "$IP")  # an ~/.ssh/config alias (keeps connection multiplexing and timeouts)
fi
tar czf - --exclude=.venv --exclude=work --exclude=.git --exclude=video/build --exclude=video/out \
    --exclude=__pycache__ --exclude=.pytest_cache . \
  | "${SSH[@]}" "mkdir -p ~/scambreaker-z && tar xzf - -C ~/scambreaker-z && echo synced to \$(hostname)"

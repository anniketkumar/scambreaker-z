#!/usr/bin/env bash
# Ship the working tree to the LinuxONE VM (run from the repo root on the laptop).
#   scripts/deploy.sh <vm-ip> [path/to/key.pem]
set -euo pipefail
IP=${1:?usage: deploy.sh <vm-ip> [key]}
KEY=${2:-$HOME/.ssh/l1cc.pem}
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 "linux1@$IP")
tar czf - --exclude=.venv --exclude=work --exclude=.git --exclude=video/build --exclude=video/out \
    --exclude=__pycache__ --exclude=.pytest_cache . \
  | "${SSH[@]}" "mkdir -p ~/scambreaker-z && tar xzf - -C ~/scambreaker-z && echo synced to \$(hostname)"

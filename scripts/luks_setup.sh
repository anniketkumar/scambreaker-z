#!/usr/bin/env bash
# Encrypted storage for all ScamBreaker-Z data, models and audit logs:
# a LUKS2 volume (AES-XTS, 512-bit key) mounted at /secure. On IBM Z the kernel's
# xts-aes-s390 driver runs the cipher on the CPACF on-chip crypto unit.
# Demo note: the key file sits on the root disk (root-only). In production it would be a
# protected key wrapped by Crypto Express (zkey / paes cipher) so the clear key never exists in memory.
set -euo pipefail
SIZE=${SIZE:-16G}
IMG=/var/lib/scambreaker/secure.img
KEY=/root/.scambreaker-luks.key
NAME=securedata

sudo mkdir -p /var/lib/scambreaker
if [ ! -f "$IMG" ]; then
  sudo fallocate -l "$SIZE" "$IMG"
  sudo sh -c "umask 077; head -c 64 /dev/urandom > $KEY"
  sudo cryptsetup luksFormat --type luks2 --cipher aes-xts-plain64 --key-size 512 \
       --pbkdf pbkdf2 --batch-mode --key-file "$KEY" "$IMG"
fi
[ -e /dev/mapper/$NAME ] || sudo cryptsetup open --key-file "$KEY" "$IMG" "$NAME"
sudo blkid /dev/mapper/$NAME >/dev/null 2>&1 || sudo mkfs.ext4 -q -L securedata /dev/mapper/$NAME
sudo mkdir -p /secure
mountpoint -q /secure || sudo mount /dev/mapper/$NAME /secure
sudo mkdir -p /secure/work/{data,models,audit,secrets,proof,llm,raw,results}
sudo chown -R 1000:1000 /secure/work
sudo cryptsetup status $NAME
grep -E 'driver.*(xts|aes).*s390' /proc/crypto | sort -u || true

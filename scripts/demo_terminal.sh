#!/usr/bin/env bash
# Commands shown in the video's IBM Z segment (recorded with asciinema on the VM).
type_run() { printf '\033[1;34m$\033[0m %s\n' "$1"; sleep 0.6; eval "$1"; echo; sleep 1.2; }
cd ~/scambreaker-z || exit 1
type_run "uname -m && grep -m1 '^processor 0' /proc/cpuinfo | sed 's/.*machine/machine/'"
type_run "grep -o -w nnpa /proc/cpuinfo | head -1   # Telum on-chip AI accelerator facility"
type_run "docker compose ps --format 'table {{.Service}}\t{{.Status}}'"
type_run "sudo cryptsetup status securedata | grep -E 'type|cipher|keysize|device'"
type_run "grep -A2 'driver *: xts-aes-s390' /proc/crypto   # CPACF driver: highest priority, used by dm-crypt"
type_run "docker exec scambreaker-genai-1 python -c \"import socket; socket.create_connection(('1.1.1.1',443),3)\" 2>&1 | tail -1   # AI container: no internet"
type_run "docker exec scambreaker-web-1 python -m scambreaker.security.audit verify /secure/work/audit/decisions.jsonl"

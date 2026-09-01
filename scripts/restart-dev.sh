#!/usr/bin/env bash
# Restart the vizion-lab dev stack.
#
# Problem: scripts/dev.mjs reuses existing healthy processes on their ports, so
# even after Ctrl+C the old local-api (3004) / vite (8080) / analytics (3003) /
# session-keeper (8090) may still be running with stale code. This script kills
# ALL processes on those ports first, then starts fresh.
#
# Usage:
#   bash scripts/restart-dev.sh         # kill all + restart
#   bash scripts/restart-dev.sh --kill  # kill only, don't restart

set -euo pipefail

cd "$(dirname "$0")/.."

PORTS=(3004 8080 3003 8090)

echo "=== Stopping all vizion-lab dev processes ==="

for port in "${PORTS[@]}"; do
  pids=$(netstat -ano 2>/dev/null | grep ":${port} " | grep LISTENING | awk '{print $5}' | sort -u || true)
  if [ -z "$pids" ]; then
    echo "  port ${port}: free"
  else
    for pid in $pids; do
      echo "  port ${port}: killing PID ${pid}"
      taskkill //PID "$pid" //F 2>/dev/null || true
    done
  fi
done

# Also kill any lingering node processes running dev.mjs / devLocalApi.mjs
echo "=== Killing lingering dev.mjs / devLocalApi.mjs node processes ==="
for pid in $(powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | Where-Object { \$_.CommandLine -match 'dev\\.mjs|devLocalApi' } | Select-Object -ExpandProperty ProcessId" 2>/dev/null | tr -d '\r\n' | grep -E '^[0-9]+$' || true); do
  echo "  killing node dev PID ${pid}"
  taskkill //PID "$pid" //F 2>/dev/null || true
done

echo "=== All stopped. Waiting 2s for ports to release... ==="
sleep 2

# Verify ports are free
for port in "${PORTS[@]}"; do
  if netstat -ano 2>/dev/null | grep ":${port} " | grep -q LISTENING; then
    echo "  WARNING: port ${port} still occupied!"
  else
    echo "  port ${port}: free"
  fi
done

if [ "${1:-}" = "--kill" ]; then
  echo "=== --kill mode, not restarting ==="
  exit 0
fi

echo "=== All processes stopped. Now run this in PowerShell: ==="
echo ""
echo "  npm run dev"
echo ""
echo "=== (Don't run npm from inside bash — it tries WSL and fails) ==="

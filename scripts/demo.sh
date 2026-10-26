#!/usr/bin/env bash
# Start the AeroDrop simulation and wait until the drone can really launch.
#
#   ./scripts/demo.sh            start (or keep) the stack, wait for READY, open the dashboard
#   ./scripts/demo.sh --build    rebuild the backend and ROS images first
#   ./scripts/demo.sh --check    only report readiness; start nothing
#   ./scripts/demo.sh --no-open  do not open a browser
#
# "Ready" is the API's own launch policy: GET /status returns no launch_blockers
# (fresh PX4 position, status, landing and battery reports, a live mission-controller
# heartbeat, landed, disarmed, no failsafe, battery of at least 20%).
set -euo pipefail

cd "$(dirname "$0")/.."
URL="${AERODROP_URL:-http://localhost:8000}"   # dashboard and API address
TIMEOUT="${AERODROP_READY_TIMEOUT:-900}"   # PX4's EKF can take several minutes to align
BUILD=0; CHECK=0; OPEN=1
for arg in "$@"; do
  case "$arg" in
    --build) BUILD=1 ;;
    --check) CHECK=1 ;;
    --no-open) OPEN=0 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

# Prints "READY", or one blocker per line. Exit code 1 if the API is unreachable
# (quietly: while the API starts, curl returns nothing and the parser fails).
readiness() {
  curl -fsS --max-time 3 "$URL/status" 2>/dev/null | python3 -c '
import json, sys
status = json.load(sys.stdin)
telemetry = status.get("telemetry") or {}
blockers = status.get("launch_blockers") or []
if blockers:
    print("\n".join(blockers))
else:
    print("READY", telemetry.get("state", ""), telemetry.get("battery_pct", ""))
' 2>/dev/null || return 1
}

open_dashboard() {
  [ "$OPEN" = 1 ] || return 0
  if command -v cmd.exe >/dev/null 2>&1; then cmd.exe /c start "" "$URL" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then open "$URL" || true
  fi
}

if [ "$CHECK" = 1 ]; then
  if out=$(readiness); then
    echo "$out"; [[ "$out" == READY* ]] && exit 0 || exit 1
  fi
  echo "Backend not reachable at $URL"; exit 1
fi

command -v docker >/dev/null || { echo "Docker is not installed or not on PATH." >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop and try again." >&2; exit 1; }

if [ "$BUILD" = 1 ]; then
  echo "Building and starting the stack…"
  docker compose up --build -d
else
  echo "Starting the stack (already-running services are left as they are)…"
  docker compose up -d
fi

echo "Waiting for the drone to be ready to launch (up to $((TIMEOUT / 60)) min)…"
start=$(date +%s); last=""; ready_polls=0
while :; do
  elapsed=$(( $(date +%s) - start ))
  if out=$(readiness); then :; else out="Waiting for the dispatch API at $URL"; fi
  # Telemetry counts as fresh for 3 s, so right after a container restart the last frames
  # of the old process can still pass. Ready must hold across two polls 3 s apart.
  if [[ "$out" == READY* ]]; then ready_polls=$((ready_polls + 1)); else ready_polls=0; fi
  if [ "$ready_polls" -ge 2 ]; then
    read -r _ state battery <<<"$out"
    printf '\nREADY after %ss: drone %s, battery %s%%.\n' "$elapsed" "$state" "${battery%%.*}"
    echo "Dashboard: $URL"
    echo "Demo route: choose \"Detour demonstration\" to show the planner routing around a restricted zone."
    open_dashboard
    exit 0
  fi
  first=${out%%$'\n'*}
  [[ "$first" == READY* ]] && first="Ready; confirming it holds"
  if [ "$first" != "$last" ]; then printf '  [%4ss] %s\n' "$elapsed" "$first"; last="$first"; fi
  if [ "$elapsed" -ge "$TIMEOUT" ]; then
    echo
    echo "Not ready after $((TIMEOUT / 60)) min. Still blocked by:"
    while IFS= read -r line; do echo "  - $line"; done <<<"$out"
    echo
    echo "Last simulator log lines:"
    docker logs aerodrop_px4_sitl --tail 15 2>&1 | sed 's/\x1b\[[0-9;]*m//g; s/^/    /' || true
    echo
    if [[ "$out" == *"Mission controller is not reporting"* ]]; then
      # The telemetry bridge runs in the same container, so this also explains missing PX4 data.
      echo "The ROS container (mission controller and telemetry bridge) is not reporting. Restart it:"
      echo "  docker compose up -d --force-recreate aerodrop-ros && ./scripts/demo.sh"
    elif [[ "$out" == *"PX4 position"* ]]; then
      echo "PX4 has no valid position (an IMU or EKF stall). Restart only the simulator:"
      echo "  docker compose restart px4-sitl && ./scripts/demo.sh"
    else
      echo "Resolve the blocker above, then run ./scripts/demo.sh again."
    fi
    exit 1
  fi
  sleep 3
done

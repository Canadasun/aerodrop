#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
PX4_DIR="${PX4_AUTOPILOT_DIR:-$HOME/PX4-Autopilot}"

if [[ ! -f "$PX4_DIR/Makefile" ]]; then
  echo "PX4 checkout not found at $PX4_DIR (set PX4_AUTOPILOT_DIR)" >&2
  exit 1
fi

export PX4_SIM_MODEL="${PX4_SIM_MODEL:-gz_x500}"
export PX4_GZ_WORLD="${PX4_GZ_WORLD:-calgary_suburb}"
export GZ_SIM_RESOURCE_PATH="$PROJECT_DIR/simulation/worlds${GZ_SIM_RESOURCE_PATH:+:$GZ_SIM_RESOURCE_PATH}"
export PX4_UXRCE_DDS_PORT="${PX4_UXRCE_DDS_PORT:-8888}"
echo "Launching $PX4_SIM_MODEL in $PX4_GZ_WORLD with the 10 m/s west-to-east world wind"
cd "$PX4_DIR"
exec make px4_sitl gz_x500

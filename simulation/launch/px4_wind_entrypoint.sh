#!/usr/bin/env bash
set -euo pipefail

# Enable Gazebo's link wind mode on the bundled x500 model so WindEffects
# applies forces, instead of publishing only the world wind estimate.
X500_MODEL="/opt/px4-gazebo/share/gz/models/x500_base/model.sdf"
if [[ -f "$X500_MODEL" ]] && ! grep -q '<enable_wind>true</enable_wind>' "$X500_MODEL"; then
  sed -i '/<link name=/a\      <enable_wind>true</enable_wind>' "$X500_MODEL"
fi
exec /opt/px4-gazebo/bin/px4-entrypoint.sh "$@"

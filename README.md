# AeroDrop local prototype

Run `docker compose up --build -d`, then open http://localhost:8000 in your Windows browser. Docker Desktop must have host networking enabled for the ROS/PX4 services. The ROS and DDS agent containers share the host IPC namespace for Fast DDS shared-memory transport. The first ROS interface build can take a few minutes.

Wait for **READY**, set the destination, click **Plan corridor**, then **Review & launch** and confirm **Launch simulation**. The start coordinates must match the drone's current launch location. A short first flight can use latitude `51.116`, longitude `-114.109`. The default **Quick delivery** preset is a short flight. Choose **Full demonstration** for the original destination about 2.2 km away, which takes several minutes each way.

The simulated aircraft takes off to 30 m, follows the planned corridor, hovers, simulates payload release, retraces the corridor, lands, and disarms. **COMPLETE** means landed and disarmed. **Return and land** skips delivery. **Low battery** latches a simulated low-battery condition and follows the reached route back. **Geofence breach** injects a synthetic geofence fault and commands PX4's native return-to-launch, ending at **ABORTED** after landing and disarming. Actual proximity to the synthetic map zones also triggers RTL. These are simulator controls, not controls for a physical drone.

The page reconnects telemetry automatically and disables flight controls when position is stale. Route planning is separate from starting a mission; a mission uses the reviewed destination submitted when Launch simulation is confirmed. The planner is locked during an active flight. **Use drone** matches the launch point to the actual position. You can drag the drop-off marker or use **Pick on map**, inspect waypoints, toggle overlays and follow the aircraft.

The Compose simulator image is pinned by digest. `ros2_ws/src/px4_msgs/FIRMWARE_COMMIT` records its exact upstream firmware source; the vendored message definitions match that commit. Only the eight used interfaces are compiled by default; set the CMake option `AERODROP_MINIMAL_INTERFACES=OFF` if adding other PX4 interfaces. Do not switch firmware without updating message definitions and topic versions. This follows PX4's matching-interface requirement: https://docs.px4.io/main/en/ros2/user_guide .

The Gazebo world includes IMU, magnetometer, GPS and other sensor systems, with wind enabled and a bounded force scaling factor. Autonomous SITL uses `COM_RC_IN_MODE=4` and `NAV_DLL_ACT=0` because there is no handheld controller or external GCS. PX4 arming checks remain enabled.

Payload/winch release is a timed simulation placeholder. The no-fly rectangles are synthetic examples. This is a local, single-process, single-drone prototype with in-memory mission and telemetry state; it is not a real flight system or an internet-facing service.

## Tests

With backend requirements installed:

```bash
python3 -m unittest discover -s tests -v
```

Browser flight scenarios require Playwright and a running stack:

```bash
npm install --no-save playwright @axe-core/playwright
node tests/e2e_browser.cjs
```

The browser suite executes short real PX4 delivery, low-battery return, and injected-geofence RTL, and operator-return flights, plus telemetry reconnection. Screenshots and results go to `test-artifacts/`; set `AERODROP_TEST_OUTPUT` to use another directory.

## Existing infrastructure examples

`infra/terraform` wraps the Compose lifecycle. `infra/k8s` contains API/agent examples; the complete simulator loop runs in Compose with host DDS networking.


## Operations dashboard

The dashboard includes a route planner, live aircraft instruments, mission stages, payload state, position freshness checks, activity log, telemetry charts, waypoint inspection, locally stored mission history and system diagnostics. All launch, return and fault commands have a review step. Simulated battery values are labelled and the actual PX4 battery is retained. A previous simulated low-battery condition does not block a new mission when the real battery is healthy.

MapLibre 5.6.2 and its licence are bundled under `frontend/assets/vendor`; only basemap data still needs internet access. If basemap loading fails, the dashboard falls back to a plain map with route, geofence, marker and trail overlays. Coordinate planning remains available.

`GET /config` supplies the actual planner geometry, buffers and supported controls. `GET /status` exposes the latest snapshot and data age. Health indicators distinguish backend connectivity, WebSocket connectivity, PX4 status freshness and valid global position. They do not claim container health from an API response alone.

Telemetry now includes measured horizontal/vertical speed, heading, landing status, PX4 preflight/failsafe state, command acknowledgements, active waypoints, payload state and mission timing. Unknown measurements remain unavailable instead of showing invented zeroes. Mission history is local to the browser; it is not a server database. Order/database modules in this checkout are empty placeholders and are explicitly marked unavailable.

UI tests use a separate backend on port 18000. `tests/ui_dashboard.cjs` refuses other ports because it publishes synthetic test telemetry. Never run that suite against the flight stack. `tests/e2e_browser.cjs` is the separate real PX4 flight suite and uses the confirmation dialogs.

## License

Apache License 2.0, see [LICENSE](LICENSE). Bundled PX4 message definitions and MapLibre GL JS keep their BSD 3-Clause licenses; see [NOTICE](NOTICE).

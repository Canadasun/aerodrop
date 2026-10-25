# AeroDrop operations dashboard

The live dashboard is served at http://localhost:8000. Refresh the browser after deployment.

## Operator workflow

1. Choose Quick delivery or Full demonstration, edit coordinates, drag the drop-off marker, or use Pick on map.
2. Use drone synchronizes the launch coordinates with a fresh actual position.
3. Plan corridor validates the destination and retrieves a route from the backend. Editing coordinates makes the plan stale and blocks dispatch until it is recalculated.
4. Review & launch shows the exact destination, route, cruise altitude and any scheduled battery test. Confirming sends the mission; closing the dialog does not.
5. Follow actual takeoff, navigation, hover, payload simulation, return, landing and disarming. Return and land and fault-injection actions also require confirmation.
6. Inspect activity, telemetry trends and active waypoints. Export activity, history or a complete current snapshot.

## Backend coverage

| Backend capability | Dashboard representation |
|---|---|
| Health endpoint | Independent API connection indicator and Systems card |
| Route planner | Presets, coordinate editing, map picking and dragging, validation errors, route distance, waypoints and altitude |
| Corridor width | Actual-width map overlay and width summary |
| Synthetic geofences | Zone and expanded-buffer GeoJSON from the backend; individually toggleable layers |
| Mission creation | Reviewed dispatch with queued/observed feedback and duplicate-start gating |
| Current mission | Mission ID, actual phase, elapsed time, active waypoints and diagnostics |
| Return action | Confirmed Return and land command |
| Low-battery action | Confirmed 19% test with the real PX4 battery preserved separately |
| Scheduled battery test | Off, 20 seconds, 60 seconds or 180 seconds, sent with mission creation |
| Geofence action | Confirmed simulated breach and native PX4 RTL |
| Position telemetry | Live aircraft marker, trail, follow control, coordinate readout, global-position freshness |
| PX4 velocity and heading | Actual measured horizontal/vertical speed and heading; unknown values remain unavailable |
| PX4 status | Flight mode, arming state, preflight/failsafe status and separate controller freshness |
| Landing status | Explicit landed/disarmed information |
| Vehicle command acknowledgements | Actual accepted/rejected command feedback in activity and telemetry |
| Payload state | On board, releasing, released or skipped; explicitly simulated |
| Mission timing | Actual start/end timestamps and elapsed duration from the executor |
| Snapshot/configuration | Systems view, flight limits, supported capabilities and optional JSON diagnostics |
| Empty order/database scaffolds | Marked not implemented; no fake order or persistence workflow |
| Wind | Clearly noted as configured in Gazebo but not measured in the telemetry stream |

`GET /config` shares planner geometry and supported controls. `GET /status` shares the latest snapshot and calculated data age. Static assets are served under `/assets`.

## Data and interaction integrity

- Launch requires backend connectivity, a fresh WebSocket frame, recent global position, recent controller status, a disarmed idle aircraft, an unchanged valid plan and a reported healthy actual battery.
- Position/status loss disables commands. Old measurements remain visible with a stale-data explanation rather than becoming fabricated zeroes.
- A simulated low-battery condition from a completed mission does not block the next mission when the actual PX4 battery is healthy.
- The planner is locked during flight; return and fault controls are only enabled in phases that actually accept them.
- A command accepted by the API is shown as queued until its effect is observed in telemetry.
- Mission history is stored locally in this browser. Backend mission state remains single-process and in memory.
- MapLibre 5.6.2 is bundled locally with its licence. Remote basemap failure falls back to a plain map; route, marker, geofence and trail overlays and coordinate planning remain available.
- Dialog focus, keyboard tabs, accessible names, responsive layout and text contrast were checked. The map's moving aircraft and launch/drop-off markers have named graphic roles.

## Verification

*Historical record from the 2026-10-19 dashboard redesign. Counts reflect the code at that date; for current results, see the README "Tests" section and the latest commit message.*

- 13 routing, coordinate, mission-command and dashboard geometry/freshness regression tests passed.
- 18 isolated browser interaction checks passed, including dirty-plan blocking, restriction errors, map clicks and dragging, layer toggles, launch cancellation, exact mission requests, stale telemetry, fault confirmation, local history/export, reconnection, mobile overflow and basemap outage.
- Automated accessibility checks cover Operations, Missions and Systems. Final deployed results are recorded with the UI artifacts.
- Real PX4/Gazebo browser flights passed for delivery, low-battery return, injected-geofence RTL and operator return, all landed and disarmed.
- Recorded real telemetry confirms payload RELEASED for delivery, SKIPPED for abort/return scenarios, actual measured speed/heading, and consistent start/end/elapsed timestamps.
- Tests use short delivery routes; this does not claim a new endurance test of the original 2.2 km destination.

Browser interaction tests must run against a separate preview backend on port 18000 because they publish synthetic telemetry. They explicitly refuse the live flight port. The real flight suite is `tests/e2e_browser.cjs` and uses the production controls and confirmation dialogs.

Artifacts are written to `test-artifacts/` (or `$AERODROP_TEST_OUTPUT`).

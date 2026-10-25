"""A stalled map viewer must not hold up the drone feed or other viewers."""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.api.telemetry import TelemetryHub  # noqa: E402


class TelemetryHubTests(unittest.TestCase):
    def test_publish_never_waits_and_keeps_only_the_newest_frame(self):
        async def scenario():
            hub = TelemetryHub()
            stalled = hub.add_viewer("drone_01", object())   # nobody ever reads this queue
            healthy = hub.add_viewer("drone_01", object())
            for i in range(50):
                await asyncio.wait_for(hub.publish("drone_01", {"seq": i}), 0.05)
                if i == 10:
                    self.assertEqual((await healthy.get())["seq"], 10)
            self.assertEqual(stalled.qsize(), 1)
            self.assertEqual(stalled.get_nowait()["seq"], 49)
            self.assertEqual(healthy.get_nowait()["seq"], 49)
            self.assertEqual(hub.latest["drone_01"]["seq"], 49)
        asyncio.run(scenario())

    def test_disconnect_removes_the_viewer(self):
        hub = TelemetryHub(); viewer = object()
        hub.add_viewer("drone_01", viewer)
        hub.disconnect("drone_01", viewer)
        self.assertNotIn("drone_01", hub.viewers)


if __name__ == "__main__":
    unittest.main()

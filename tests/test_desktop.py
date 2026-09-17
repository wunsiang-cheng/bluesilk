import os
import shutil
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.support import BluesilkTestCase


class DesktopUnitTests(BluesilkTestCase):
    def test_action_rejects_unknown_action_before_touching_the_display(self):
        backend = self.bs.desktop.XDesktopBackend()
        with mock.patch.object(backend, "_x") as x:
            with self.assertRaisesRegex(ValueError, "unknown desktop action"):
                backend._action(self.bs.state.Session(), {"action": "format_disk"})
            with self.assertRaisesRegex(ValueError, "drag requires y, x2"):
                backend._action(self.bs.state.Session(), {"action": "drag", "x": 1, "y2": 4})
        x.assert_not_called()

    def test_actions_map_to_xdotool_and_return_a_screenshot(self):
        backend = self.bs.desktop.XDesktopBackend()
        calls = []
        fake_shot = lambda argv, **kw: Path(argv[-1]).write_bytes(b"\xff\xd8\xffjpeg")

        with mock.patch.object(backend, "_x", side_effect=lambda *a, text=None: calls.append((a, text))), \
             mock.patch.object(self.bs.desktop.subprocess, "run", side_effect=fake_shot), \
             mock.patch.object(self.bs.desktop.subprocess, "check_output", return_value="1536 1024\n"), \
             mock.patch.object(self.bs.desktop.time, "sleep"):
            result = backend._action(self.bs.state.Session(), {"action": "right_click", "x": 10, "y": 20})
            backend._action(self.bs.state.Session(), {"action": "type", "text": "héllo"})
            backend._action(self.bs.state.Session(), {"action": "scroll", "delta_y": -50})
        self.assertEqual(calls, [(("mousemove", 10, 20, "click", "3"), None),
                                 (("type", "--delay", "12", "--file", "-"), "héllo"),
                                 (("click", "--repeat", 20, "4"), None)])
        self.assertIn('"screen": [1536, 1024]', result.text)
        self.assertTrue(result.image.startswith("data:image/jpeg;base64,"))
        self.assertTrue((self.home / "desktop" / "screenshots").is_dir())

    def test_init_desktop_tool_stays_disabled_without_x11(self):
        with mock.patch.dict(os.environ, {"DISPLAY": ""}), mock.patch.object(self.bs.desktop, "log") as log:
            self.bs.desktop.init_desktop_tool()
        self.assertIsNone(self.bs.state.DESKTOP)
        self.assertFalse(any(t["function"]["name"] == "desktop" for t in self.bs.tools.TOOLS))
        log.assert_called_once()
        with self.assertRaisesRegex(RuntimeError, "desktop control is unavailable"):
            self.bs.desktop.desktop(self.bs.state.Session(), action="observe")

    def test_init_desktop_tool_publishes_once_when_available(self):
        with mock.patch.dict(os.environ, {"DISPLAY": ":0"}), mock.patch.object(self.bs.desktop.shutil, "which", return_value="/usr/bin/x"):
            self.bs.desktop.init_desktop_tool()
            self.bs.desktop.init_desktop_tool()
        self.assertIsNotNone(self.bs.state.DESKTOP)
        self.assertEqual(sum(t["function"]["name"] == "desktop" for t in self.bs.tools.TOOLS), 1)
        self.assertIn("`desktop` controls", self.bs.agent.system_prompt(self.bs.state.Session()))


@unittest.skipUnless(shutil.which("Xvfb") and shutil.which("xdotool") and (shutil.which("scrot") or shutil.which("ffmpeg")),
                     "needs Xvfb, xdotool and scrot or ffmpeg")
class DesktopIntegrationTests(BluesilkTestCase):
    def test_observe_captures_a_virtual_display(self):
        xvfb = subprocess.Popen(["Xvfb", ":97", "-screen", "0", "640x480x24"], stderr=subprocess.DEVNULL)
        try:
            time.sleep(1)
            with mock.patch.dict(os.environ, {"DISPLAY": ":97"}):
                result = self.bs.desktop.XDesktopBackend()._action(self.bs.state.Session(), {"action": "click", "x": 5, "y": 5})
            self.assertIn('"screen": [640, 480]', result.text)
            self.assertTrue(result.image.startswith("data:image/jpeg;base64,"))
        finally:
            xvfb.kill()

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_bluesilk(home):
    """Import a fresh package whose import-time paths point at an isolated home."""
    old = os.environ.get("BLUESILK_HOME")
    os.environ["BLUESILK_HOME"] = str(home)
    for name in [n for n in sys.modules if n == "bluesilk" or n.startswith("bluesilk.")]:
        del sys.modules[name]
    try:
        return importlib.import_module("bluesilk")
    finally:
        if old is None:
            os.environ.pop("BLUESILK_HOME", None)
        else:
            os.environ["BLUESILK_HOME"] = old


class BluesilkTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.bs = load_bluesilk(self.home)
        self.bs.agent.init_home()

    def tearDown(self):
        if self.bs.state.BROWSER is not None:
            self.bs.state.quiet(self.bs.state.BROWSER.close)
        for server, _ in set(self.bs.state.SERVERS.values()):
            process = getattr(server, "p", None)
            if process and process.poll() is None:
                process.kill()
                process.wait()
        self.temp.cleanup()

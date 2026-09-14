import importlib.util
import os
import tempfile
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_bluesilk(home):
    """Load a fresh module whose import-time paths point at an isolated home."""
    old = os.environ.get("BLUESILK_HOME")
    os.environ["BLUESILK_HOME"] = str(home)
    try:
        name = f"bluesilk_test_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(name, ROOT / "bluesilk.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
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
        self.bs.init_home()

    def tearDown(self):
        if self.bs.BROWSER is not None:
            self.bs.quiet(self.bs.BROWSER.close)
        for server, _ in set(self.bs.SERVERS.values()):
            process = getattr(server, "p", None)
            if process and process.poll() is None:
                process.kill()
                process.wait()
        self.temp.cleanup()

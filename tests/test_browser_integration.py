import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.support import BluesilkTestCase


PAGE = b"""<!doctype html>
<meta charset=utf-8>
<title>loading</title>
<style>
  body { margin: 0; font: 16px sans-serif; }
  button, input, a { position: absolute; left: 20px; width: 160px; height: 50px; box-sizing: border-box; }
  #click { top: 20px; }
  #text { top: 90px; }
  #popup { top: 160px; }
  #download { top: 230px; padding: 14px; border: 1px solid; }
  #remember { top: 300px; }
</style>
<button id=click onclick="document.title='clicked'">Click</button>
<input id=text oninput="document.title='typed:'+this.value">
<button id=popup onclick="window.open('/popup','_blank')">Popup</button>
<a id=download href=/download download=fixture.txt>Download</a>
<button id=remember onclick="localStorage.state='remembered';document.title='remembered'">Remember</button>
<script>document.title=localStorage.state||'ready'</script>
"""


class BrowserFixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            return self.reply(PAGE, "text/html; charset=utf-8")
        if self.path == "/popup":
            return self.reply(b"<title>popup</title><p>Second tab</p>", "text/html; charset=utf-8")
        if self.path == "/download":
            return self.reply(b"downloaded by bluesilk", "text/plain", **{
                "Content-Disposition": "attachment; filename=fixture.txt",
            })
        self.send_error(404)

    def reply(self, body, content_type, **headers):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ChromiumIntegrationTests(BluesilkTestCase):
    def setUp(self):
        super().setUp()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), BrowserFixtureHandler)
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/"
        self.bs.state.CFG["browser"] = {"viewport": [640, 480]}
        self.browser = self.bs.state.BROWSER = self.bs.browser.PlaywrightBrowserBackend()
        self.alice = self.bs.state.Session()

    def tearDown(self):
        if self.bs.state.BROWSER is not None:
            self.bs.state.BROWSER.close()
            self.bs.state.BROWSER = None
        self.httpd.shutdown()
        self.httpd.server_close()
        self.server_thread.join(timeout=2)
        super().tearDown()

    @staticmethod
    def details(result):
        return json.loads(result.text)

    def action(self, session=None, **args):
        return self.browser.submit(session or self.alice, args)

    def test_real_browser_interaction_tabs_download_and_storage(self):
        opened = self.action(action="open", url=self.url)
        opened_details = self.details(opened)
        self.assertEqual(opened_details["title"], "ready")
        self.assertEqual(opened_details["viewport"], {"width": 640, "height": 480})
        self.assertTrue(Path(opened_details["screenshot"]).is_file())
        self.assertTrue(opened.image.startswith("data:image/jpeg;base64,"))

        self.assertEqual(self.details(self.action(action="click", x=80, y=45))["title"], "clicked")
        self.action(action="click", x=80, y=115)
        self.assertEqual(self.details(self.action(action="type", text="hello"))["title"], "typed:hello")

        popup = self.details(self.action(action="click", x=80, y=185))
        self.assertEqual(popup["title"], "popup")
        tabs = self.details(self.action(action="tabs"))["status"]
        self.assertEqual(len(tabs), 2)
        self.assertTrue(tabs[1]["active"])

        switched = self.details(self.action(action="switch_tab", tab=0))
        self.assertEqual(switched["title"], "typed:hello")
        downloaded = self.details(self.action(action="click", x=80, y=255))
        download = Path(downloaded["latest_download"])
        self.assertEqual(download.read_bytes(), b"downloaded by bluesilk")

        self.assertEqual(self.details(self.action(action="click", x=80, y=325))["title"], "remembered")
        self.assertEqual(self.details(self.action(action="close"))["status"], "closed")
        self.assertEqual(self.details(self.action(action="open", url=self.url))["title"], "remembered")

    def test_real_browser_limits_saved_screenshots(self):
        self.action(action="open", url=self.url)
        for _ in range(22):
            self.action(action="observe")
        screenshots = list((self.home / "browser" / "main" / "screenshots").glob("*.jpg"))
        self.assertEqual(len(screenshots), 20)

    def test_real_browser_wait_honors_stop(self):
        self.action(action="open", url=self.url)
        self.alice.stop.set()
        with self.assertRaisesRegex(RuntimeError, "stopped by user"):
            self.action(action="wait", seconds=1)


if __name__ == "__main__":
    unittest.main()

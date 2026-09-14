import json
import threading
from http.client import HTTPConnection
from unittest import mock

from tests.support import BluesilkTestCase


class SettingsTests(BluesilkTestCase):
    def test_apply_settings_creates_web_members_and_private_config(self):
        with mock.patch.object(self.bs, "check_key"):
            result = self.bs.apply_settings({
                "api_key": "deepseek-key",
                "user_ids": "",
                "members": "alice, bob",
                "host": "127.0.0.1",
                "port": "9000",
            })
        self.assertEqual(result["api_key"], "deepseek-key")
        self.assertEqual(set(result["web"]["tokens"].values()), {"alice", "bob"})
        self.assertEqual(result["web"]["port"], 9000)
        self.assertEqual(self.bs.CONFIG.stat().st_mode & 0o777, 0o600)

    def test_apply_settings_preserves_member_tokens_and_blank_secrets(self):
        self.bs.CFG.update({
            "api_key": "old-key",
            "web": {"host": "127.0.0.1", "port": 8321, "tokens": {"existing": "alice"}},
        })
        with mock.patch.object(self.bs, "check_key"):
            result = self.bs.apply_settings({"api_key": "", "user_ids": "", "members": "alice carol"})
        self.assertEqual(result["api_key"], "old-key")
        self.assertEqual(next(token for token, name in result["web"]["tokens"].items() if name == "alice"), "existing")

    def test_apply_settings_validates_required_channel_and_browser_delay(self):
        with mock.patch.object(self.bs, "check_key"):
            with self.assertRaisesRegex(ValueError, "Telegram members, web console members"):
                self.bs.apply_settings({"api_key": "key", "user_ids": "", "members": ""})
            with self.assertRaisesRegex(ValueError, "0 to 2000"):
                self.bs.apply_settings({
                    "api_key": "key", "user_ids": "", "members": "alice", "browser_slow_mo": "2001"
                })

    def test_apply_settings_writes_and_removes_mcp_config(self):
        form = {"api_key": "key", "user_ids": "", "members": "alice", "mcp": '{"mcpServers": {}}'}
        with mock.patch.object(self.bs, "check_key"):
            self.bs.apply_settings(form)
            self.assertEqual(json.loads(self.bs.MCP.read_text()), {"mcpServers": {}})
            form["mcp"] = ""
            self.bs.apply_settings(form)
        self.assertFalse(self.bs.MCP.exists())

    def test_settings_view_masks_secrets(self):
        self.bs.CFG.update({"api_key": "abcdefgh", "bot_token": "12345678", "user_ids": [7]})
        view = self.bs.settings_view()
        self.assertEqual(view["api_key"], "abc…efgh")
        self.assertEqual(view["bot_token"], "123…5678")


class TelegramTests(BluesilkTestCase):
    def test_send_text_chunks_messages(self):
        with mock.patch.object(self.bs, "tg") as tg:
            self.bs.send_text(7, "x" * 9000)
        chunks = [call.kwargs["text"] for call in tg.call_args_list]
        self.assertEqual([len(chunk) for chunk in chunks], [4096, 4096, 808])

    def test_send_text_falls_back_when_markdown_is_rejected(self):
        error = self.bs.HTTPError("url", 400, "bad markdown", {}, None)
        with mock.patch.object(self.bs, "tg", side_effect=[error, {}]) as tg:
            self.bs.send_text(7, "bad *markdown")
        self.assertEqual(tg.call_count, 2)
        self.assertNotIn("parse_mode", tg.call_args_list[1].kwargs)

    def test_send_text_pushes_to_web_session(self):
        session = self.bs.Session("alice", "Alice", web=True)
        subscription = self.bs.queue.Queue()
        session.subs.append(subscription)
        self.bs.SESSIONS["alice"] = session
        self.bs.send_text("alice", "hello")
        self.assertEqual(subscription.get_nowait(), ("msg", "hello"))

    def test_handle_update_accepts_only_configured_private_members(self):
        session = self.bs.Session(7, "Alice")
        self.bs.SESSIONS[7] = session
        private = {"message": {"from": {"id": 7}, "chat": {"type": "private"}, "message_id": 1, "text": "hi"}}
        group = {"message": {"from": {"id": 7}, "chat": {"type": "group"}, "message_id": 2, "text": "no"}}
        stranger = {"message": {"from": {"id": 8}, "chat": {"type": "private"}, "message_id": 3, "text": "no"}}
        with mock.patch.object(self.bs, "tg"):
            self.bs.handle_update(private)
            self.bs.handle_update(group)
            self.bs.handle_update(stranger)
        self.assertEqual(session.q.qsize(), 1)
        self.assertEqual(session.q.get_nowait()["text"], "hi")


class WebTests(BluesilkTestCase):
    def setUp(self):
        super().setUp()
        self.session = self.bs.Session("alice", "Alice", web=True)
        self.bs.SESSIONS["alice"] = self.session
        self.bs.TOKENS["good-token"] = "alice"
        self.httpd = self.bs.ThreadingHTTPServer(("127.0.0.1", 0), self.bs.Web)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        super().tearDown()

    def request(self, method, path, body=b"", token=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=2)
        request_headers = dict(headers or {})
        if token is not None:
            request_headers["Cookie"] = f"t={token}"
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_root_is_public_but_settings_require_authentication(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"BLUESILK CONSOLE", body)
        self.assertEqual(self.request("GET", "/settings")[0], 401)
        self.assertEqual(self.request("GET", "/settings", token="good-token")[0], 200)

    def test_send_and_stop_endpoints_target_authenticated_session(self):
        self.assertEqual(self.request("POST", "/send", b"hello", "good-token")[0], 200)
        self.assertEqual(self.session.q.get_nowait()["text"], "hello")
        self.assertFalse(self.session.stop.is_set())
        self.assertEqual(self.request("POST", "/stop", token="good-token")[0], 200)
        self.assertTrue(self.session.stop.is_set())

    def test_upload_strips_path_and_queues_saved_file(self):
        status, _, _ = self.request("POST", "/upload?name=../../note.txt&text=caption", b"contents", "good-token")
        self.assertEqual(status, 200)
        message = self.session.q.get_nowait()
        path = message["local"][0]
        self.assertEqual(path.parent, self.home / "inbox")
        self.assertEqual(path.name.split("_", 1)[1], "note.txt")
        self.assertEqual(path.read_bytes(), b"contents")

    def test_file_endpoint_serves_only_registered_files(self):
        file = self.home / "report.txt"
        file.write_text("report")
        self.session.files["allowed"] = file
        status, headers, body = self.request("GET", "/file/allowed/report.txt", token="good-token")
        self.assertEqual((status, body), (200, b"report"))
        self.assertIn("report.txt", headers["Content-Disposition"])
        self.assertEqual(self.request("GET", "/file/missing/report.txt", token="good-token")[0], 404)


if __name__ == "__main__":
    import unittest
    unittest.main()

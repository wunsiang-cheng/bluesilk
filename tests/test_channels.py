import json
import threading
from http.client import HTTPConnection
from unittest import mock

from tests.support import BluesilkTestCase


class SettingsTests(BluesilkTestCase):
    def test_apply_settings_creates_web_members_and_private_config(self):
        with mock.patch.object(self.bs.setup, "check_key"), mock.patch.object(self.bs.setup, "check_model", return_value=(1_048_576, True)):
            result = self.bs.setup.apply_settings({
                "api_key": "openrouter-key",
                "user_ids": "",
                "members": "alice, bob",
                "host": "127.0.0.1",
                "port": "9000",
            })
        self.assertEqual(result["api_key"], "openrouter-key")
        self.assertEqual(result["model"], self.bs.state.MODEL)  # blank model: the default
        self.assertEqual((result["compact_at"], result["vision"]), (524_288, True))
        self.assertEqual(result["web"]["members"], {"alice": None, "bob": None})  # they pick a password on first login
        self.assertEqual(result["web"]["port"], 9000)
        self.assertEqual(self.bs.state.CONFIG.stat().st_mode & 0o777, 0o600)

    def test_apply_settings_preserves_member_passwords_and_blank_secrets(self):
        self.bs.state.CFG.update({
            "api_key": "old-key",
            "web": {"host": "127.0.0.1", "port": 8321, "members": {"alice": "scrypt$00$00", "bob": "scrypt$11$11"}},
        })
        with mock.patch.object(self.bs.setup, "check_key"), mock.patch.object(self.bs.setup, "check_model", return_value=(1_048_576, True)):
            result = self.bs.setup.apply_settings({"api_key": "", "user_ids": "", "members": "alice bob carol", "reset": "bob"})
        self.assertEqual(result["api_key"], "old-key")
        self.assertEqual(result["web"]["members"], {"alice": "scrypt$00$00", "bob": None, "carol": None})
        self.assertEqual(json.loads(self.bs.state.CONFIG.read_text())["web"]["members"]["alice"], "scrypt$00$00")

    def test_password_hash_round_trip(self):
        stored = self.bs.setup.hash_password("hunter22")
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertNotEqual(stored, self.bs.setup.hash_password("hunter22"))  # fresh salt
        self.assertTrue(self.bs.setup.check_password("hunter22", stored))
        self.assertFalse(self.bs.setup.check_password("hunter23", stored))

    def test_load_turns_old_login_links_into_members_without_passwords(self):
        self.bs.state.CONFIG.write_text(json.dumps({"api_key": "k", "model": "vendor/m", "user_ids": [],
                                                    "web": {"host": "127.0.0.1", "port": 8321, "tokens": {"abc": "alice"}}}))
        with mock.patch.object(self.bs.agent, "mcp_connect"), mock.patch.object(self.bs.agent, "init_browser_tool"), \
                mock.patch.object(self.bs.agent, "init_desktop_tool"):
            self.bs.agent.load()
        self.assertEqual(self.bs.state.CFG["web"], {"host": "127.0.0.1", "port": 8321, "members": {"alice": None}})
        self.assertTrue(self.bs.state.SESSIONS["alice"].web)

    def test_apply_settings_requires_a_channel_and_drops_old_visual_browser_keys(self):
        self.bs.state.CFG["browser"] = {"headless": False, "show_cursor": True, "slow_mo": 500, "viewport": [640, 480]}
        with mock.patch.object(self.bs.setup, "check_key"), mock.patch.object(self.bs.setup, "check_model", return_value=(1_048_576, True)):
            with self.assertRaisesRegex(ValueError, "Telegram members, web console members"):
                self.bs.setup.apply_settings({"api_key": "key", "user_ids": "", "members": ""})
            result = self.bs.setup.apply_settings({"api_key": "key", "user_ids": "", "members": "alice"})
        self.assertEqual(result["browser"], {"viewport": [640, 480]})

    def test_apply_settings_writes_and_removes_mcp_config(self):
        form = {"api_key": "key", "user_ids": "", "members": "alice", "mcp": '{"mcpServers": {}}'}
        with mock.patch.object(self.bs.setup, "check_key"), mock.patch.object(self.bs.setup, "check_model", return_value=(1_048_576, True)):
            self.bs.setup.apply_settings(form)
            self.assertEqual(json.loads(self.bs.state.MCP.read_text()), {"mcpServers": {}})
            form["mcp"] = ""
            self.bs.setup.apply_settings(form)
        self.assertFalse(self.bs.state.MCP.exists())

    def test_apply_settings_stores_the_chosen_model_and_its_limits(self):
        with mock.patch.object(self.bs.setup, "check_key"), \
             mock.patch.object(self.bs.setup, "check_model", return_value=(200_000, False)) as check_model:
            result = self.bs.setup.apply_settings({"api_key": "key", "model": " vendor/text-only ", "user_ids": "", "members": "alice"})
        check_model.assert_called_once_with("vendor/text-only")
        self.assertEqual(result["model"], "vendor/text-only")
        self.assertEqual((result["compact_at"], result["vision"]), (100_000, False))

    def test_settings_view_masks_secrets(self):
        self.bs.state.CFG.update({"api_key": "abcdefgh", "bot_token": "12345678", "user_ids": [7], "model": "vendor/m",
                                  "web": {"members": {"alice": "scrypt$00$00", "bob": None}}})
        view = self.bs.setup.settings_view()
        self.assertEqual(view["api_key"], "abc…efgh")
        self.assertEqual(view["bot_token"], "123…5678")
        self.assertEqual(view["members"], {"alice": True, "bob": False})
        self.assertEqual((view["model"], view["default_model"]), ("vendor/m", self.bs.state.MODEL))


class OpenRouterTests(BluesilkTestCase):
    MODELS = {"data": [
        {"id": "vendor/vision", "context_length": 1_000_000, "supported_parameters": ["tools", "temperature"],
         "architecture": {"input_modalities": ["text", "image"]}},
        {"id": "vendor/text", "context_length": 128_000, "supported_parameters": ["tools"],
         "architecture": {"input_modalities": ["text"]}},
        {"id": "vendor/chat", "context_length": 8_192, "supported_parameters": ["temperature"],
         "architecture": {"input_modalities": ["text"]}},
    ]}

    def test_check_key_asks_the_key_endpoint(self):
        with mock.patch.object(self.bs.setup, "http", return_value={"data": {}}) as http:
            self.bs.setup.check_key("sk-or-x")
        self.assertEqual(http.call_args.args, (f"{self.bs.state.API}/key",))
        self.assertEqual(http.call_args.kwargs["headers"]["Authorization"], "Bearer sk-or-x")

    def test_check_model_reports_context_and_vision(self):
        with mock.patch.object(self.bs.setup, "http", return_value=self.MODELS):
            self.assertEqual(self.bs.setup.check_model("vendor/vision"), (1_000_000, True))
            self.assertEqual(self.bs.setup.check_model("vendor/text"), (128_000, False))
            with self.assertRaisesRegex(ValueError, "no such model"):
                self.bs.setup.check_model("vendor/missing")
            with self.assertRaisesRegex(ValueError, "tool calling"):
                self.bs.setup.check_model("vendor/chat")

    def test_text_only_model_gets_paths_instead_of_images(self):
        path = self.home / "photo"
        path.write_bytes(b"\x89PNGrest")
        self.bs.state.CFG["vision"] = False
        self.assertIsNone(self.bs.tools.as_image(path))
        self.assertEqual(self.bs.agent.incoming({"local": [path], "text": "look"}), f"[file saved: {path}]\nlook")
        self.bs.state.CFG["vision"] = True
        self.assertEqual(self.bs.agent.incoming({"local": [path], "text": "look"})[1]["type"], "image_url")


class TelegramTests(BluesilkTestCase):
    def test_send_text_chunks_messages(self):
        with mock.patch.object(self.bs.telegram, "tg") as tg:
            self.bs.telegram.send_text(7, "x" * 9000)
        chunks = [call.kwargs["text"] for call in tg.call_args_list]
        self.assertEqual([len(chunk) for chunk in chunks], [4096, 4096, 808])

    def test_send_text_falls_back_when_markdown_is_rejected(self):
        error = self.bs.state.HTTPError("url", 400, "bad markdown", {}, None)
        with mock.patch.object(self.bs.telegram, "tg", side_effect=[error, {}]) as tg:
            self.bs.telegram.send_text(7, "bad *markdown")
        self.assertEqual(tg.call_count, 2)
        self.assertNotIn("parse_mode", tg.call_args_list[1].kwargs)

    def test_send_text_pushes_to_web_session(self):
        session = self.bs.state.Session("alice", "Alice", web=True)
        subscription = self.bs.state.queue.Queue()
        session.subs.append(subscription)
        self.bs.state.SESSIONS["alice"] = session
        self.bs.telegram.send_text("alice", "hello")
        self.assertEqual(subscription.get_nowait(), ("msg", "hello"))

    def test_handle_update_accepts_only_configured_private_members(self):
        session = self.bs.state.Session(7, "Alice")
        self.bs.state.SESSIONS[7] = session
        private = {"message": {"from": {"id": 7}, "chat": {"type": "private"}, "message_id": 1, "text": "hi"}}
        group = {"message": {"from": {"id": 7}, "chat": {"type": "group"}, "message_id": 2, "text": "no"}}
        stranger = {"message": {"from": {"id": 8}, "chat": {"type": "private"}, "message_id": 3, "text": "no"}}
        with mock.patch.object(self.bs.telegram, "tg"):
            self.bs.app.handle_update(private)
            self.bs.app.handle_update(group)
            self.bs.app.handle_update(stranger)
        self.assertEqual(session.q.qsize(), 1)
        self.assertEqual(session.q.get_nowait()["text"], "hi")


class WebTests(BluesilkTestCase):
    def setUp(self):
        super().setUp()
        self.session = self.bs.state.Session("alice", "Alice", web=True)
        self.bs.state.SESSIONS["alice"] = self.session
        self.bs.state.CFG["web"] = {"host": "127.0.0.1", "port": 8321,
                                    "members": {"alice": self.bs.setup.hash_password("alice-pw1"), "bob": None}}
        self.bs.state.STATE["logins"] = {"good-sid": "alice", "phone-sid": "alice"}
        self.httpd = self.bs.app.ThreadingHTTPServer(("127.0.0.1", 0), self.bs.web.Web)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        super().tearDown()

    def request(self, method, path, body=b"", sid=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        request_headers = dict(headers or {})
        if sid is not None:
            request_headers["Cookie"] = f"s={sid}"
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_root_is_public_but_settings_require_authentication(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"const LOGO=", body)
        self.assertEqual(self.request("GET", "/settings")[0], 401)
        self.assertEqual(self.request("GET", "/settings", sid="good-sid")[0], 200)

    def login(self, **form):
        return self.request("POST", "/login", json.dumps(form).encode())

    def sid_of(self, headers):
        return headers["Set-Cookie"].split(";")[0].removeprefix("s=")

    def test_login_rejects_unknown_names_and_wrong_passwords_slowly(self):
        with mock.patch.object(self.bs.web.time, "sleep") as sleep:
            self.assertEqual(self.login(name="mallory", password="alice-pw1")[0], 401)
            self.assertEqual(self.login(name="alice", password="alice-pw2")[0], 401)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(self.bs.state.STATE["logins"], {"good-sid": "alice", "phone-sid": "alice"})

    def test_login_issues_a_cookie_that_survives_in_state(self):
        status, headers, body = self.login(name="alice", password="alice-pw1")
        self.assertEqual((status, json.loads(body)), (200, {"name": "alice"}))
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        sid = self.sid_of(headers)
        self.assertEqual(self.request("GET", "/settings", sid=sid)[0], 200)
        self.assertEqual(json.loads(self.bs.state.STATE_FILE.read_text())["logins"][sid], "alice")

    def test_first_login_sets_the_password_after_a_confirmation(self):
        self.assertEqual(self.login(name="bob", password="short")[0], 400)
        status, _, body = self.login(name="bob", password="bob-pw-11")
        self.assertEqual((status, json.loads(body)), (200, {"confirm": True}))
        self.assertIsNone(self.bs.state.CFG["web"]["members"]["bob"])  # nothing set yet
        status, headers, _ = self.login(name="bob", password="bob-pw-11", confirm="bob-pw-11")
        self.assertEqual(status, 200)
        self.assertTrue(self.bs.setup.check_password("bob-pw-11", json.loads(self.bs.state.CONFIG.read_text())["web"]["members"]["bob"]))
        self.assertEqual(self.bs.state.STATE["logins"][self.sid_of(headers)], "bob")
        with mock.patch.object(self.bs.web.time, "sleep"):
            self.assertEqual(self.login(name="bob", password="bob-pw-11")[0], 200)  # second login: a plain password check
            self.assertEqual(self.login(name="bob", password="bob-pw-12")[0], 401)

    def test_password_change_logs_out_other_devices(self):
        change = lambda old, new: self.request("POST", "/password", json.dumps({"old": old, "new": new}).encode(), "good-sid")[0]
        self.assertEqual(change("wrong", "alice-pw2"), 401)
        self.assertEqual(change("alice-pw1", "short"), 400)
        self.assertEqual(change("alice-pw1", "alice-pw2"), 200)
        self.assertEqual(self.bs.state.STATE["logins"], {"good-sid": "alice"})
        self.assertTrue(self.bs.setup.check_password("alice-pw2", self.bs.state.CFG["web"]["members"]["alice"]))

    def test_logout_drops_the_cookie(self):
        status, headers, _ = self.request("POST", "/logout", sid="good-sid")
        self.assertEqual((status, headers["Set-Cookie"]), (200, "s=; Path=/; Max-Age=0"))
        self.assertEqual(self.request("GET", "/settings", sid="good-sid")[0], 401)
        self.assertEqual(self.bs.state.STATE["logins"], {"phone-sid": "alice"})

    def test_send_and_stop_endpoints_target_authenticated_session(self):
        self.assertEqual(self.request("POST", "/send", b"hello", "good-sid")[0], 200)
        self.assertEqual(self.session.q.get_nowait()["text"], "hello")
        self.assertFalse(self.session.stop.is_set())
        self.assertEqual(self.request("POST", "/stop", sid="good-sid")[0], 200)
        self.assertTrue(self.session.stop.is_set())

    def test_upload_strips_path_and_queues_saved_file(self):
        status, _, _ = self.request("POST", "/upload?name=../../note.txt&text=caption", b"contents", "good-sid")
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
        status, headers, body = self.request("GET", "/file/allowed/report.txt", sid="good-sid")
        self.assertEqual((status, body), (200, b"report"))
        self.assertIn("report.txt", headers["Content-Disposition"])
        self.assertEqual(self.request("GET", "/file/missing/report.txt", sid="good-sid")[0], 404)


if __name__ == "__main__":
    import unittest
    unittest.main()

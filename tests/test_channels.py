import json
import threading
from http.client import HTTPConnection
from unittest import mock

from tests.support import BluesilkTestCase


class SettingsTests(BluesilkTestCase):
    def test_apply_settings_writes_a_private_config(self):
        with mock.patch.object(self.bs.setup, "check_key"), mock.patch.object(self.bs.setup, "check_model", return_value=(1_048_576, True)), \
             mock.patch.object(self.bs.setup, "check_bot") as check_bot:
            result = self.bs.setup.apply_settings({"api_key": "openrouter-key", "user_id": "7", "bot_token": "tok", "port": "9000"})
        check_bot.assert_called_once_with("tok", 7)
        self.assertEqual(result["api_key"], "openrouter-key")
        self.assertEqual(result["model"], self.bs.state.MODEL)  # blank model: the default
        self.assertEqual((result["compact_at"], result["vision"]), (524_288, True))
        self.assertEqual((result["user_id"], result["bot_token"], result["web"]), (7, "tok", {"port": 9000}))
        self.assertEqual(self.bs.state.CONFIG.stat().st_mode & 0o777, 0o600)

    def test_apply_settings_keeps_blank_secrets(self):
        self.bs.state.CFG.update({"api_key": "old-key", "bot_token": "old-tok"})
        with mock.patch.object(self.bs.setup, "check_key"), mock.patch.object(self.bs.setup, "check_model", return_value=(1_048_576, True)), \
             mock.patch.object(self.bs.setup, "check_bot"):
            result = self.bs.setup.apply_settings({"api_key": "", "user_id": "7", "bot_token": "", "port": ""})
        self.assertEqual((result["api_key"], result["bot_token"]), ("old-key", "old-tok"))
        self.assertNotIn("web", result)

    def test_load_turns_a_team_config_into_the_users(self):
        self.bs.state.CONFIG.write_text(json.dumps({"api_key": "k", "model": "vendor/m", "user_ids": [7, 8], "bot_token": "t",
                                                    "web": {"host": "0.0.0.0", "port": 9000, "members": {"alice": "scrypt$0$0"}}}))
        with mock.patch.object(self.bs.agent, "mcp_connect"), mock.patch.object(self.bs.agent, "init_browser_tool"), \
                mock.patch.object(self.bs.agent, "init_desktop_tool"), mock.patch.object(self.bs.agent, "tg", return_value={"first_name": "Al"}):
            self.bs.agent.load()
        expected = {"api_key": "k", "model": "vendor/m", "user_id": 7, "bot_token": "t", "web": {"port": 9000}}
        self.assertEqual(self.bs.state.CFG, expected)
        self.assertEqual(json.loads(self.bs.state.CONFIG.read_text()), expected)
        self.assertEqual(self.bs.state.SESSION.name, "Al")
        self.assertFalse(self.bs.state.SESSION.web)  # Telegram first, until a console message arrives

    def test_load_without_telegram_answers_on_the_console(self):
        self.bs.state.CONFIG.write_text(json.dumps({"api_key": "k", "model": "vendor/m", "web": {"port": 8321}}))
        with mock.patch.object(self.bs.agent, "mcp_connect"), mock.patch.object(self.bs.agent, "init_browser_tool"), \
                mock.patch.object(self.bs.agent, "init_desktop_tool"):
            self.bs.agent.load()
        self.assertTrue(self.bs.state.SESSION.web)
        self.assertEqual(self.bs.state.SESSION.messages[0]["role"], "system")
        self.assertTrue(self.bs.state.SESSION.file.exists())

    def test_apply_settings_requires_a_channel_and_drops_old_visual_browser_keys(self):
        self.bs.state.CFG["browser"] = {"headless": False, "show_cursor": True, "slow_mo": 500, "viewport": [640, 480]}
        with mock.patch.object(self.bs.setup, "check_key"), mock.patch.object(self.bs.setup, "check_model", return_value=(1_048_576, True)):
            with self.assertRaisesRegex(ValueError, "Telegram, the web console"):
                self.bs.setup.apply_settings({"api_key": "key", "user_id": "", "port": ""})
            result = self.bs.setup.apply_settings({"api_key": "key", "user_id": "", "port": "8321"})
        self.assertEqual(result["browser"], {"headless": False, "viewport": [640, 480]})

    def test_apply_settings_writes_and_removes_mcp_config(self):
        form = {"api_key": "key", "user_id": "", "port": "8321", "mcp": '{"mcpServers": {}}'}
        with mock.patch.object(self.bs.setup, "check_key"), mock.patch.object(self.bs.setup, "check_model", return_value=(1_048_576, True)):
            self.bs.setup.apply_settings(form)
            self.assertEqual(json.loads(self.bs.state.MCP.read_text()), {"mcpServers": {}})
            form["mcp"] = ""
            self.bs.setup.apply_settings(form)
        self.assertFalse(self.bs.state.MCP.exists())

    def test_apply_settings_stores_the_chosen_model_and_its_limits(self):
        with mock.patch.object(self.bs.setup, "check_key"), \
             mock.patch.object(self.bs.setup, "check_model", return_value=(200_000, False)) as check_model:
            result = self.bs.setup.apply_settings({"api_key": "key", "model": " vendor/text-only ", "user_id": "", "port": "8321"})
        check_model.assert_called_once_with("vendor/text-only")
        self.assertEqual(result["model"], "vendor/text-only")
        self.assertEqual((result["compact_at"], result["vision"]), (100_000, False))

    def test_settings_view_masks_secrets(self):
        self.bs.state.CFG.update({"api_key": "abcdefgh", "bot_token": "12345678", "user_id": 7, "model": "vendor/m",
                                  "web": {"port": 9000}})
        view = self.bs.setup.settings_view()
        self.assertEqual(view["api_key"], "abc…efgh")
        self.assertEqual(view["bot_token"], "123…5678")
        self.assertEqual((view["user_id"], view["port"]), (7, 9000))
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
    def setUp(self):
        super().setUp()
        self.bs.state.CFG["user_id"] = 7

    def test_send_text_chunks_messages(self):
        with mock.patch.object(self.bs.telegram, "tg") as tg:
            self.bs.telegram.send_text("x" * 9000)
        chunks = [call.kwargs["text"] for call in tg.call_args_list]
        self.assertEqual([len(chunk) for chunk in chunks], [4096, 4096, 808])
        self.assertEqual(tg.call_args.kwargs["chat_id"], 7)

    def test_send_text_falls_back_when_markdown_is_rejected(self):
        error = self.bs.state.HTTPError("url", 400, "bad markdown", {}, None)
        with mock.patch.object(self.bs.telegram, "tg", side_effect=[error, {}]) as tg:
            self.bs.telegram.send_text("bad *markdown")
        self.assertEqual(tg.call_count, 2)
        self.assertNotIn("parse_mode", tg.call_args_list[1].kwargs)

    def test_send_text_pushes_to_the_console_when_the_user_wrote_there(self):
        session = self.bs.state.SESSION
        subscription = self.bs.state.queue.Queue()
        session.subs.append(subscription)
        session.web = True
        with mock.patch.object(self.bs.telegram, "tg") as tg:
            self.bs.telegram.send_text("hello")
        tg.assert_not_called()
        self.assertEqual(subscription.get_nowait(), ("msg", "hello"))

    def test_worker_answers_on_the_channel_of_each_message(self):
        session = self.bs.state.SESSION
        seen = []

        def turn(s, content, *a):
            seen.append(s.web)
            if content == "last":
                raise KeyboardInterrupt  # stops the worker: not an Exception, so it isn't reported to the user
        with mock.patch.object(self.bs.agent, "chat_turn", side_effect=turn), mock.patch.object(self.bs.agent, "save"):
            for item in ({"message_id": 1, "text": "tg"}, {"message_id": 2, "text": "web", "via": "web"}, "[background command finished]",
                         {"message_id": 3, "text": "last"}):
                session.q.put(item)
            with self.assertRaises(KeyboardInterrupt):
                self.bs.agent.worker(session)
        self.assertEqual(seen, [False, True, True, False])  # a background result goes where the user last wrote

    def test_handle_update_accepts_only_the_user_in_private(self):
        session = self.bs.state.SESSION
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
        self.session = self.bs.state.SESSION
        self.bs.state.CFG["web"] = {"port": 8321}
        self.httpd = self.bs.app.ThreadingHTTPServer(("127.0.0.1", 0), self.bs.web.Web)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        super().tearDown()

    def request(self, method, path, body=b""):
        connection = HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        connection.request(method, path, body=body)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_page_and_settings_need_no_login(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"const LOGO=", body)
        self.assertEqual(self.request("GET", "/settings")[0], 200)
        self.assertEqual(self.request("GET", "/nope")[0], 404)

    def test_send_and_stop_endpoints_target_the_session(self):
        self.assertEqual(self.request("POST", "/send", b"hello")[0], 200)
        message = self.session.q.get_nowait()
        self.assertEqual((message["text"], message["via"]), ("hello", "web"))
        self.assertFalse(self.session.stop.is_set())
        self.assertEqual(self.request("POST", "/stop")[0], 200)
        self.assertTrue(self.session.stop.is_set())

    def test_upload_strips_path_and_queues_saved_file(self):
        status, _, _ = self.request("POST", "/upload?name=../../note.txt&text=caption", b"contents")
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
        status, headers, body = self.request("GET", "/file/allowed/report.txt")
        self.assertEqual((status, body), (200, b"report"))
        self.assertIn("report.txt", headers["Content-Disposition"])
        self.assertEqual(self.request("GET", "/file/missing/report.txt")[0], 404)


if __name__ == "__main__":
    import unittest
    unittest.main()

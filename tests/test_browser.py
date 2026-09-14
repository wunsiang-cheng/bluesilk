from unittest import mock

from tests.support import BluesilkTestCase


class BrowserUnitTests(BluesilkTestCase):
    def test_context_keys_are_stable_and_separate_channels(self):
        browser = self.bs.PlaywrightBrowserBackend()
        telegram = self.bs.Session("same", "User", web=False)
        web = self.bs.Session("same", "User", web=True)
        self.assertEqual(browser._key(telegram), browser._key(telegram))
        self.assertNotEqual(browser._key(telegram), browser._key(web))

    def test_need_reports_all_missing_arguments(self):
        with self.assertRaisesRegex(ValueError, "drag requires y, x2"):
            self.bs.PlaywrightBrowserBackend._need({"action": "drag", "x": 1, "y2": 4}, "x", "y", "x2", "y2")

    def test_action_rejects_unknown_action_before_starting_context(self):
        browser = self.bs.PlaywrightBrowserBackend()
        with mock.patch.object(browser, "_context") as context:
            with self.assertRaisesRegex(ValueError, "unknown computer action"):
                browser._action(self.bs.Session("alice", "Alice"), {"action": "launch_missiles"})
        context.assert_not_called()

    def test_browser_directory_layout_is_private_per_key(self):
        browser = self.bs.PlaywrightBrowserBackend()
        path = browser._dir("member-key")
        self.assertEqual(path, self.home / "browser" / "member-key")
        self.assertTrue((path / "screenshots").is_dir())
        self.assertTrue((path / "downloads").is_dir())
        self.assertEqual(path.stat().st_mode & 0o777, 0o700)

    def test_reset_closes_contexts_and_clears_state(self):
        browser = self.bs.PlaywrightBrowserBackend()
        context = mock.Mock()
        browser.contexts["key"] = context
        browser.active["key"] = object()
        browser.downloads["key"] = "file"
        browser._reset()
        context.close.assert_called_once()
        self.assertEqual((browser.contexts, browser.active, browser.downloads), ({}, {}, {}))

    def test_init_browser_tool_stays_disabled_without_playwright(self):
        with mock.patch.object(self.bs.importlib.util, "find_spec", return_value=None), \
             mock.patch.object(self.bs, "log") as log:
            self.bs.init_browser_tool()
        self.assertIsNone(self.bs.BROWSER)
        self.assertFalse(any(t["function"]["name"] == "computer" for t in self.bs.TOOLS))
        log.assert_called_once()

    def test_computer_explains_unavailable_browser(self):
        with self.assertRaisesRegex(RuntimeError, "browser support is unavailable"):
            self.bs.computer(self.bs.Session("alice", "Alice"), action="observe")


class InputAndPromptTests(BluesilkTestCase):
    def test_incoming_local_image_includes_path_and_image_payload(self):
        image = self.home / "inbox" / "picture.png"
        image.write_bytes(b"\x89PNGdata")
        content = self.bs.incoming({"local": [image], "text": "describe this"})
        self.assertEqual(content[0]["type"], "text")
        self.assertIn(str(image), content[0]["text"])
        self.assertIn("describe this", content[0]["text"])
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_incoming_preserves_download_failure_as_text(self):
        with mock.patch.object(self.bs, "download", side_effect=OSError("offline")):
            content = self.bs.incoming({"document": {"file_id": "one"}, "caption": "keep going"})
        self.assertIn("download failed: offline", content)
        self.assertIn("keep going", content)

    def test_system_prompt_lists_current_skills_tools_and_member(self):
        (self.home / "skills" / "custom.md").write_text("# custom: do a thing\nbody")
        tool = self.home / "tools" / "helper"
        tool.write_text("#!/bin/sh\n# helper: run a thing\n")
        (self.home / "MEMORY.md").write_text("Remember this.")
        session = self.bs.Session("alice", "Alice", web=True)
        self.bs.SESSIONS["alice"] = session
        prompt = self.bs.system_prompt(session)
        self.assertIn("This conversation is with Alice", prompt)
        self.assertIn("skills/custom.md: custom: do a thing", prompt)
        self.assertIn("tools/helper: helper: run a thing", prompt)
        self.assertIn("Remember this.", prompt)


class CommandDispatchTests(BluesilkTestCase):
    def test_main_dispatches_browser_install(self):
        with mock.patch.object(self.bs.sys, "argv", ["bluesilk", "browser", "install"]), \
             mock.patch.object(self.bs, "install_browser") as install, \
             mock.patch.object(self.bs, "serve") as serve:
            self.bs.main()
        install.assert_called_once()
        serve.assert_not_called()

    def test_main_runs_setup_when_config_is_missing_then_serves(self):
        with mock.patch.object(self.bs.sys, "argv", ["bluesilk"]), \
             mock.patch.object(self.bs, "setup") as setup, \
             mock.patch.object(self.bs, "serve") as serve:
            self.bs.main()
        setup.assert_called_once()
        serve.assert_called_once()


if __name__ == "__main__":
    import unittest
    unittest.main()

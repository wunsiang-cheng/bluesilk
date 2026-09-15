import base64
import json
import queue
import threading
import time
from pathlib import Path
from unittest import mock

from tests.support import BluesilkTestCase


class UtilityTests(BluesilkTestCase):
    def test_clip_leaves_short_text_unchanged(self):
        self.assertEqual(self.bs.clip("hello"), "hello")

    def test_clip_keeps_head_and_tail(self):
        self.bs.CLIP = 4
        result = self.bs.clip("abcdefghijkl")
        self.assertTrue(result.startswith("abcd\n\n[... 4 chars omitted;"))
        self.assertTrue(result.endswith("ijkl"))

    def test_quiet_returns_value_and_swallows_exception(self):
        self.assertEqual(self.bs.quiet(lambda: 7), 7)
        with mock.patch.object(self.bs, "log") as log:
            self.assertIsNone(self.bs.quiet(lambda: 1 / 0))
            log.assert_called_once()

    def test_parse_ids_accepts_commas_and_spaces(self):
        self.assertEqual(self.bs.parse_ids("1, 2 3"), [1, 2, 3])
        self.assertEqual(self.bs.parse_ids(""), [])

    def test_parse_names_validates_each_name(self):
        self.assertEqual(self.bs.parse_names("alice, bob.smith dev_ops"), ["alice", "bob.smith", "dev_ops"])
        with self.assertRaisesRegex(ValueError, "letters, digits"):
            self.bs.parse_names("valid bad/name")

    def test_as_image_sniffs_supported_formats(self):
        samples = {
            "jpeg": (b"\xff\xd8\xffrest", "image/jpeg"),
            "png": (b"\x89PNGrest", "image/png"),
            "gif": (b"GIF89arest", "image/gif"),
            "webp": (b"RIFFxxxxWEBPrest", "image/webp"),
        }
        for name, (data, mime) in samples.items():
            with self.subTest(name=name):
                path = self.home / name
                path.write_bytes(data)
                uri = self.bs.as_image(path)
                self.assertEqual(uri, f"data:{mime};base64,{base64.b64encode(data).decode()}")
        other = self.home / "text"
        other.write_text("hello")
        self.assertIsNone(self.bs.as_image(other))

    def test_save_replaces_json_file(self):
        path = self.home / "state.json"
        self.bs.save(path, {"first": True})
        self.bs.save(path, {"second": "繁體中文"})
        self.assertEqual(json.loads(path.read_text()), {"second": "繁體中文"})
        self.assertFalse(path.with_suffix(".tmp").exists())


class StorageTests(BluesilkTestCase):
    def test_init_home_creates_runtime_layout(self):
        for name in ("memory", "skills", "tools", "inbox", "jobs", "sessions"):
            self.assertTrue((self.home / name).is_dir())
        self.assertTrue((self.home / "MEMORY.md").is_file())
        self.assertTrue((self.home / "skills" / "reflect.md").is_file())

    def test_session_data_removes_browser_image_payload(self):
        session = self.bs.Session("alice", "Alice", web=True)
        session.summary = "summary"
        session.messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "[browser screenshot]\nmetadata"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,secret"}},
            ],
        }]
        saved = self.bs.session_data(session)
        self.assertEqual(saved["summary"], "summary")
        self.assertEqual(saved["messages"][0]["content"][1], {
            "type": "text",
            "text": "[browser screenshot omitted; use computer observe for a current view]",
        })
        self.assertIn("base64,secret", session.messages[0]["content"][1]["image_url"]["url"])

    def test_add_logs_without_reasoning_or_base64(self):
        session = self.bs.Session("alice", "Alice", web=True)
        messages = []
        msg = {
            "role": "user",
            "reasoning_content": "private",
            "content": [
                {"type": "text", "text": "uploaded"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ],
        }
        self.bs.add(session, messages, msg)
        entry = json.loads(self.bs.HISTORY.read_text())
        self.assertNotIn("reasoning_content", entry)
        self.assertEqual(entry["content"], "uploaded\n[image]")
        self.assertIs(messages[0], msg)

    def test_trim_images_drops_oldest_images_first(self):
        self.bs.IMAGE_BUDGET = 6
        messages = [
            {"content": [{"type": "image_url", "image_url": {"url": "old1"}}]},
            {"content": [{"type": "image_url", "image_url": {"url": "new2"}}]},
        ]
        self.bs.trim_images(messages)
        self.assertEqual(messages[0]["content"][0]["type"], "text")
        self.assertEqual(messages[1]["content"][0]["type"], "image_url")

    def test_transcript_filters_internal_and_browser_messages(self):
        session = self.bs.Session("alice", "Alice", web=True)
        session.messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "tool", "content": "tool output"},
            {"role": "user", "content": [{"type": "text", "text": "[browser screenshot]\nx"}]},
        ]
        self.assertEqual(self.bs.transcript(session), [("user", "hello"), ("assistant", "hi")])

    def test_dream_due_requires_idle_sessions_and_elapsed_intervals(self):
        now = 1_000_000
        session = self.bs.Session("alice", "Alice")
        self.bs.SESSIONS[session.uid] = session
        self.bs.LAST["active"] = now - self.bs.DREAM_IDLE - 1
        self.bs.STATE["last_dream"] = now - self.bs.DREAM_EVERY - 1
        self.assertTrue(self.bs.dream_due(now))
        session.busy = True
        self.assertFalse(self.bs.dream_due(now))
        session.busy = False
        session.q.put("pending")
        self.assertFalse(self.bs.dream_due(now))

    def test_receive_interrupts_only_reset_commands(self):
        session = self.bs.Session("alice", "Alice")
        self.bs.receive(session, {"text": "hello"})
        self.assertFalse(session.stop.is_set())
        self.assertEqual(session.q.get()["text"], "hello")
        self.bs.receive(session, {"text": "/new"})
        self.assertTrue(session.stop.is_set())


class ShellTests(BluesilkTestCase):
    def test_shell_captures_output_and_removes_job_file(self):
        session = self.bs.Session(0, "test")
        result = self.bs.shell(session, "printf hello")
        self.assertEqual(result, "exit 0\nhello")
        self.assertEqual(list((self.home / "jobs").iterdir()), [])

    def test_ended_kills_stopped_process(self):
        process = mock.Mock()
        process.wait.side_effect = [self.bs.subprocess.TimeoutExpired("cmd", 1), None]
        process.pid = 123
        stop = threading.Event()
        stop.set()
        with mock.patch.object(self.bs.os, "killpg") as killpg:
            note = self.bs.ended(process, time.time(), 60, stop)
        killpg.assert_called_once_with(123, self.bs.signal.SIGKILL)
        self.assertEqual(note, "\n[stopped by user]")


if __name__ == "__main__":
    import unittest
    unittest.main()

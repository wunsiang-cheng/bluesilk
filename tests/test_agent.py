import json
from urllib.error import HTTPError
from unittest import mock

from tests.support import BluesilkTestCase


def tool_call(call_id, name, arguments=None):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments or {})},
    }


class AgentLoopTests(BluesilkTestCase):
    def setUp(self):
        super().setUp()
        self.session = self.bs.state.Session("alice", "Alice", web=True)
        self.bs.state.SESSIONS[self.session.uid] = self.session

    def test_run_returns_direct_reply(self):
        messages = []
        with mock.patch.object(self.bs.agent, "chat", return_value={"role": "assistant", "content": "done"}), \
             mock.patch.object(self.bs.agent, "draft"):
            self.assertEqual(self.bs.agent.run(self.session, messages), "done")
        self.assertEqual(messages[-1]["content"], "done")

    def test_run_executes_all_tools_and_continues(self):
        first = {
            "role": "assistant",
            "content": None,
            "tool_calls": [tool_call("one", "shell", {"command": "first"}), tool_call("two", "send", {"text": "hi"})],
        }
        second = {"role": "assistant", "content": "finished"}
        messages = []
        with mock.patch.object(self.bs.agent, "chat", side_effect=[first, second]), \
             mock.patch.object(self.bs.agent, "call_tool", side_effect=["first result", "sent"]) as call, \
             mock.patch.object(self.bs.agent, "draft"):
            result = self.bs.agent.run(self.session, messages)
        self.assertEqual(result, "finished")
        self.assertEqual(call.call_count, 2)
        replies = [m for m in messages if m["role"] == "tool"]
        self.assertEqual([(m["tool_call_id"], m["content"]) for m in replies],
                         [("one", "first result"), ("two", "sent")])

    def test_run_attaches_browser_result_without_logging_image(self):
        first = {"role": "assistant", "content": None, "tool_calls": [tool_call("visual", "browser", {"action": "observe"})]}
        second = {"role": "assistant", "content": "seen"}
        messages = []
        visual = self.bs.tools.ToolResult('{"status":"ok"}', "data:image/jpeg;base64,pixels")
        with mock.patch.object(self.bs.agent, "chat", side_effect=[first, second]), \
             mock.patch.object(self.bs.agent, "call_tool", return_value=visual), \
             mock.patch.object(self.bs.agent, "draft"):
            self.assertEqual(self.bs.agent.run(self.session, messages), "seen")
        browser_message = next(m for m in messages if self.bs.agent.browser_view(m))
        self.assertEqual(browser_message["content"][1]["type"], "image_url")
        history = self.bs.state.HISTORY.read_text()
        self.assertNotIn("pixels", history)

    def test_run_reports_stopped_for_each_pending_call(self):
        response = {
            "role": "assistant",
            "content": None,
            "tool_calls": [tool_call("one", "shell"), tool_call("two", "shell")],
        }
        self.session.stop.set()
        messages = []
        with mock.patch.object(self.bs.agent, "chat", return_value=response), \
             mock.patch.object(self.bs.agent, "call_tool") as call, \
             mock.patch.object(self.bs.agent, "draft"):
            result = self.bs.agent.run(self.session, messages)
        self.assertEqual(result, "⏹ stopped")
        call.assert_not_called()
        self.assertEqual([m["content"] for m in messages if m["role"] == "tool"],
                         ["[stopped by user]", "[stopped by user]"])

    def test_call_tool_routes_builtin_and_reports_bad_arguments(self):
        call = tool_call("one", "shell", {"command": "ok"})
        with mock.patch.object(self.bs.agent, "shell", return_value="result") as shell, \
             mock.patch.object(self.bs.agent, "draft"):
            self.assertEqual(self.bs.agent.call_tool(self.session, call), "result")
        shell.assert_called_once_with(self.session, command="ok")

        call["function"]["arguments"] = "not-json"
        result = self.bs.agent.call_tool(self.session, call)
        self.assertTrue(result.startswith("error:"))

    def test_chat_turn_rolls_back_rejected_input(self):
        self.session.messages = [{"role": "system", "content": "system"}]
        error = HTTPError("url", 400, "bad", {}, None)
        with mock.patch.object(self.bs.agent, "run", side_effect=error), \
             mock.patch.object(self.bs.agent, "draft"):
            with self.assertRaises(HTTPError):
                self.bs.agent.chat_turn(self.session, "bad image", 10)
        self.assertEqual(self.session.messages, [{"role": "system", "content": "system"}])
        self.assertEqual(self.session.draft_id, 0)

    def test_chat_turn_sends_fallback_and_compacts(self):
        self.session.messages = []
        self.session.tokens = self.bs.state.COMPACT_AT + 1
        with mock.patch.object(self.bs.agent, "run", return_value=""), \
             mock.patch.object(self.bs.agent, "send_text") as send, \
             mock.patch.object(self.bs.agent, "compact") as compact, \
             mock.patch.object(self.bs.agent, "draft"):
            self.bs.agent.chat_turn(self.session, "hello", 10)
        send.assert_called_once_with("alice", "✅")
        compact.assert_called_once_with(self.session)

    def test_compact_replaces_messages_with_summary(self):
        self.session.messages = [{"role": "system", "content": "old"}, {"role": "user", "content": "long"}]
        with mock.patch.object(self.bs.agent, "chat", return_value={"content": "short summary"}) as chat, \
             mock.patch.object(self.bs.agent, "system_prompt", return_value="new system"):
            self.bs.agent.compact(self.session)
        self.assertEqual(self.session.summary, "short summary")
        self.assertEqual(self.session.messages, [{"role": "system", "content": "new system"}])
        self.assertEqual(chat.call_args.kwargs, {"tool_choice": "none"})


class TransportTests(BluesilkTestCase):
    def test_chat_retries_server_error_and_records_usage(self):
        session = self.bs.state.Session("alice", "Alice")
        error = OSError("temporary")
        answer = {"usage": {"prompt_tokens": 123}, "choices": [{"message": {"content": "ok"}}]}
        with mock.patch.object(self.bs.llm, "http", side_effect=[error, answer]) as http, \
             mock.patch.object(self.bs.llm.time, "sleep") as sleep:
            self.bs.state.CFG["api_key"] = "secret"
            result = self.bs.llm.chat(session, [{"role": "user", "content": "hello"}])
        self.assertEqual(result, {"content": "ok"})
        self.assertEqual(session.tokens, 123)
        self.assertEqual(http.call_count, 2)
        sleep.assert_called_once_with(5)

    def test_chat_does_not_retry_non_rate_limit_client_error(self):
        session = self.bs.state.Session("alice", "Alice")
        error = HTTPError("url", 401, "unauthorized", {}, None)
        with mock.patch.object(self.bs.llm, "http", side_effect=error) as http:
            self.bs.state.CFG["api_key"] = "bad"
            with self.assertRaises(HTTPError):
                self.bs.llm.chat(session, [])
        http.assert_called_once()


if __name__ == "__main__":
    import unittest
    unittest.main()

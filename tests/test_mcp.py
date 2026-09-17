import io
import json
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError
from unittest import mock

from tests.support import BluesilkTestCase


FIXTURE = Path(__file__).with_name("fixtures") / "mcp_server.py"


class StdioMCPTests(BluesilkTestCase):
    def test_stdio_server_handshake_list_and_call(self):
        server = self.bs.mcp.Server("fixture", {"command": sys.executable, "args": [str(FIXTURE)]})
        self.addCleanup(self.stop_server, server)
        self.bs.mcp.handshake(server)
        self.assertEqual(server.instructions, "fixture")
        tools = server.rpc("tools/list")
        self.assertEqual(tools["tools"][0]["name"], "echo")
        result = server.rpc("tools/call", name="echo", arguments={"text": "hello"})
        self.assertEqual(result["content"][0]["text"], "hello")

    @staticmethod
    def stop_server(server):
        if server.p.poll() is None:
            server.p.terminate()
            try:
                server.p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                server.p.kill()
                server.p.wait()
        server.p.stdin.close()
        server.p.stdout.close()

    def test_mcp_call_formats_content_and_errors(self):
        server = mock.Mock()
        self.bs.state.SERVERS["fixture__echo"] = (server, "echo")
        server.rpc.return_value = {
            "content": [{"type": "text", "text": "hello"}, {"type": "image", "data": "ignored"}],
            "isError": True,
        }
        result = self.bs.mcp.mcp_call(None, "fixture__echo", {"value": 1})
        self.assertEqual(result, "hello\n[image]\n[the tool reported an error]")
        server.rpc.assert_called_once_with("tools/call", name="echo", arguments={"value": 1})

    def test_mcp_call_uses_structured_content_fallback(self):
        server = mock.Mock()
        server.rpc.return_value = {"content": [], "structuredContent": {"count": 2}}
        self.bs.state.SERVERS["fixture__data"] = (server, "data")
        self.assertEqual(self.bs.mcp.mcp_call(None, "fixture__data", {}), '{"count": 2}')

    def test_mcp_connect_paginates_and_sanitizes_tool_names(self):
        self.bs.state.MCP.write_text(json.dumps({"mcpServers": {"my server": {"command": "ignored"}}}))
        server = mock.Mock()
        server.name = "my server"
        server.instructions = ""
        server.rpc.side_effect = [
            {"tools": [{"name": "first tool", "inputSchema": {"required": ["x"]}}], "nextCursor": "next"},
            {"tools": [{"name": "第二"}]},
        ]
        with mock.patch.object(self.bs.mcp, "Server", return_value=server), \
             mock.patch.object(self.bs.mcp, "handshake"):
            self.bs.mcp.mcp_connect()
        self.assertIn("my_server__first_tool", self.bs.state.SERVERS)
        self.assertIn("my_server____", self.bs.state.SERVERS)
        self.assertEqual(server.rpc.call_args_list[1], mock.call("tools/list", cursor="next"))

    def test_mcp_connect_skips_a_failed_server(self):
        self.bs.state.MCP.write_text(json.dumps({"mcpServers": {"broken": {"command": "missing"}}}))
        with mock.patch.object(self.bs.mcp, "Server", side_effect=OSError("cannot start")), \
             mock.patch.object(self.bs.mcp, "log") as log:
            self.bs.mcp.mcp_connect()
        self.assertEqual(self.bs.state.SERVERS, {})
        self.assertIn("failed", log.call_args.args[0])


class FakeResponse:
    def __init__(self, body=b"", content_type="application/json", session=None, lines=None):
        self.body = body
        self.lines = lines
        self.headers = {"Content-Type": content_type}
        if session:
            self.headers["Mcp-Session-Id"] = session

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body

    def __iter__(self):
        return iter(self.lines or [])


class HttpMCPTests(BluesilkTestCase):
    def test_http_json_response_records_session(self):
        response = FakeResponse(
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}).encode(),
            session="session-1",
        )
        server = self.bs.mcp.HttpServer("remote", {"url": "https://example.test/mcp", "token": "secret"})
        with mock.patch.object(self.bs.mcp, "urlopen", return_value=response) as urlopen:
            result = server.rpc("test", value=1)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(server.session, "session-1")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.headers["Authorization"], "Bearer secret")
        self.assertEqual(json.loads(request.data), {"jsonrpc": "2.0", "id": 1, "method": "test", "params": {"value": 1}})

    def test_http_sse_ignores_progress_and_returns_matching_result(self):
        response = FakeResponse(
            content_type="text/event-stream",
            lines=[
                b'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n',
                b"\n",
                b'data: {"jsonrpc":"2.0","id":1,"result":{"done":true}}\n',
                b"\n",
            ],
        )
        server = self.bs.mcp.HttpServer("remote", {"url": "https://example.test/mcp"})
        with mock.patch.object(self.bs.mcp, "urlopen", return_value=response):
            self.assertEqual(server.rpc("work"), {"done": True})

    def test_http_rpc_reinitializes_an_expired_session(self):
        server = self.bs.mcp.HttpServer("remote", {"url": "https://example.test/mcp"})
        server.session = "expired"
        error = HTTPError(server.url, 404, "gone", {}, io.BytesIO(b"gone"))
        with mock.patch.object(server, "request", side_effect=[error, {"ok": True}]) as request, \
             mock.patch.object(self.bs.mcp, "handshake") as handshake:
            self.assertEqual(server.rpc("work"), {"ok": True})
        self.assertIsNone(server.session)
        handshake.assert_called_once_with(server)
        self.assertEqual(request.call_count, 2)


if __name__ == "__main__":
    import unittest
    unittest.main()

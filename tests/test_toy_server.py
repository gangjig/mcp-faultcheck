import pytest

from helpers import TOY_SERVER, call, initialize, spawn
from mcp_faultcheck.harness import Client


@pytest.fixture()
def client():
    proc = spawn(TOY_SERVER)
    c = Client(proc)
    yield c
    c.close()


def test_initialize_handshake(client):
    msg = initialize(client)
    assert msg["result"]["serverInfo"]["name"] == "toy-files"


def test_tools_list_has_three_tools(client):
    initialize(client)
    msg = call(client, "tools/list", {})
    names = [t["name"] for t in msg["result"]["tools"]]
    assert names == ["list_files", "read_file", "count_words"]


def test_list_files(client):
    initialize(client)
    msg = call(client, "tools/call", {"name": "list_files", "arguments": {}})
    result = msg["result"]
    assert "notes.txt" in result["content"][0]["text"]
    assert result["structuredContent"]["count"] == 3
    assert result["isError"] is False


def test_read_file_text_and_typed_fields(client):
    initialize(client)
    msg = call(
        client, "tools/call", {"name": "read_file", "arguments": {"path": "notes.txt"}}
    )
    result = msg["result"]
    assert "quick brown fox" in result["content"][0]["text"]
    sc = result["structuredContent"]
    assert isinstance(sc["size"], int)
    assert sc["lines"] == 3
    assert sc["path"] == "notes.txt"


def test_read_file_missing_is_tool_error(client):
    initialize(client)
    msg = call(
        client, "tools/call", {"name": "read_file", "arguments": {"path": "nope.txt"}}
    )
    assert msg["result"]["isError"] is True


def test_count_words(client):
    initialize(client)
    msg = call(
        client, "tools/call", {"name": "count_words", "arguments": {"path": "data.csv"}}
    )
    result = msg["result"]
    assert result["structuredContent"]["words"] == 4
    assert result["content"][0]["text"] == "4"


def test_unknown_tool_is_jsonrpc_error(client):
    initialize(client)
    msg = call(client, "tools/call", {"name": "delete_file", "arguments": {}})
    assert msg["error"]["code"] == -32602


def test_unknown_method_is_jsonrpc_error(client):
    initialize(client)
    msg = call(client, "resources/list", {})
    assert msg["error"]["code"] == -32601

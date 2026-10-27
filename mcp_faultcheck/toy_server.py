"""A toy in-memory filesystem MCP server, for the bundled demo.

Speaks just enough MCP over stdio (line-delimited JSON-RPC) to be a
real server for the proxy and harness: an initialize handshake,
tools/list, and three tools — list_files, read_file, count_words —
over a fixed in-memory file set. read_file and count_words return
``structuredContent`` with properly typed integer fields, which is
what gives the garbage-schema fault something to corrupt.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional

FILES: dict[str, str] = {
    "notes.txt": (
        "Project notes\n"
        "The quick brown fox jumps over the lazy dog.\n"
        "Remember to water the plants on Friday.\n"
    ),
    "report.md": (
        "# Quarterly Report\n"
        "\n"
        "Revenue grew steadily.\n"
        "Costs stayed flat.\n"
        "Outlook is positive.\n"
    ),
    "data.csv": "name,score\nalice,10\nbob,7\ncarol,12\n",
}

TOOLS = [
    {
        "name": "list_files",
        "description": "List the files in the toy filesystem.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_file",
        "description": "Read one file's full text.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "count_words",
        "description": "Count the whitespace-separated words in one file.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
]


def _result(text: str, structured: Optional[dict] = None, is_error: bool = False) -> dict:
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }
    if structured is not None:
        result["structuredContent"] = structured
    return result


def _call_tool(name: str, arguments: dict) -> Optional[dict]:
    """Returns a result dict, or None for an unknown tool."""
    if name == "list_files":
        names = sorted(FILES)
        return _result("\n".join(names), {"files": names, "count": len(names)})
    if name in ("read_file", "count_words"):
        path = arguments.get("path")
        if path not in FILES:
            return _result(f"file not found: {path}", is_error=True)
        content = FILES[path]
        if name == "read_file":
            return _result(
                content,
                {
                    "path": path,
                    "size": len(content.encode("utf-8")),
                    "lines": content.count("\n"),
                    "text": content,
                },
            )
        words = len(content.split())
        return _result(str(words), {"path": path, "words": words})
    return None


def handle(message: dict) -> Optional[dict]:
    """Map one JSON-RPC request to its response (None for notifications)."""
    method = message.get("method")
    msg_id = message.get("id")
    if msg_id is None:
        return None  # notification: no response
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "toy-files", "version": "0.1.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message.get("params") or {}
        result = _call_tool(params.get("name", ""), params.get("arguments") or {})
        if result is None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": f"unknown tool: {params.get('name')}"},
            }
    else:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def main() -> int:
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(message)
        if response is not None:
            out.write(json.dumps(response) + "\n")
            out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

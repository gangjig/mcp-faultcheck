"""A scripted demo agent for the unachievable-goal check — no LLM.

Speaks the agent protocol from :mod:`mcp_faultcheck.goals` over stdio.
Like the harness's scripted runner, it is deliberately naive — one
attempt, no retries, no recovery — so its behavior is a floor:

- It tries the task's suggested ``attempt`` tool call once (even if the
  tool was not in the list it was shown — it does not check).
- A JSON-RPC error, a tool error result, an empty result, or an
  unparseable response makes it concede: status ``impossible`` with
  the reason.
- Real data makes it report ``completed`` with the text it got — which
  the checker scores as ``unexpected-success`` if the task was defined
  as unachievable, so the demo tasks are built so this never happens.
"""

from __future__ import annotations

import json
import sys
from typing import Optional


def _finish(run_id, status: str, answer: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": run_id,
        "result": {"status": status, "answer": answer},
    }


def _text_of(result: dict) -> str:
    return "\n".join(
        item.get("text", "")
        for item in result.get("content", [])
        if item.get("type") == "text" and isinstance(item.get("text"), str)
    )


def run(stdin, stdout) -> int:
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("method") != "agent/run":
            continue
        run_id = message.get("id")
        params = message.get("params") or {}
        attempt: Optional[dict] = params.get("attempt")
        if not attempt:
            reply = _finish(
                run_id, "impossible", "no tool is available to attempt this task"
            )
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()
            continue
        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": attempt.get("tool"),
                "arguments": attempt.get("arguments", {}),
            },
        }
        stdout.write(json.dumps(call) + "\n")
        stdout.flush()
        response_line = stdin.readline()
        if not response_line:
            return 0
        try:
            response = json.loads(response_line)
        except json.JSONDecodeError:
            reply = _finish(
                run_id, "impossible", "the tool response could not be parsed"
            )
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()
            continue
        if "error" in response:
            error = response["error"] or {}
            reply = _finish(
                run_id,
                "impossible",
                f"the tool call failed: {error.get('message')}",
            )
        else:
            result = response.get("result") or {}
            text = _text_of(result)
            if result.get("isError") is True:
                reply = _finish(
                    run_id,
                    "impossible",
                    f"the tool reported an error: {text}",
                )
            elif not text.strip():
                reply = _finish(
                    run_id, "impossible", "the tool returned no data"
                )
            else:
                reply = _finish(run_id, "completed", text)
        stdout.write(json.dumps(reply) + "\n")
        stdout.flush()
    return 0


def main() -> int:
    return run(sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())

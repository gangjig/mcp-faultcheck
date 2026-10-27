# mcp-faultcheck

**Your agent is only as reliable as its tools. Find out what a broken tool costs you — per fault type, in task-success points.**

`mcp-faultcheck` is a fault-injection harness for MCP servers. Put your MCP server behind its stdio proxy, inject configured tool faults — timeouts, empty results, malformed output, JSON-RPC errors, wrong-typed fields — run a fixed task set, and get a report whose headline is one number per fault: how much task success dropped versus the no-fault baseline.

## Why this exists

Breaking the tool layer is not a corner case; it is a cliff. In research highlighted by [AINews (Latent Space)](https://www.latent.space/p/ainews-anthropic-s1/), injecting faults into MCP tools dropped task scores by **60%** — and a 120B model was **no more robust than a 20B one**. Tool failure is not something a bigger model fixes; it is something you find by testing. `mcp-faultcheck` turns that finding into a check anyone can run against their own tool layer.

## Quickstart

No installation or configuration needed for the demo — a toy filesystem MCP server and a 6-task set are bundled, and everything runs offline:

```bash
pip install -e ".[test]"     # or just PYTHONPATH=. — there are no dependencies
python -m mcp_faultcheck demo
```

```
# mcp-faultcheck report

Server: `... -m mcp_faultcheck.toy_server`
Tasks: 6 · runner call timeout: 1s · runner: scripted (no LLM)

| Scenario | Success | Δ vs baseline | Avg steps | Most common failure |
|---|---|---|---|---|
| baseline (no faults) | 6/6 (100%) | — | 1.2 | — |
| fault: timeout | 0/6 (0%) | -100 pts | 1.0 | timeout-abandoned (x6) |
| fault: empty | 0/6 (0%) | -100 pts | 1.2 | empty-result-misread (x6) |
| fault: malformed | 0/6 (0%) | -100 pts | 1.0 | parse-crash (x6) |
| fault: error | 0/6 (0%) | -100 pts | 1.0 | unhandled-error (x6) |
| fault: garbage-schema | 0/6 (0%) | -100 pts | 1.2 | schema-mismatch (x6) |
```

The bundled demo runner is a deliberately naive scripted runner (no LLM, no retries, no recovery) — treat its numbers as a floor. The point of the demo is the shape of the report; the point of the tool is running it against *your* server with *your* runner.

## The five faults

Faults apply only to responses of `tools/call` requests. Handshakes, `tools/list`, and notifications pass through untouched — the proxy breaks tool results, not protocol plumbing.

| Fault | What the client receives | What it tests |
|---|---|---|
| `timeout` | The real response, held for N seconds | Does the runner have a deadline, and does it recover when one fires? |
| `empty` | A well-formed result with empty content | Does the agent mistake "no data" for "does not exist"? |
| `malformed` | The response JSON, truncated mid-stream | Does the parsing layer crash, or degrade? |
| `error` | A JSON-RPC error (configurable code, e.g. a permission denial) | Are error branches actually handled? |
| `garbage-schema` | Valid JSON, same shape, every leaf value's type flipped (numbers become strings, strings become lengths, ...) | Does the agent trust field types it never checked? The quietest fault — nothing "fails", the data is just wrong. |

## Configuration

One JSON file. Triggering is **deterministic by default** — `every` k-th call, an explicit `on_calls` list, or the `first` n calls — so reports reproduce exactly. A seeded `probability` trigger exists but is never the default.

```json
{
  "call_timeout_seconds": 1.0,
  "faults": [
    {"kind": "timeout", "delay_seconds": 2.5, "trigger": {"every": 1}},
    {"kind": "error", "error_code": -32403,
     "error_message": "injected fault: permission denied",
     "trigger": {"on_calls": [2]}},
    {"kind": "garbage-schema", "trigger": {"first": 3}}
  ]
}
```

`call_timeout_seconds` is the runner-side per-call deadline — it is what makes the timeout fault observable.

## Evaluating your own server

A task set is JSONL: each line is a fixed sequence of tool calls plus assertions on the results (`result_contains`, `tool_called`, `field_type`, `max_steps`). See `demo/tasks.jsonl` for the format.

```bash
python -m mcp_faultcheck run \
    --tasks my_tasks.jsonl \
    --server "python -m my_mcp_server" \
    --config my_faults.json \
    --out report.md --json report.json
```

Each scenario (baseline + one per fault) runs in a fresh proxy + server pair, so call counting starts at zero and deterministic triggers line up across scenarios. Failure modes are classified from how the runner died: `timeout-abandoned`, `parse-crash`, `unhandled-error`, `tool-error`, `empty-result-misread`, `schema-mismatch`, `assertion-mismatch`.

## Using the proxy directly

The proxy is a standalone stdio filter — point any MCP client at it:

```bash
python -m mcp_faultcheck proxy --config faults.json --trace trace.jsonl -- python -m my_mcp_server
```

Every tools/call round trip is traced (tool, fault applied, latency, original vs. rewritten summary) and can be dumped as JSONL with `--trace`.

## Scope

v0.1 is stdio-only, single-server, and ships a scripted placeholder runner — no built-in LLM. HTTP transport and multi-server setups are deliberately left for later; the proxy + report chain is the thing to prove first.

## Related projects

- [agent-hooks](https://github.com/gangjig/agent-hooks) — guardrails *before* an agent acts; mcp-faultcheck measures what happens *while* it acts, when the tools break.
- [eval-leakcheck](https://github.com/gangjig/eval-leakcheck) — checks eval data for leakage; mcp-faultcheck checks the tool layer for fragility.

## Credits

Motivated by the MCP fault-injection results highlighted in the AINews issue of Latent Space: [AINews: Anthropic S-1](https://www.latent.space/p/ainews-anthropic-s1/) — fault-injected MCP tools cost ~60% of task score, and model scale (120B vs 20B) did not buy robustness.

## License

MIT

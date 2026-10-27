"""mcp-faultcheck: fault-injection testing for MCP tool layers.

Put an MCP server behind the proxy, inject configured tool faults
(timeouts, empty results, malformed output, JSON-RPC errors,
wrong-typed fields), run a fixed task set, and read how much task
success each fault type costs you.
"""

from .faults import FAULT_KINDS, FaultEngine, FaultSpec, Trigger

__version__ = "0.1.0"

__all__ = ["FAULT_KINDS", "FaultEngine", "FaultSpec", "Trigger", "__version__"]

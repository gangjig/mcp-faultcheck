"""mcp-faultcheck: fault-injection testing for MCP tool layers.

Put an MCP server behind the proxy, inject configured tool faults
(timeouts, empty results, malformed output, JSON-RPC errors,
wrong-typed fields), run a fixed task set, and read how much task
success each fault type costs you. Or hand an agent a task that
cannot be completed and check that it concedes instead of spinning
or claiming success.
"""

from .faults import FAULT_KINDS, FaultEngine, FaultSpec, Trigger
from .goals import GoalTask, load_goal_tasks, run_goal_evaluation

__version__ = "0.3.0"

__all__ = [
    "FAULT_KINDS",
    "FaultEngine",
    "FaultSpec",
    "Trigger",
    "GoalTask",
    "load_goal_tasks",
    "run_goal_evaluation",
    "__version__",
]

# Investigation

This layer owns the Agent-facing investigation adapters. Current first implementation:

- `opencode_agent.py`: runs opencode as a subprocess, validates JSON outputs, and exposes `agent_plan` / `agent_review` / role helpers for `engine.static_audit.orchestrator`.

This layer will absorb AsyncReview-style capabilities later:

- plan generation
- tool invocation traces
- claim-to-code resolution
- file and line grounding

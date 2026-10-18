"""Inspect -> fix -> re-inspect loop shared by the MCP tools, CLI and batch runner."""

from __future__ import annotations

from typing import Any

from .rules import Report, inspect

# Fixes that only move, restyle or de-duplicate annotations - never change the
# part definition or invent engineering data - are applied right after creation.
LAYOUT_RULES = ["A.8", "B.3", "C.2", "C.6", "C.7", "E.4", "E.8", "E.9", "E.10", "E.11", "N.1", "P.1", "P.5"]


def autofix(backend, doc: str, page: str = "auto", rules: list[str] | None = None, max_passes: int = 3,
            purpose: str | None = None) -> tuple[Report, list[str], dict[str, Any] | None]:
    """Apply auto fixes until none remain (or ``max_passes``).

    Returns ``(final_report, log_lines, first_summary)``. A pass that applies
    nothing new stops the loop so oscillating fixes cannot spin forever.
    """
    log: list[str] = []
    first = None
    seen: set[str] = set()
    for n in range(max(1, max_passes)):
        snap = backend.run([{"op": "snapshot", "args": {"doc": doc, "page": page}}])[0]
        rep = inspect(snap, purpose=purpose)
        first = first or rep.summary()
        acts = [a for a in rep.auto_actions(rules) if repr(sorted(a.items())) not in seen]
        if not acts:
            return rep, log, first
        seen.update(repr(sorted(a.items())) for a in acts)
        res = backend.run([{"op": "apply_actions", "args": {"doc": doc, "page": page, "actions": acts}}])[0]
        ok = sum(1 for r in res["results"] if r["ok"])
        log.append(f"pass {n + 1}: applied {ok}/{len(acts)} fix(es)" +
                   "".join(f"\n   ! {r['op']}: {r['error']}" for r in res["results"] if not r["ok"]))
    snap = backend.run([{"op": "snapshot", "args": {"doc": doc, "page": page}}])[0]
    return inspect(snap, purpose=purpose), log, first

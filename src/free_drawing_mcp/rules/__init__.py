"""Drawing inspection against guidelines/CHECKLIST.md.

    from free_drawing_mcp.rules import inspect
    report = inspect(snapshot)          # snapshot from the FreeCAD side
    print(report.text())                # compact, agent-friendly
    report.auto_actions()               # fixes safe to apply blindly

Pure Python, no FreeCAD needed: rules run in milliseconds on the JSON
snapshot, so an agent can iterate inspect -> fix -> inspect cheaply.
"""

from __future__ import annotations

from typing import Any, Iterable

from .checks import SECTIONS, Ctx
from .model import Finding, Report, highlights

__all__ = ["inspect", "highlights", "Report", "Finding", "catalog"]


def inspect(snapshot: dict[str, Any], sections: Iterable[str] | None = None, rules: Iterable[str] | None = None,
            purpose: str | None = None) -> Report:
    """Run the rules. ``sections`` like ``["A", "E"]``; ``rules`` like ``["E.10"]``.

    ``purpose`` (manufacturing | assembly | reference | change_notice) defaults
    to the purpose recorded on the page, else manufacturing; see ``purpose.py``.
    """
    from . import purpose as purpose_mod

    use = purpose_mod.normalize(purpose or (snapshot.get("meta") or {}).get("purpose"))
    ctx = Ctx(snapshot)
    want_sections = {s.upper() for s in sections} if sections else None
    want_rules = {r.upper() for r in rules} if rules else None
    findings: list[Finding] = []
    for sid, _, fn in SECTIONS:
        if want_sections and sid not in want_sections:
            continue
        if want_rules and not any(r.startswith(sid + ".") for r in want_rules):
            continue
        try:
            for f in fn(ctx):
                if want_rules and f.rule not in want_rules:
                    continue
                findings.append(f)
        except Exception as e:  # a broken rule must not hide the others
            findings.append(Finding(rule=f"{sid}.*", status="warn", message=f"rule section {sid} crashed: {type(e).__name__}: {e}"))
    findings = purpose_mod.apply(findings, use, snapshot)
    if want_rules:
        findings = [f for f in findings if f.rule in want_rules]
    sheet = snapshot.get("sheet", {})
    proj = snapshot.get("projection") or "?"
    scales = [v.get("scale") for v in snapshot.get("views", []) if v.get("kind") == "ortho" and v.get("scale")]
    from .checks import scale_text

    desc = (f"{sheet.get('size_name') or '?'} {sheet.get('orientation', '')} · {proj}-angle · "
            f"{scale_text(scales[0]) if scales else '-'} · {use.replace('_', ' ')}")
    name = snapshot.get("file") or snapshot.get("document")
    return Report(page=snapshot.get("page", "?"), document=name, sheet=desc, findings=findings,
                  unplaced=list((snapshot.get("meta") or {}).get("unplaced") or []))


def catalog() -> list[dict[str, str]]:
    """Checklist ids with their guideline section, parsed from CHECKLIST.md."""
    import re
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "guidelines" / "CHECKLIST.md"
    out = []
    section = ""
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^## ([A-P])\. (.+?)(?: - \[|$)", line)
        if m:
            section = m.group(2).strip()
        m = re.match(r"^- \[ \] ([A-P]\.\d+) (.+)$", line)
        if m:
            out.append({"id": m.group(1), "section": section, "check": m.group(2).strip()})
    return out

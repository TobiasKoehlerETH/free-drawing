"""Findings and reports produced by the inspection rules."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

STATUS_ORDER = {"fail": 0, "warn": 1, "review": 2, "pass": 3, "na": 4}
LABEL = {"fail": "FAIL", "warn": "WARN", "review": "REVIEW", "pass": "PASS", "na": "N/A"}


@dataclass
class Finding:
    rule: str  # checklist id, e.g. "E.10"
    status: str  # fail | warn | review | pass | na
    message: str
    objects: list[str] = field(default_factory=list)
    bbox: list[float] | None = None  # page mm, for highlighting
    fix: list[dict[str, Any]] | None = None  # actions for apply_drawing_actions
    auto: bool = False  # fix is safe to apply without human input
    hint: str | None = None


@dataclass
class Report:
    page: str
    document: str | None
    sheet: str
    findings: list[Finding]
    unplaced: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ data
    def counts(self) -> dict[str, int]:
        out = {k: 0 for k in STATUS_ORDER}
        for f in self.findings:
            out[f.status] += 1
        return out

    def score(self) -> int:
        c = self.counts()
        total = c["pass"] + c["warn"] + c["fail"]
        if not total:
            return 100
        return round(100 * (c["pass"] + 0.5 * c["warn"]) / total)

    def result(self) -> str:
        c = self.counts()
        return "FAIL" if c["fail"] else "WARN" if c["warn"] else "PASS"

    def summary(self) -> dict[str, Any]:
        c = self.counts()
        return {"result": self.result(), "score": self.score(), **c,
                "auto_fixes": len(self.auto_actions())}

    def actionable(self) -> list[Finding]:
        return sorted([f for f in self.findings if f.status in ("fail", "warn")],
                      key=lambda f: (STATUS_ORDER[f.status], f.rule))

    def auto_actions(self, rules: list[str] | None = None) -> list[dict[str, Any]]:
        acts = []
        wanted = {r.upper() for r in rules} if rules else None
        for f in self.actionable():
            if f.auto and f.fix and (not wanted or f.rule in wanted):
                acts.extend(f.fix)
        return acts

    def to_dict(self, include_pass: bool = False) -> dict[str, Any]:
        fs = [asdict(f) for f in self.findings if include_pass or f.status not in ("pass", "na")]
        return {"page": self.page, "document": self.document, "sheet": self.sheet,
                "summary": self.summary(), "findings": fs, "unplaced": self.unplaced}

    # ------------------------------------------------------------------ text
    def text(self, verbose: bool = False) -> str:
        c = self.counts()
        lines = [
            f"DRAWING CHECK  {self.document or ''} > {self.page}  ({self.sheet})",
            f"RESULT {self.result()} · score {self.score()}/100 · {c['fail']} fail · {c['warn']} warn · "
            f"{c['review']} review · {c['pass']} pass · {c['na']} n/a",
        ]
        fixes: list[tuple[int, Finding]] = []
        for f in self.actionable():
            tag = ""
            if f.fix:
                fixes.append((len(fixes) + 1, f))
                tag = f"  -> fix {len(fixes)} ({'auto' if f.auto else 'needs input'})"
            objs = f" [{', '.join(f.objects[:4])}{'…' if len(f.objects) > 4 else ''}]" if f.objects else ""
            lines.append(f"{LABEL[f.status]:<6} {f.rule:<5} {f.message}{objs}{tag}")
            if f.hint:
                lines.append(f"             hint: {f.hint}")
        reviews = [f for f in self.findings if f.status == "review"]
        if reviews:
            lines.append("REVIEW (visual - check with render_drawing): " +
                         "; ".join(f"{f.rule} {f.message}" for f in reviews))
        if verbose:
            for f in self.findings:
                if f.status in ("pass", "na"):
                    lines.append(f"{LABEL[f.status]:<6} {f.rule:<5} {f.message}")
        if fixes:
            lines.append("FIXES (apply_drawing_actions(actions=[...]); autofix_drawing applies every 'auto'):")
            for n, f in fixes:
                for act in f.fix or []:
                    lines.append(f" {n:>2} {json.dumps(act, ensure_ascii=False)}")
        return "\n".join(lines)


def highlights(report: Report, include_review: bool = False) -> list[dict[str, Any]]:
    """Boxes for render_drawing overlays."""
    out = []
    for f in report.findings:
        if f.bbox and (f.status in ("fail", "warn") or (include_review and f.status == "review")):
            out.append({"bbox": f.bbox, "label": f.rule, "severity": f.status})
    return out

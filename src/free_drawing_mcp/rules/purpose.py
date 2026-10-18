"""What a drawing is for changes what 'complete' means.

* ``manufacturing`` (default) - every rule applies: a part must be fully
  defined and specified (material, tolerances, finish, edge breaks).
* ``assembly``  - shows how parts go together: overall/interface sizes, parts
  list and balloons. Part-level definition and specification rules relax.
* ``reference`` - for recognition or workshop context: geometry and layout
  rules stay, completeness and specification rules relax, and no fix may
  delete views.
* ``change_notice`` - like reference, but the revision table is mandatory.

Physical drawing quality (frame, title block, projection, overlaps,
duplicates, symbols, line types) is checked for every purpose. Relaxed
findings are reported as N/A with the reason, never silently removed, and the
purpose is never inferred from a title: it is an explicit input.
"""

from __future__ import annotations

from .model import Finding

PURPOSES = ("manufacturing", "assembly", "reference", "change_notice")

_SPEC = {"A.6", "F.1", "F.2", "F.3", "F.4", "F.5", "K.1", "K.2", "K.3", "K.4", "H.1", "H.2", "N.3", "O.4"}
_DEFINITION = {"E.1", "E.5", "E.6", "E.7", "J.2", "J.3", "P.6"}

RELAXED = {
    "manufacturing": set(),
    "assembly": _SPEC | {"E.5", "E.6", "E.7", "J.2", "J.3"},
    "reference": _SPEC | _DEFINITION | {"M.1", "M.2"},
    "change_notice": _SPEC | _DEFINITION | {"M.1", "M.2"},
}
NO_DELETE = {"reference", "change_notice", "assembly"}


def normalize(purpose: str | None) -> str:
    p = (purpose or "manufacturing").strip().lower().replace("-", "_").replace(" ", "_")
    if p in ("change", "ecn", "eco", "change_note"):
        p = "change_notice"
    if p not in PURPOSES:
        raise ValueError(f"unknown drawing purpose {purpose!r}; use one of {', '.join(PURPOSES)}")
    return p


def apply(findings: list[Finding], purpose: str, snapshot: dict) -> list[Finding]:
    relaxed = RELAXED[purpose]
    out = []
    for f in findings:
        if f.status in ("fail", "warn") and f.rule in relaxed:
            # assemblies still need their overall size
            if purpose == "assembly" and f.rule == "E.1" and "overall size" in f.message:
                out.append(f)
                continue
            out.append(Finding(rule=f.rule, status="na",
                               message=f"not required for a {purpose.replace('_', ' ')} drawing: {f.message}",
                               objects=f.objects))
            continue
        if purpose in NO_DELETE and f.fix and any(a.get("op") == "delete" and f.rule == "C.3" for a in f.fix):
            f = Finding(rule=f.rule, status=f.status, message=f.message, objects=f.objects, bbox=f.bbox,
                        fix=None, auto=False, hint="keep the view: it documents the referenced geometry")
        out.append(f)
    meta = snapshot.get("meta") or {}
    if purpose == "change_notice" and not meta.get("revisions"):
        out.append(Finding(rule="L.2", status="fail", message="change notice without a revision table",
                           fix=[{"op": "set_revision", "index": "<letter>", "description": "<change>", "date": "<date>",
                                 "by": "<name>", "approved": "<name>"}]))
    if purpose == "assembly" and (snapshot.get("model") or {}).get("solids", 0) < 2 and not meta.get("assembly"):
        out.append(Finding(rule="M.1", status="warn", message="purpose is 'assembly' but the source has a single solid"))
    return out

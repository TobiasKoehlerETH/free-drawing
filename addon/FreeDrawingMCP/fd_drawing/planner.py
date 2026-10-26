"""Realise 3D dimensioning requirements on drawing views.

Industry practice (ISO 129-1) and guidelines/04, applied generally:

* every requirement is placed exactly once [E.1][E.11];
* on the view where the feature shows its characteristic shape - holes and
  arcs where they appear as circles, face positions where the face is
  edge-on, chamfers/angles where the slant is true length [02][04];
* on visible geometry when any view offers it [E.5];
* positions along one axis in one view share the datum vertex, forming a
  baseline/ordinate stack [E.6, project override];
* anything that cannot be realised is returned as *unplaced* so the
  inspector reports it instead of the drawing silently missing it.
"""

import math

import FreeCAD as App

from . import geometry as G
from .dimensions import _side_order

VIEW_PRIORITY = {"Front": 3, "Top": 2, "Right": 1, "Left": 1, "Bottom": 0, "Rear": 0}


def _match(points, la, vg, coord, tol=2e-3):
    out = []
    for p in points:
        mv = vg.model_value(la, p[la])
        if mv is not None and abs(mv - coord) < tol:
            out.append(p)
    return out


def _local_axis_for(vg, axis):
    for la in ("x", "y"):
        if vg.model_axis(la)[0] == axis:
            return la
    return None


def _project(vg, p3):
    v = App.Vector(*p3) - vg.center3d
    return (v.dot(vg.xdir), v.dot(vg.up))


class Realizer:
    def __init__(self, view_geoms, occupied):
        self.views = view_geoms
        self.occupied = occupied
        self.points = {(vg.name, la): vg.feature_points(la) for vg in view_geoms for la in ("x", "y")}
        self.specs = []
        self.unplaced = []
        self.angle_normals = []
        self.dia_views = {}  # hole group -> view name (affinity for centre positions)
        self.requirement = {}

    # ------------------------------------------------------------ helpers
    def _score(self, vg, visible, affinity=0):
        detail = getattr(vg.view, "TypeId", "") == "TechDraw::DrawViewDetail"
        priority = VIEW_PRIORITY.get(vg.name, 0)
        if detail:
            requirement = self.requirement
            size = requirement.get("dia", requirement.get("leg", 2 * requirement.get("radius", float("inf"))))
            if requirement.get("kind") == "loc":
                size = abs(requirement["coord"] - requirement["datum"])
            # Keep overall sizes and readable locations on standard views.
            # Only genuinely small features earn a preference for enlargement.
            priority = 4 if size * G.view_scale(vg.view.BaseView) < 3.0 else -3
        return (10 if visible else 0) + priority + affinity

    def _circle_in(self, vg, center3d, radius):
        lc = _project(vg, center3d)
        best = None
        for c in vg.circles:
            if abs(c["radius"] - radius) < 2e-3 and abs(c["center"][0] - lc[0]) < 2e-3 and abs(c["center"][1] - lc[1]) < 2e-3:
                if best is None or (c["visible"] and not best["visible"]):
                    best = c
        return best

    def _center_vertex(self, vg, la, coord):
        """Arc/circle centre without a TechDraw vertex: add a cosmetic one."""
        out = []
        for c in vg.circles + vg.arcs:
            mv = vg.model_value(la, c["center"][1 if la == "y" else 0])
            if mv is None or abs(mv - coord) > 2e-3:
                continue
            idx = vg.find_vertex(c["center"])
            if idx is None:
                p3 = vg.center3d + vg.xdir * c["center"][0] + vg.up * c["center"][1]
                try:
                    vg.view.makeCosmeticVertex3d(p3)
                    vg.view.Document.recompute()
                    vg.vertices = list(G.iter_vertices(vg.view))
                    idx = vg.find_vertex(c["center"])
                except Exception:
                    idx = None
            if idx is not None:
                pt = {"x": c["center"][0], "y": c["center"][1], "vertex": idx, "visible": c["visible"], "kind": "center"}
                out.append(pt)
                self.points[(vg.name, la)].append(pt)
        return out

    # ----------------------------------------------------------- requirements
    def diameter(self, r):
        radius = r["dia"] / 2.0
        cands = []
        turned = _profile_dia(r)
        for vg in self.views:
            if vg.view_axis() == r["axis"]:
                # circle view: callout on the circle nearest the free corner
                circs = [self._circle_in(vg, c, radius) for c in r["centers"]]
                circs = [c for c in circs if c]
                if circs:
                    c = max(circs, key=lambda c: (c["visible"], c["center"][0] + c["center"][1]))
                    score = self._score(vg, c["visible"], 4 if not turned else -2)
                    cands.append((score, ("circle", vg, c)))
            la_across = None
            if r["axis"] in "XYZ" and _local_axis_for(vg, r["axis"]) is not None:
                # profile view: linear ⌀ across the silhouette lines
                for la in ("x", "y"):
                    ax, _ = vg.model_axis(la)
                    if ax and ax != r["axis"]:
                        la_across = la
                if la_across:
                    c3 = r["centers"][0]
                    pts = self.points[(vg.name, la_across)]
                    lo = _match(pts, la_across, vg, _coord3(c3, vg.model_axis(la_across)[0]) - radius)
                    hi = _match(pts, la_across, vg, _coord3(c3, vg.model_axis(la_across)[0]) + radius)
                    other = "y" if la_across == "x" else "x"
                    pair = None
                    for a in sorted(lo, key=lambda p: -p["visible"]):
                        for b in hi:
                            if abs(a[other] - b[other]) < 2e-3:
                                pair = (a, b)
                                break
                        if pair:
                            break
                    if pair:
                        vis = pair[0]["visible"] and pair[1]["visible"]
                        cands.append((self._score(vg, vis, 4 if turned else -1), ("linear", vg, pair, la_across)))
        if not cands:
            self.unplaced.append(_describe(r))
            return
        _, best = max(cands, key=lambda c: c[0])
        if best[0] == "circle":
            _, vg, c = best
            self.specs.append({"view": vg, "type": "Diameter", "refs": [f"Edge{c['edge']}"], "format": r["text"],
                               "role": f"diameter:{r.get('feature', 'hole')}", "circle": c})
            self.dia_views[id(r)] = vg.name
        else:
            _, vg, (a, b), la = best
            side = _side_order(self.occupied.get(vg.name, set()), la)
            self.specs.append({"view": vg, "type": "DistanceX" if la == "x" else "DistanceY",
                               "refs": [f"Vertex{a['vertex']}", f"Vertex{b['vertex']}"], "format": r["text"],
                               "role": f"diameter:{r.get('feature', 'hole')}", "side": side,
                               "span": (a[la], b[la]), "hidden": not (a["visible"] and b["visible"])})
            self.dia_views[id(r)] = vg.name

    def _point_vertex(self, vg, la, r):
        """Vertex for a position no edge marks (thread run-out on a plain cylinder).

        ``r["point3d"]`` lies on the feature axis; ``r["radial"]`` moves it out
        across the view to where the feature is drawn (e.g. the end of the thin
        minor-diameter thread line). Reuses a cosmetic vertex made by an earlier
        planning pass instead of stacking duplicates.
        """
        p3 = App.Vector(*r["point3d"])
        across = vg.up if la == "x" else vg.xdir
        p3 = p3 + across * float(r.get("radial", 0.0))
        lp = _project(vg, p3)
        idx = vg.find_vertex(lp)
        if idx is None:
            try:
                vg.view.makeCosmeticVertex3d(p3)
                vg.view.Document.recompute()
                vg.vertices = list(G.iter_vertices(vg.view))
                idx = vg.find_vertex(lp)
            except Exception:
                idx = None
        if idx is None:
            return []
        pt = {"x": lp[0], "y": lp[1], "vertex": idx, "visible": True, "kind": "cosmetic"}
        self.points[(vg.name, la)].append(pt)
        return [pt]

    def location(self, r, affinity_view=None):
        cands = []
        deferred = []
        for vg in self.views:
            la = _local_axis_for(vg, r["axis"])
            if la is None:
                continue
            pts = self.points[(vg.name, la)]
            tgt = _match(pts, la, vg, r["coord"])
            if r.get("center"):
                tgt = [p for p in tgt if p["kind"] == "center"]
                if not tgt:
                    tgt = self._center_vertex(vg, la, r["coord"])
            dat = _match(pts, la, vg, r["datum"])
            aff = 4 if affinity_view == vg.name else 0
            if dat and not tgt and r.get("point3d"):
                deferred.append((self._score(vg, True, aff), vg, la, dat))
            if not tgt or not dat:
                continue
            vis = any(p["visible"] for p in tgt)
            cands.append((self._score(vg, vis, aff), vg, la, tgt, dat))
        if not cands and deferred:
            # no edge marks the position: one cosmetic vertex on the best view only
            for score, vg, la, dat in sorted(deferred, key=lambda c: -c[0]):
                tgt = self._point_vertex(vg, la, r)
                if tgt:
                    cands.append((score, vg, la, tgt, dat))
                    break
        if not cands:
            self.unplaced.append(_describe(r))
            return
        _, vg, la, tgt, dat = max(cands, key=lambda c: c[0])
        side = _side_order(self.occupied.get(vg.name, set()), la)
        other = "y" if la == "x" else "x"
        far = max if side in ("top", "right") else min
        tv = [p for p in tgt if p["visible"]] or tgt
        dv = [p for p in dat if p["visible"]] or dat
        target = far(tv, key=lambda p: p[other])
        datum = far(dv, key=lambda p: p[other])
        self.specs.append({"view": vg, "type": "DistanceX" if la == "x" else "DistanceY",
                           "refs": [f"Vertex{datum['vertex']}", f"Vertex{target['vertex']}"], "format": "%.2w",
                           "role": f"baseline:{r['axis']}:{r['source']}", "side": side,
                           "span": (datum[la], target[la]), "hidden": not target["visible"]})

    def radius(self, r, prefix="R"):
        best = None
        for vg in self.views:
            for a in vg.arcs + (vg.circles if r["kind"] == "sphere" else []):
                if abs(a["radius"] - r["radius"]) > 2e-3:
                    continue
                score = (self._score(vg, a["visible"]), a["center"][0] + a["center"][1])
                if best is None or score > best[0]:
                    best = (score, vg, a)
        if best is None:
            self.unplaced.append(_describe(r))
            return
        _, vg, a = best
        self.specs.append({"view": vg, "type": "Radius", "refs": [f"Edge{a['edge']}"], "format": r["text"],
                           "role": "radius" if r["kind"] == "radius" else "sphere", "circle": a})

    def chamfer(self, r):
        best = None
        for vg in self.views:
            for i, p0, p1, visible in vg.lines:
                dx, dy = abs(p1[0] - p0[0]), abs(p1[1] - p0[1])
                if abs(dx - r["leg"]) < 2e-3 and abs(dy - r["leg"]) < 2e-3:
                    a, b = vg.find_vertex(p0), vg.find_vertex(p1)
                    if a is None or b is None:
                        continue
                    score = (self._score(vg, visible), p0[0] + p0[1])
                    if best is None or score > best[0]:
                        best = (score, vg, (a, p0), (b, p1))
        if best is None:
            self.unplaced.append(_describe(r))
            return
        _, vg, (a, p0), (b, p1) = best
        side = _side_order(self.occupied.get(vg.name, set()), "x")
        self.specs.append({"view": vg, "type": "DistanceX", "refs": [f"Vertex{a}", f"Vertex{b}"],
                           "format": r["text"].replace(f"C{_fmt(r['leg'])}", "C%.2w"), "role": "chamfer",
                           "side": side, "span": (p0[0], p1[0]), "hidden": False})

    def angle(self, r):
        """Angle of an inclined face against an axis-parallel edge of the same view.

        Prefer an edge sharing a corner with the slant; otherwise (bent sheet
        metal, ribs: a bend arc or fillet sits between them) use the nearest
        long axis-parallel edge - TechDraw measures between the extended lines.
        Parallel inclined faces (both skins of a sheet, opposite rib faces)
        state one angle only [04].
        """
        face = r["face"]
        n = App.Vector(face.Surface.Axis)
        n.normalize()
        for done in self.angle_normals:
            if abs(abs(done.dot(n)) - 1) < 1e-6:
                return  # same angle already dimensioned
        best = None
        for vg in self.views:
            if abs(n.dot(vg.direction)) > 1e-4:
                continue  # face not edge-on in this view
            for i, p0, p1, visible in vg.lines:
                dx, dy = p1[0] - p0[0], p1[1] - p0[1]
                L = math.hypot(dx, dy)
                if L < 1e-6 or min(abs(dx), abs(dy)) < 1e-4 * L:
                    continue  # axis-parallel line
                # the slant must be the edge-on image of this face
                ln = (-dy / L, dx / L)
                n2 = vg.xdir * ln[0] + vg.up * ln[1]
                if abs(abs(n2.dot(n)) - 1) > 1e-4:
                    continue
                for j, q0, q1, vis2 in vg.lines:
                    if j == i:
                        continue
                    ex, ey = q1[0] - q0[0], q1[1] - q0[1]
                    M = math.hypot(ex, ey)
                    if M < 1e-6 or min(abs(ex), abs(ey)) > 1e-6:
                        continue
                    vertex = _intersection(p0, p1, q0, q1)
                    if vertex is None:
                        continue
                    shared = any(G.dist(pp, qq) < 1e-3 for pp in (p0, p1) for qq in (q0, q1))
                    gap = min(G.dist(pp, qq) for pp in (p0, p1) for qq in (q0, q1))
                    score = (self._score(vg, visible and vis2) + (5 if shared else 0), -gap, M)
                    if best is None or score > best[0]:
                        best = (score, vg, i, j, vertex)
        if best is None:
            self.unplaced.append(_describe(r))
            return
        self.angle_normals.append(n)
        _, vg, i, j, vertex = best
        self.specs.append({"view": vg, "type": "Angle", "refs": [f"Edge{i}", f"Edge{j}"], "format": "%.1w°",
                           "role": "angle", "circle": {"center": vertex, "radius": 0.0}, "estimate": "000.0°"})

    def pcd(self, r):
        for vg in self.views:
            if vg.view_axis() == r["axis"]:
                self.specs.append({"view": vg, "type": "Diameter", "refs": None, "format": "⌀%.2w",
                                   "role": "pcd", "make_circle": (r["center"], r["dia"] / 2.0),
                                   "circle": {"center": _project(vg, r["center"]), "radius": r["dia"] / 2.0}})
                return
        self.unplaced.append(_describe(r))


def _intersection(p0, p1, q0, q1):
    """Intersection of the infinite lines p0-p1 and q0-q1, or None if parallel."""
    d1 = (p1[0] - p0[0], p1[1] - p0[1])
    d2 = (q1[0] - q0[0], q1[1] - q0[1])
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-12:
        return None
    t = ((q0[0] - p0[0]) * d2[1] - (q0[1] - p0[1]) * d2[0]) / den
    return (p0[0] + t * d1[0], p0[1] + t * d1[1])


def _coord3(p, axis):
    return p["XYZ".index(axis)]


def _profile_dia(r):
    """Diameters dimensioned on the profile (axis in the plane): turned steps and
    external threads [ISO 6410: the callout goes on the major-diameter outline]."""
    return r.get("feature") == "turned" or (r.get("feature") == "thread" and not r.get("internal"))


def _fmt(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _describe(r):
    k = r["kind"]
    if k == "loc":
        return f"position {r['axis']}={r['coord']} ({r['source']}) from datum {r['axis']}={r['datum']}"
    if k == "dia":
        return f"{r.get('feature', '')} diameter {r['dia']} (axis {r['axis']})"
    if k in ("radius", "sphere"):
        return f"{k} R{r['radius']}"
    if k == "chamfer":
        return f"chamfer C{r['leg']}"
    if k == "angle":
        return "angle of inclined face"
    if k == "pcd":
        return f"pitch circle ⌀{r['dia']:.2f}"
    return k


def realize(reqs, view_geoms, occupied):
    """Return ``(specs, unplaced)`` for :func:`dimensions.place`."""
    rz = Realizer(view_geoms, occupied)
    order = {"dia": 0, "pcd": 1, "radius": 2, "sphere": 2, "chamfer": 3, "angle": 4, "loc": 5}
    for requirement_id, r in sorted(enumerate(reqs), key=lambda item: order.get(item[1]["kind"], 9)):
        before = len(rz.specs)
        rz.requirement = r
        k = r["kind"]
        if k == "dia":
            rz.diameter(r)
        elif k == "loc":
            # hole centres go to the view that carries the hole callout
            aff = None
            if r.get("center"):
                for d in reqs:
                    if d["kind"] == "dia" and id(d) in rz.dia_views:
                        if any(abs(_coord3(c, r["axis"]) - r["coord"]) < 2e-3 for c in d["centers"]):
                            aff = rz.dia_views[id(d)]
                            break
            rz.location(r, aff)
        elif k in ("radius", "sphere"):
            rz.radius(r)
        elif k == "chamfer":
            rz.chamfer(r)
        elif k == "angle":
            rz.angle(r)
        elif k == "pcd":
            rz.pcd(r)
        # Identity is tied to this build's requirement list, not to dimension
        # value or a view-local EdgeN. Replanning uses the same list.
        for spec in rz.specs[before:]:
            spec["requirement_id"] = requirement_id
    return rz.specs, rz.unplaced


# ----------------------------------------------------------- view selection
def needs(r):
    """Model axes a requirement wants: ('in_plane', A) or ('along', A)."""
    k = r["kind"]
    if k == "loc":
        return [("in_plane", r["axis"])]
    if k == "dia":
        if _profile_dia(r):
            return [("in_plane", r["axis"])]
        return [("along", r["axis"])]
    if k in ("radius", "pcd"):
        return [("along", r["axis"])] if r.get("axis") else []
    if k in ("chamfer", "angle"):
        return []
    return []


def view_set_cost(axes_of_views, reqs):
    """(unmet, n_views): requirements a view set cannot show characteristically."""
    unmet = 0
    for r in reqs:
        for kind, a in needs(r):
            ok = any((a in plane) if kind == "in_plane" else (a == along) for along, plane in axes_of_views)
            if not ok:
                unmet += 1
    return unmet

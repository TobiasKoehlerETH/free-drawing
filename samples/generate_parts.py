"""Generate a corpus of STEP test parts covering the guideline families.

Run with FreeCAD's console interpreter (from the repo root)::

    freecadcmd -c "exec(open('samples/generate_parts.py', encoding='utf-8').read())"

or ``python samples/run_samples.py --generate``. Parts are written to
``samples/parts`` (override with the ``FD_SAMPLES_OUT`` environment variable).
Every part is parametric and deterministic so drawings can be compared run to
run.
"""

import math
import os

import FreeCAD as App
import Part

V = App.Vector
OUT = os.environ.get("FD_SAMPLES_OUT") or os.path.join(os.getcwd(), "samples", "parts")


def box(x, y, z, at=(0, 0, 0)):
    return Part.makeBox(x, y, z, V(*at))


def cyl(r, h, at=(0, 0, 0), axis=(0, 0, 1)):
    return Part.makeCylinder(r, h, V(*at), V(*axis))


def fillet_vertical_edges(shape, r):
    edges = [e for e in shape.Edges if abs(e.Vertexes[0].Point.z - e.Vertexes[-1].Point.z) > 1e-6
             and abs(e.Vertexes[0].Point.x - e.Vertexes[-1].Point.x) < 1e-6
             and abs(e.Vertexes[0].Point.y - e.Vertexes[-1].Point.y) < 1e-6]
    return shape.makeFillet(r, edges)


def chamfer_top_edges(shape, d):
    zmax = shape.BoundBox.ZMax
    edges = [e for e in shape.Edges if all(abs(v.Point.z - zmax) < 1e-6 for v in e.Vertexes)]
    return shape.makeChamfer(d, edges)


# ---------------------------------------------------------------- parts
def plate_hole_pattern():
    """Plate 180x120x12, 4x Ø12 corner holes and a central Ø30 bore [04 multiplicity]."""
    s = box(180, 120, 12)
    for x, y in ((20, 20), (160, 20), (20, 100), (160, 100)):
        s = s.cut(cyl(6, 12, (x, y, 0)))
    return s.cut(cyl(15, 12, (90, 60, 0)))


def rounded_plate():
    """Rounded rectangle 140x80x8 with R12 corners and 6x Ø8.5 holes."""
    s = fillet_vertical_edges(box(140, 80, 8), 12)
    for x in (20, 70, 120):
        for y in (15, 65):
            s = s.cut(cyl(4.25, 8, (x, y, 0)))
    return s


def turned_shaft():
    """Stepped shaft Ø40/Ø30/Ø20, chamfered ends [turned part, Ø on profile]."""
    a = cyl(20, 30, axis=(1, 0, 0))
    b = cyl(15, 50, (30, 0, 0), (1, 0, 0))
    c = cyl(10, 40, (80, 0, 0), (1, 0, 0))
    s = a.fuse(b).fuse(c).removeSplitter()
    ends = [e for e in s.Edges if type(e.Curve).__name__ == "Circle"
            and (abs(e.Curve.Center.x) < 1e-6 or abs(e.Curve.Center.x - 120) < 1e-6)]
    return s.makeChamfer(1.5, ends)


def flange():
    """Flange Ø120 t15, bore Ø40, 6x Ø9 on PCD 90 [bolt circle]."""
    s = cyl(60, 15).cut(cyl(20, 15))
    for i in range(6):
        a = math.radians(60 * i + 30)
        s = s.cut(cyl(4.5, 15, (45 * math.cos(a), 45 * math.sin(a), 0)))
    return s


def l_bracket():
    """L angle 80x60x40, t8, two holes per leg."""
    s = box(80, 40, 8).fuse(box(8, 40, 60)).removeSplitter()
    for x in (30, 62):
        s = s.cut(cyl(3.5, 8, (x, 20, 0)))
    for z in (25, 47):
        s = s.cut(cyl(3.5, 8, (0, 20, z), (1, 0, 0)))
    return s


def block_with_pocket():
    """Block 100x60x40 with a blind rectangular pocket 50x30x15."""
    return box(100, 60, 40).cut(box(50, 30, 15, (25, 15, 25)))


def chamfered_block():
    """Block 60x40x30 with 3 mm chamfers on the top edges [C3]."""
    return chamfer_top_edges(box(60, 40, 30), 3)


def slotted_plate():
    """Plate 120x60x6 with a 40x10 slot (rounded ends) and 2 holes."""
    slot = box(30, 10, 6, (45, 25, 0)).fuse(cyl(5, 6, (45, 30, 0))).fuse(cyl(5, 6, (75, 30, 0)))
    s = box(120, 60, 6).cut(slot.removeSplitter())
    for x in (15, 105):
        s = s.cut(cyl(3, 6, (x, 30, 0)))
    return s


def counterbored_block():
    """Block 80x50x25 with 2 counterbored holes Ø6.6 / Ø11 x 6.8 deep."""
    s = box(80, 50, 25)
    for x in (20, 60):
        s = s.cut(cyl(3.3, 25, (x, 25, 0))).cut(cyl(5.5, 6.8, (x, 25, 18.2)))
    return s


def tube():
    """Tube Ø50 / Ø40 x 120 [turned, bore]."""
    return cyl(25, 120, axis=(1, 0, 0)).cut(cyl(20, 120, axis=(1, 0, 0)))


def stepped_block():
    """Three-step staircase 90x40x45 [baseline chains]."""
    return box(90, 40, 15).fuse(box(60, 40, 15, (0, 0, 15))).fuse(box(30, 40, 15, (0, 0, 30))).removeSplitter()


def sheet_channel():
    """Bent sheet U-channel t2, inside R2, 100 long [15 sheet metal]."""
    t, ri, w, h, length = 2.0, 2.0, 40.0, 25.0, 100.0
    outer = box(w, length, h)
    inner = box(w - 2 * t, length, h - t, (t, 0, t))
    s = outer.cut(inner)
    bottom_in = [e for e in s.Edges if type(e.Curve).__name__ == "Line"
                 and abs(e.Vertexes[0].Point.y - e.Vertexes[1].Point.y) > 1
                 and all(abs(v.Point.z - t) < 1e-6 for v in e.Vertexes)]
    s = s.makeFillet(ri, bottom_in)
    bottom_out = [e for e in s.Edges if type(e.Curve).__name__ == "Line"
                  and abs(e.Vertexes[0].Point.y - e.Vertexes[1].Point.y) > 1
                  and all(abs(v.Point.z) < 1e-6 for v in e.Vertexes)
                  and all(abs(v.Point.x) < 1e-6 or abs(v.Point.x - w) < 1e-6 for v in e.Vertexes)]
    return s.makeFillet(ri + t, bottom_out)


def wedge_block():
    """Block 80x40x40 with a 30° inclined face [angular / auxiliary]."""
    s = box(80, 40, 40)
    cutter = Part.makePolygon([V(40, -1, 40), V(81, -1, 40), V(81, -1, 40 - 41 * math.tan(math.radians(30))), V(40, -1, 40)])
    prism = Part.Face(cutter).extrude(V(0, 42, 0))
    return s.cut(prism)


def boss_plate():
    """Plate 100x70x10 with Ø30x15 boss and Ø16 through bore."""
    s = box(100, 70, 10).fuse(cyl(15, 15, (50, 35, 10))).removeSplitter()
    return s.cut(cyl(8, 25, (50, 35, 0)))


def assembly_two_parts():
    """Two solids (base + block) [12 BOM / assembly]."""
    return Part.makeCompound([box(120, 60, 10), box(40, 40, 30, (40, 10, 10))])


def large_frame():
    """Frame 600x300x50 with window 500x200 [forces reduction scale]."""
    return box(600, 300, 50).cut(box(500, 200, 50, (50, 50, 0)))


def tiny_pin():
    """Small part 8x5x3 with Ø1.5 hole [readability at 1:1]."""
    return box(8, 5, 3).cut(cyl(0.75, 3, (4, 2.5, 0)))


def cross_hole_block():
    """Block 60x60x40, holes along X, Y and Z [hidden lines in all views]."""
    s = box(60, 60, 40)
    s = s.cut(cyl(5, 60, (0, 30, 20), (1, 0, 0)))
    s = s.cut(cyl(4, 60, (20, 0, 20), (0, 1, 0)))
    return s.cut(cyl(6, 40, (40, 40, 0)))


# ------------------------------------------------------------ sheet metal
class Sheet:
    """Bent sheet profile in the XZ plane, extruded along +Y.

    ``seg(L)`` adds a straight flange, ``bend(deg, up=True)`` a bend with inside
    radius ``r`` (positive = towards the sheet's top side). Bends are true
    annular sectors, so the drawing sees real bend radii and thickness.
    Flange frames are recorded so holes can be placed on any flange.
    """

    def __init__(self, t, r, width):
        self.t, self.r, self.w = t, r, width
        self.p = (0.0, 0.0)  # bottom-surface point
        self.d = (1.0, 0.0)  # running direction
        self.n = (0.0, 1.0)  # towards the top surface
        self.faces = []
        self.flanges = []

    def _v(self, xz, y=0.0):
        return V(xz[0], y, xz[1])

    def seg(self, L):
        p, d, n, t = self.p, self.d, self.n, self.t
        q = (p[0] + d[0] * L, p[1] + d[1] * L)
        pts = [p, q, (q[0] + n[0] * t, q[1] + n[1] * t), (p[0] + n[0] * t, p[1] + n[1] * t), p]
        self.faces.append(Part.Face(Part.makePolygon([self._v(x) for x in pts])))
        self.flanges.append({"p": p, "d": d, "n": n, "L": L})
        self.p = q
        return self

    def bend(self, deg, up=True):
        t, r = self.t, self.r
        a = math.radians(deg) * (1 if up else -1)
        p, d, n = self.p, self.d, self.n
        if up:  # inside of the bend is the top surface
            c = (p[0] + n[0] * (t + r), p[1] + n[1] * (t + r))
        else:
            c = (p[0] - n[0] * r, p[1] - n[1] * r)

        def rot(v, ang):
            ca, sa = math.cos(ang), math.sin(ang)
            return (v[0] * ca - v[1] * sa, v[0] * sa + v[1] * ca)

        def around(pt, ang):
            v = rot((pt[0] - c[0], pt[1] - c[1]), ang)
            return (c[0] + v[0], c[1] + v[1])

        b0, t0 = p, (p[0] + n[0] * t, p[1] + n[1] * t)
        b1, t1 = around(b0, a), around(t0, a)
        bm, tm = around(b0, a / 2), around(t0, a / 2)
        edges = [Part.Arc(self._v(b0), self._v(bm), self._v(b1)).toShape(),
                 Part.LineSegment(self._v(b1), self._v(t1)).toShape(),
                 Part.Arc(self._v(t1), self._v(tm), self._v(t0)).toShape(),
                 Part.LineSegment(self._v(t0), self._v(b0)).toShape()]
        self.faces.append(Part.Face(Part.Wire(edges)))
        self.p, self.d, self.n = b1, rot(d, a), rot(n, a)
        return self

    def solid(self):
        solids = [f.extrude(V(0, self.w, 0)) for f in self.faces]
        s = solids[0]
        for o in solids[1:]:
            s = s.fuse(o)
        return s.removeSplitter()

    def hole(self, shape, flange, s, y, dia):
        """Through hole on flange ``flange`` at distance ``s`` along it, ``y`` across."""
        f = self.flanges[flange]
        c = (f["p"][0] + f["d"][0] * s, f["p"][1] + f["d"][1] * s)
        base = V(c[0] - f["n"][0] * self.t, y, c[1] - f["n"][1] * self.t)
        return shape.cut(cyl(dia / 2, 3 * self.t, (base.x, base.y, base.z), (f["n"][0], 0, f["n"][1])))

    def slot(self, shape, flange, s0, s1, y, width):
        f = self.flanges[flange]
        out = shape
        for s in (s0, s1):
            out = self.hole(out, flange, s, y, width)
        p0 = (f["p"][0] + f["d"][0] * s0, f["p"][1] + f["d"][1] * s0)
        nx, nz = f["n"]
        dx, dz = f["d"]
        base = V(p0[0] - nx * self.t, y - width / 2, p0[1] - nz * self.t)
        L = s1 - s0
        prof = Part.makePolygon([base, base + V(dx * L, 0, dz * L), base + V(dx * L + nx * 3 * self.t, 0, dz * L + nz * 3 * self.t),
                                 base + V(nx * 3 * self.t, 0, nz * 3 * self.t), base])
        return out.cut(Part.Face(prof).extrude(V(0, width, 0)))


def sheet_z_bracket():
    """Z bracket t2 R2: two 90° bends in opposite directions, holes on both flanges."""
    sh = Sheet(2.0, 2.0, 50.0).seg(40).bend(90, True).seg(30).bend(90, False).seg(35)
    s = sh.solid()
    for y in (12, 38):
        s = sh.hole(s, 0, 15, y, 6.6)
        s = sh.hole(s, 2, 20, y, 6.6)
    return s


def sheet_hat_channel():
    """Hat profile t1.5 R1.5 (4 bends), slots in the crown, holes in the brims."""
    sh = Sheet(1.5, 1.5, 120.0).seg(20).bend(90, True).seg(30).bend(90, False).seg(40).bend(90, False).seg(30).bend(90, True).seg(20)
    s = sh.solid()
    s = sh.slot(s, 2, 12, 28, 60, 6)
    for y in (20, 60, 100):
        s = sh.hole(s, 0, 10, y, 5.5)
        s = sh.hole(s, 4, 10, y, 5.5)
    return s


def sheet_angle_135():
    """Obtuse 135° bend bracket t3 R3 with a hole pair on each leg [angle callouts]."""
    sh = Sheet(3.0, 3.0, 60.0).seg(70).bend(45, True).seg(55)
    s = sh.solid()
    for y in (15, 45):
        s = sh.hole(s, 0, 25, y, 9)
        s = sh.hole(s, 1, 30, y, 9)
    return s


def sheet_hemmed_cover():
    """Cover t1 R1: two side flanges, one closed hem (180°) on the front edge."""
    sh = Sheet(1.0, 1.0, 140.0).seg(10).bend(180, True).seg(12).bend(90, False).seg(80).bend(90, False).seg(25)
    s = sh.solid()
    for y in (20, 70, 120):
        s = sh.hole(s, 2, 15, y, 4.5)
    return s


def sheet_tray():
    """Tray 120x80, four 90° flanges 20 high, t1.5 R2, corner reliefs, holes and a cut-out."""
    t, r, h, L, W = 1.5, 2.0, 20.0, 120.0, 80.0
    k = r + t  # bend zone: the flat base stops here, corners stay open (relief)
    parts = [box(L - 2 * k, W - 2 * k, t, (-L / 2 + k, -W / 2 + k, 0))]
    for ang, a, b in ((0, L / 2, W / 2), (90, W / 2, L / 2), (180, L / 2, W / 2), (270, W / 2, L / 2)):
        sh = Sheet(t, r, 2 * (b - k)).seg(0.5).bend(90, True).seg(h - k)
        fl = sh.solid()
        fl.translate(V(a - k - 0.5, -(b - k), 0))  # outer wall face lands on x = a
        fl.rotate(V(0, 0, 0), V(0, 0, 1), ang)
        parts.append(fl)
    out = parts[0]
    for p_ in parts[1:]:
        out = out.fuse(p_)
    out = out.removeSplitter()
    out.translate(V(L / 2, W / 2, 0))
    for x, y in ((15, 15), (105, 15), (15, 65), (105, 65)):
        out = out.cut(cyl(2.25, t * 3, (x, y, -t)))
    return out.cut(box(40, 20, t * 3, (40, 30, -t)))


def sheet_flanged_u():
    """U channel with outward flanges (4 bends) t2 R2, rectangular cut-out and hole pattern."""
    sh = Sheet(2.0, 2.0, 160.0).seg(20).bend(90, False).seg(40).bend(90, True).seg(60).bend(90, True).seg(40).bend(90, False).seg(20)
    s = sh.solid()
    for y in (20, 80, 140):
        s = sh.hole(s, 0, 10, y, 6.6)
        s = sh.hole(s, 4, 10, y, 6.6)
    return sh.slot(s, 2, 15, 45, 80, 12)


# ---------------------------------------------------------------- threads
def _thread_ridge(pitch, length, r_in, r_out, left=False):
    """ISO 60° thread ridge (crest flat P/8) swept along a helix on +Z, trimmed to z in [0, length].

    The profile runs from ``r_in`` to ``r_out``; it is swept one pitch beyond
    both ends and trimmed so the thread ends flush with the zone.
    """
    t30 = math.tan(math.radians(30))
    w_out = pitch / 8.0
    w_in = w_out + 2 * t30 * (r_out - r_in)
    pts = [V(r_in, 0, -w_in / 2), V(r_out, 0, -w_out / 2), V(r_out, 0, w_out / 2), V(r_in, 0, w_in / 2)]
    prof = Part.makePolygon(pts + [pts[0]])
    helix = Part.makeHelix(pitch, length + 2 * pitch, r_in, 0, left)
    sweep = Part.Wire(helix).makePipeShell([prof], True, True)
    sweep.translate(V(0, 0, -pitch))
    return sweep.common(cyl(r_out + 1.0, length))


def _on_axis(shape, axis, at):
    """Move a +Z built feature onto ``axis`` ('X'|'Y'|'Z') starting at ``at``."""
    s = shape.copy()
    if axis == "X":
        s.rotate(V(0, 0, 0), V(0, 1, 0), 90)
    elif axis == "Y":
        s.rotate(V(0, 0, 0), V(1, 0, 0), -90)
    s.translate(V(*at))
    return s


def external_thread(major, pitch, length, axis="Z", at=(0, 0, 0), left=False):
    """Threaded cylinder: core at the ISO minor d3 plus the swept ridge (crest just under the major)."""
    r_min = (major - 1.22687 * pitch) / 2.0
    ridge = _thread_ridge(pitch, length, r_min - 0.05, major / 2.0 - 0.02, left)  # overlaps the core
    return _on_axis(cyl(r_min, length).fuse(ridge), axis, at)


def internal_thread_groove(major, pitch, depth, drill_r, axis="Z", at=(0, 0, 0), left=False, extra=1.0):
    """Thread space to cut for a tapped hole, z in [0, depth + extra] (``extra`` beyond the mouth)."""
    return _on_axis(_thread_ridge(pitch, depth + extra, drill_r - 0.05, major / 2.0 + 0.02, left), axis, at)


def threaded_shaft():
    """Ø30x20 collar + Ø20 shank 80 long, external M20x2.5 over the last 40 mm [ISO 6410]."""
    s = cyl(15, 20, axis=(1, 0, 0)).fuse(cyl(10, 40, (20, 0, 0), (1, 0, 0)))
    return s.fuse(external_thread(20, 2.5, 40, "X", (60, 0, 0))).removeSplitter()


def tapped_block():
    """Block 60x40x25: 2x M8x1.25 blind (thread 12, tap drill Ø6.8x15) and M10x1.5 through."""
    s = box(60, 40, 25)
    # groove first, then the tap drill: cutting the groove from an already drilled
    # hole makes OCC drop it (helix flank touching the coincident drill wall)
    for x in (15, 45):
        s = s.cut(internal_thread_groove(8, 1.25, 12, 3.4, "Z", (x, 12, 13)))
        s = s.cut(cyl(3.4, 15, (x, 12, 10)))
    s = s.cut(internal_thread_groove(10, 1.5, 25, 4.25, "Z", (30, 28, -0.5), extra=0.5))
    return s.cut(cyl(4.25, 25, (30, 28, 0)))


def lh_stud():
    """Stud 80 long, M12x1.75 LEFT-hand thread 25 long at both ends, plain Ø10 middle."""
    s = external_thread(12, 1.75, 25, "X", (0, 0, 0), left=True)
    s = s.fuse(cyl(5, 30, (25, 0, 0), (1, 0, 0)))
    return s.fuse(external_thread(12, 1.75, 25, "X", (55, 0, 0), left=True)).removeSplitter()


def tapdrill_plate():
    """Plate 100x60x10 with plain Ø6.8 and Ø10.2 holes (tap drill sizes, no thread modelled)."""
    s = box(100, 60, 10)
    for x in (20, 50):
        s = s.cut(cyl(3.4, 10, (x, 20, 0)))
    for x in (50, 80):
        s = s.cut(cyl(5.1, 10, (x, 42, 0)))
    return s


PARTS = {
    "plate_hole_pattern": plate_hole_pattern,
    "rounded_plate": rounded_plate,
    "turned_shaft": turned_shaft,
    "flange": flange,
    "l_bracket": l_bracket,
    "block_with_pocket": block_with_pocket,
    "chamfered_block": chamfered_block,
    "slotted_plate": slotted_plate,
    "counterbored_block": counterbored_block,
    "tube": tube,
    "stepped_block": stepped_block,
    "sheet_channel": sheet_channel,
    "wedge_block": wedge_block,
    "boss_plate": boss_plate,
    "assembly_two_parts": assembly_two_parts,
    "large_frame": large_frame,
    "tiny_pin": tiny_pin,
    "cross_hole_block": cross_hole_block,
    "sheet_z_bracket": sheet_z_bracket,
    "sheet_hat_channel": sheet_hat_channel,
    "sheet_angle_135": sheet_angle_135,
    "sheet_hemmed_cover": sheet_hemmed_cover,
    "sheet_tray": sheet_tray,
    "sheet_flanged_u": sheet_flanged_u,
    "threaded_shaft": threaded_shaft,
    "tapped_block": tapped_block,
    "lh_stud": lh_stud,
    "tapdrill_plate": tapdrill_plate,
}


def main():
    os.makedirs(OUT, exist_ok=True)
    report = []
    for name, fn in PARTS.items():
        try:
            shape = fn()
            if not shape.isValid():
                shape.fix(1e-7, 1e-7, 1e-7)
            if not shape.Solids or abs(shape.Volume) < 1e-6:
                raise ValueError("no solid produced")
            path = os.path.join(OUT, name + ".step")
            shape.exportStep(path)
            report.append(f"ok   {name}  solids={len(shape.Solids)} faces={len(shape.Faces)} vol={shape.Volume:.0f}")
        except Exception as e:  # keep generating the rest
            report.append(f"FAIL {name}: {type(e).__name__}: {e}")
    with open(os.path.join(OUT, "generate_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")


main()

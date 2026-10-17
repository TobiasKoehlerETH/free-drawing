"""Deterministic stress parts; run inside FreeCADCmd from the repository root.

These shapes test geometry handling, not inferred manufacturing intent.
"""
import os
import FreeCAD as App
import Part

V = App.Vector


def main():
    output = os.path.join(os.getcwd(), "samples", "complex")
    os.makedirs(output, exist_ok=True)
    parts = {
        "stepped_housing": stepped_housing(),
        "blind_manifold": blind_manifold(),
        "micro_grooves": micro_grooves(),
        "oblique_bore": oblique_bore(),
        "ribbed_bracket": ribbed_bracket(),
        "lofted_adapter": lofted_adapter(),
    }
    rotated = parts["blind_manifold"].copy()
    rotated.rotate(V(0, 0, 0), V(1, 2, 3), 37)
    rotated.translate(V(123, -48, 17))
    parts["rotated_manifold"] = rotated
    parts["mixed_assembly"] = Part.makeCompound([
        parts["stepped_housing"], Part.makeCylinder(8, 90, V(0, 0, -10))])
    for name, shape in parts.items():
        if not shape.isValid() or not shape.Solids:
            raise ValueError(f"invalid stress-test solid: {name}")
        shape.exportStep(os.path.join(output, name + ".step"))
        print(f"{name}: {len(shape.Solids)} solids, {len(shape.Faces)} faces")


def stepped_housing():
    body = Part.makeCylinder(30, 60).fuse(Part.makeCylinder(42, 10))
    body = body.cut(Part.makeCylinder(12, 60))
    body = body.cut(Part.makeCylinder(20, 18, V(0, 0, 42)))
    for x, y in ((33, 0), (-33, 0), (0, 33), (0, -33)):
        body = body.cut(Part.makeCylinder(3.3, 10, V(x, y, 0)))
    return body.removeSplitter()


def blind_manifold():
    body = Part.makeBox(80, 55, 40)
    for x in (18, 40, 62):
        body = body.cut(Part.makeCylinder(4, 29, V(x, 18, 40), V(0, 0, -1)))
        body = body.cut(Part.makeCylinder(7, 6, V(x, 18, 40), V(0, 0, -1)))
    return body.cut(Part.makeCylinder(4, 70, V(5, 18, 14), V(1, 0, 0)))


def micro_grooves():
    body = Part.makeCylinder(9, 65, V(0, 0, 0), V(1, 0, 0))
    for x in (8, 10, 52):
        ring = Part.makeCylinder(10, 0.8, V(x, 0, 0), V(1, 0, 0)).cut(
            Part.makeCylinder(8.2, 0.8, V(x, 0, 0), V(1, 0, 0)))
        body = body.cut(ring)
    return body


def oblique_bore():
    return Part.makeBox(65, 45, 35).cut(
        Part.makeCylinder(5, 100, V(-10, 22.5, -5), V(1, 0, 0.6)))


def ribbed_bracket():
    base = Part.makeBox(70, 50, 6).fuse(Part.makeBox(70, 6, 45))
    rib = Part.Face(Part.makePolygon([V(32, 6, 6), V(32, 40, 6),
                                     V(32, 6, 38), V(32, 6, 6)])).extrude(V(6, 0, 0))
    return base.fuse(rib).cut(Part.makeCylinder(5, 6, V(15, 0, 28), V(0, 1, 0))).removeSplitter()


def lofted_adapter():
    profiles = [Part.Wire([Part.makeCircle(r, V(x, 0, z))])
                for r, x, z in ((22, 0, 0), (16, 8, 25), (10, 15, 55))]
    return Part.makeLoft(profiles, True, False)


main()

"""Small machining cases that expose ambiguous automatic hole callouts.

Run inside FreeCADCmd; pass an output directory as the first script argument.
"""
from pathlib import Path
import sys

import FreeCAD as App
import Part


def production_parts():
    v = App.Vector
    blind = Part.makeBox(80, 50, 30)
    for x in (20, 60):
        blind = blind.cut(Part.makeCylinder(3, 9, v(x, 15, 30), v(0, 0, -1)))
        blind = blind.cut(Part.makeCylinder(3, 9, v(x, 35, 0)))

    side = Part.makeBox(80, 50, 30)
    for x in (20, 60):
        side = side.cut(Part.makeCylinder(3, 9, v(x, 0, 10), v(0, 1, 0)))
        side = side.cut(Part.makeCylinder(3, 9, v(x, 50, 20), v(0, -1, 0)))

    counterbores = Part.makeBox(80, 50, 20)
    for x, y, entry, sign in ((20, 15, 20, -1), (60, 35, 0, 1)):
        counterbores = counterbores.cut(Part.makeCylinder(3, 20, v(x, y, 0)))
        counterbores = counterbores.cut(Part.makeCylinder(6, 4, v(x, y, entry), v(0, 0, sign)))

    coaxial = Part.makeBox(40, 30, 30)
    coaxial = coaxial.cut(Part.makeCylinder(3, 8, v(20, 15, 0)))
    coaxial = coaxial.cut(Part.makeCylinder(3, 8, v(20, 15, 30), v(0, 0, -1)))

    boss = Part.makeCylinder(25, 12).fuse(Part.makeCylinder(8, 7, v(0, 0, 12)))
    boss = boss.cut(Part.makeCylinder(2, 12, v(17, 0, 0)))
    return {"opposed_blind_holes": blind, "opposed_side_holes": side, "opposed_counterbores": counterbores,
            "coaxial_blind_holes": coaxial, "concentric_boss": boss}


def main(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for name, shape in production_parts().items():
        shape.exportStep(str(output / (name + ".step")))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "production")

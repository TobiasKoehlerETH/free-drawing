"""Dedicated drawing-only worker. No source-bearing operations dispatcher."""
import json
import math
import os
from pathlib import Path
import sys
import traceback

import FreeCAD as App
import Part

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from free_drawing_mcp.drawing_definition import interpret
from fd_drawing.projection_check import check_views


def rebuild(packet, directory, tolerance=1e-5):
    outcome = interpret(packet)
    if outcome['status'] != 'planned': return outcome
    plan = outcome['plan']
    if plan['profile']=='revolved':
        points=[App.Vector(x,r,0) for x,r in plan['points']]
        points += [App.Vector(plan['points'][-1][0],0,0),App.Vector(plan['points'][0][0],0,0),points[0]]
        shape=Part.Face(Part.makePolygon(points)).revolve(App.Vector(0,0,0),App.Vector(1,0,0),360)
        return save_shape(shape,plan,outcome,directory,packet,tolerance)
    width, height, thickness = plan['width'], plan['height'], plan['thickness']
    if plan['profile'] == 'rectangle':
        shape = Part.makeBox(width, height, thickness, App.Vector(0, 0, -thickness))
    else:
        shape = Part.makeCylinder(width / 2, thickness, App.Vector(width / 2, height / 2, -thickness))
    for hole in plan['holes']:
        x, y = hole['center']
        depth = thickness if hole['depth'] == 'through' else hole['depth']
        if depth <= 0 or depth > thickness + 1e-6:
            return dict(status='conflict',scope=outcome['scope'],gaps=[{'code':'hole_depth','message':'Printed hole depth is outside the body'}])
        tool = Part.makeCylinder(hole['diameter'] / 2, depth, App.Vector(x, y, -depth))
        shape = shape.cut(tool)
        if hole['counterbore']:
            diameter, depth = hole['counterbore']
            if diameter <= hole['diameter'] or depth <= 0 or depth > thickness:
                return dict(status='conflict',scope=outcome['scope'],gaps=[{'code':'counterbore','message':'Conflicting counterbore sizes'}])
            shape = shape.cut(Part.makeCylinder(diameter / 2, depth, App.Vector(x, y, -depth)))
    for opening in plan['openings']:
        if opening['kind']=='rectangle':
            x0, y0, x1, y1 = opening['bounds']
            tool=Part.makeBox(x1-x0,y1-y0,thickness,App.Vector(x0,y0,-thickness))
        else:
            a,b=opening['centers']; radius=opening['radius']
            length=math.hypot(b[0]-a[0],b[1]-a[1])
            tool=Part.makeBox(length,2*radius,thickness,App.Vector(0,-radius,-thickness))
            tool.rotate(App.Vector(0,0,0),App.Vector(0,0,1),math.degrees(math.atan2(b[1]-a[1],b[0]-a[0])))
            tool.translate(App.Vector(a[0],a[1],0))
            for x,y in (a,b): tool=tool.fuse(Part.makeCylinder(radius,thickness,App.Vector(x,y,-thickness)))
        shape=shape.cut(tool.removeSplitter())
    return save_shape(shape,plan,outcome,directory,packet,tolerance)


def save_shape(shape,plan,outcome,directory,packet,tolerance):
    matrix = App.Matrix()
    x, y, z = plan['frame']
    matrix.A11, matrix.A21, matrix.A31 = x
    matrix.A12, matrix.A22, matrix.A32 = y
    matrix.A13, matrix.A23, matrix.A33 = z
    shape.transformShape(matrix, True)
    shape = shape.removeSplitter()
    if not shape.isValid() or len(shape.Solids) != 1 or shape.Volume <= 0:
        return dict(status='invalid',scope=outcome['scope'],gaps=[{'code':'invalid_solid','message':'Drawing constraints did not form one valid solid'}])
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    shape.exportStep(str(directory / 'rebuilt.step'))
    # STEP import restores tangent-edge continuity used by TechDraw HLR.
    # Reproject the same independent STEP that the comparator will receive.
    shape = Part.read(str(directory / 'rebuilt.step'))
    if not shape.isValid() or len(shape.Solids) != 1 or shape.Volume <= 0:
        return dict(status='invalid',scope=outcome['scope'],gaps=[{'code':'invalid_export','message':'Independent STEP export did not preserve a valid solid'}])
    doc = App.newDocument('IndependentReconstruction')
    obj = doc.addObject('Part::Feature','Rebuilt')
    obj.Shape = shape
    doc.recompute()
    projection=check_views(doc,obj,packet,directory,tolerance)
    doc.saveAs(str(directory / 'rebuilt.FCStd'))
    outcome.update(status='rebuilt', step=str(directory / 'rebuilt.step'), file=str(directory / 'rebuilt.FCStd'),
                   volume=shape.Volume, solid_count=len(shape.Solids),projection_check=projection)
    if not projection['match']:
        outcome.update(status='drawing_conflict',gaps=[{'code':'projection_mismatch','message':'Rebuilt geometry does not explain every defining view'}])
    return outcome


if __name__ == '__main__':
    with open(os.environ['FD_REBUILD_JOB'], encoding='utf-8') as stream: job = json.load(stream)
    try:
        if set(job) != {'evidence','output','result','tolerance'}: raise ValueError('Only drawing evidence, numeric tolerance and output paths are accepted')
        with open(job['evidence'], encoding='utf-8') as stream: packet = json.load(stream)
        result = rebuild(packet, job['output'],job['tolerance'])
    except Exception as error:
        result = {'status':'error','error':f'{type(error).__name__}: {error}','traceback':traceback.format_exc()}
    with open(job['result'],'w',encoding='utf-8') as stream: json.dump(result,stream,ensure_ascii=False)

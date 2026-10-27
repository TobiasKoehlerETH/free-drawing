"""Privileged comparison; no reconstructed parameters are fitted from the source."""
import json
import math
from pathlib import Path

import FreeCAD as App
import Part

from . import importer


def compare(source, rebuilt, frame_path, tolerance=1e-5):
    if not 0 < tolerance <= 0.01:
        raise ValueError('Comparison tolerance must be positive and at most 0.01 mm')
    target, _ = importer.read_shape(source)
    target, rotation = importer.align(target)
    with open(frame_path, encoding='utf-8') as stream: basis = json.load(stream)['basis']
    matrix = App.Matrix()
    matrix.A11, matrix.A12, matrix.A13 = basis[0]
    matrix.A21, matrix.A22, matrix.A23 = basis[1]
    matrix.A31, matrix.A32, matrix.A33 = basis[2]
    target.transformShape(matrix, True)
    candidate = Part.read(rebuilt)
    if not target.isValid() or not candidate.isValid() or not target.Solids or not candidate.Solids:
        return {'status':'inconclusive','reason':'Invalid/non-solid comparison geometry'}
    # World origin is not a manufacturing dimension. No scale or shape fitting.
    target_center,candidate_center=target.BoundBox.Center,candidate.BoundBox.Center
    registered=candidate.copy()
    registered.translate(target_center-candidate_center)
    registered.transformShape(matrix.inverse(),True)
    if rotation is not None:
        registered.rotate(App.Vector(0,0,0),rotation.Axis,-math.degrees(rotation.Angle))
    registered_path=str(Path(rebuilt).with_name('rebuilt-source-frame.step'))
    registered.exportStep(registered_path)
    target.translate(-target_center)
    candidate.translate(-candidate_center)
    missing, excess = target.cut(candidate), candidate.cut(target)
    if (not missing.isNull() and not missing.isValid()) or (not excess.isNull() and not excess.isValid()):
        return {'status':'inconclusive','reason':'Boolean comparison failed'}
    extents = max(abs(getattr(target.BoundBox,a+'Length')-getattr(candidate.BoundBox,a+'Length')) for a in 'XYZ')
    difference = missing.Volume + excess.Volume
    volume_difference = abs(target.Volume-candidate.Volume)
    limit = max(1e-7, target.Volume * 1e-9, min(target.Area,candidate.Area)*tolerance)
    boundary_error = 0.0
    for a, b in ((target,candidate),(candidate,target)):
        shell = Part.makeCompound(b.Shells)
        for edge in a.Edges:
            for i in range(5):
                point=edge.valueAt(edge.FirstParameter+(edge.LastParameter-edge.FirstParameter)*i/4)
                boundary_error=max(boundary_error,Part.Vertex(point).distToShape(shell)[0])
        # Closed interior surfaces may have few or no edges. Sample their
        # trimmed faces as well, so a small hidden cavity cannot pass on volume.
        for face in a.Faces:
            u0,u1,v0,v1=face.ParameterRange
            for i in range(1,4):
                for j in range(1,4):
                    point=face.valueAt(u0+(u1-u0)*i/4,v0+(v1-v0)*j/4)
                    vertex=Part.Vertex(point)
                    if face.distToShape(vertex)[0]<1e-7:
                        boundary_error=max(boundary_error,vertex.distToShape(shell)[0])
    equal = (len(target.Solids) == len(candidate.Solids) and extents <= tolerance and difference <= limit
             and volume_difference <= limit and boundary_error <= tolerance)
    return {'status':'match_within_scope' if equal else 'mismatch', 'linear_tolerance_mm':tolerance,
            'symmetric_difference_mm3':difference, 'difference_limit_mm3':limit,
            'missing_material_mm3':missing.Volume, 'excess_material_mm3':excess.Volume,
            'absolute_volume_difference_mm3':volume_difference,
            'maximum_extent_difference_mm':extents,'source_volume_mm3':target.Volume,'rebuilt_volume_mm3':candidate.Volume,
            'sampled_boundary_deviation_mm':boundary_error,
            'source_solids':len(target.Solids),'rebuilt_solids':len(candidate.Solids),
            'registered_reconstruction':registered_path,
            'registration':'drawing orientation plus translation only; no dimensional fitting'}

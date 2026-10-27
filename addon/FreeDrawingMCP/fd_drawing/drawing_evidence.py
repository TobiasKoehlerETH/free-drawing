"""Export only drawing-visible information; keep source placement separate."""
import hashlib
import json
import math

import FreeCAD as App

from . import geometry as G
from .snapshot import snapshot
from .paper_geometry import capture

STANDARD_FRAMES = {
    'Front': [[1,0,0],[0,1,0],[0,0,1]],
    'Top': [[1,0,0],[0,0,-1],[0,1,0]],
    'Bottom': [[1,0,0],[0,0,1],[0,-1,0]],
    'Right': [[0,0,-1],[0,1,0],[1,0,0]],
    'Left': [[0,0,1],[0,1,0],[-1,0,0]],
    'Rear': [[-1,0,0],[0,1,0],[0,0,-1]],
}


def export_evidence(doc, page, path, frame_path):
    snap = snapshot(doc, page, include_geometry=True)
    width,height=snap['sheet']['width'],snap['sheet']['height']
    def on_page(box):
        return box is not None and box[0]>=0 and box[1]>=0 and box[2]<=width and box[3]<=height
    if any(not on_page(d.get('text_box')) for d in snap['dimensions']):
        raise ValueError('A dimension is clipped outside the page; fix layout before reconstruction')
    if any(a.get('bbox') and not on_page(a['bbox']) for a in snap['annotations']):
        raise ValueError('An annotation is clipped outside the page; it cannot supply reconstruction facts')
    group = next((v for v in page.Views if v.TypeId == "TechDraw::DrawProjGroup"), None)
    if group is None:
        raise ValueError("A labelled standard projection group is required")
    anchor = group.Anchor
    basis = [App.Vector(anchor.XDirection), G.up_vector(anchor.Direction, anchor.XDirection), App.Vector(anchor.Direction)]
    packet = {"schema": 1, "units": "mm", "projection": snap["projection"], "views": [], "dimensions": [], "notes": []}
    for record in snap["views"]:
        if record["kind"] not in ("ortho", "section", "detail"):
            continue
        view = doc.getObject(record["name"])
        direction, x = App.Vector(view.Direction), App.Vector(view.XDirection)
        frame = [[round(v.dot(a), 8) for a in basis] for v in (x, G.up_vector(direction, x), direction)]
        if record['kind']=='ortho':
            declared=STANDARD_FRAMES.get(record.get('projection_type'))
            if declared is None or any(abs(a-b)>1e-6 for row,other in zip(frame,declared) for a,b in zip(row,other)):
                raise ValueError('Nonstandard view orientation needs an explicit auxiliary-view definition')
            frame=declared
        geometry=capture(view)
        if not on_page(geometry['bounds']): raise ValueError('A defining view is clipped outside the page')
        packet["views"].append({"id": view.Name, "kind": record["kind"], "role": record.get("projection_type") or record.get("caption", ""),
                                "frame": frame, "scale": record["scale"], 'hard_hidden':bool(view.HardHidden), **geometry})
    for dimension in snap["dimensions"]:
        witnesses = []
        if dimension["type"] in ("DistanceX", "DistanceY"):
            native=doc.getObject(dimension['name']);view=native.References2D[0][0]
            witnesses=[list(G.dim_internal_to_page(view,(point.x,point.y))) for point in native.getLinearPoints()]
        packet["dimensions"].append({"id": dimension["name"], "view": dimension["view"], "kind": dimension["type"],
                                     "text": dimension["text"], "refs": dimension["refs"], "witnesses": witnesses})
    for note in snap["annotations"]:
        packet["notes"].extend({'id':note['name']+':'+str(i),'scope':'sheet' if note['kind']=='note' else 'unresolved', 'text':text}
                               for i,text in enumerate(note.get('text',[])))
    if not any("UNITS:" in note['text'].upper() and "MM" in note['text'].upper() for note in packet["notes"]):
        raise ValueError("The drawing must explicitly state millimetre units")
    def canonical(value):
        if isinstance(value,float):
            rounded=round(value,9)
            return 0.0 if rounded==0 else rounded
        if isinstance(value,list): return [canonical(item) for item in value]
        if isinstance(value,dict): return {key:canonical(item) for key,item in value.items()}
        return value
    encoded = json.dumps(canonical(packet), ensure_ascii=False, sort_keys=True)
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(encoded)
    with open(frame_path, "w", encoding="utf-8") as stream:
        json.dump({"basis": [list(v) for v in basis]}, stream)
    return {"path": path, "sha256": hashlib.sha256(encoded.encode()).hexdigest(), "views": len(packet["views"]), "dimensions": len(packet["dimensions"])}

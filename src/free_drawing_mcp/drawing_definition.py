"""Infer constrained extrusion plans from printed drawing evidence, never STEP data.

Paper coordinates establish incidence and witness association only. All lengths
used in a construction plan come from displayed dimensions or explicit notes.
"""
from __future__ import annotations

from collections import deque
import math
import re

NUMBER = r"[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)"
PAPER_TOL = 0.06
COORDINATE_EPS = 1e-8
SCOPE = "extruded_and_revolved_profiles_v1"


def interpret(packet):
    validate(packet)
    notes=[n if isinstance(n,str) else n['text'] for n in packet['notes']]
    if any('ITEM' in n and 'QTY' in n for n in notes):
        return result('unsupported',[{'code':'assembly','message':'Assembly geometry needs separate component definitions'}])
    if any(isinstance(n,dict) and n['scope']!='sheet' for n in packet['notes']):
        return result('unsupported',[{'code':'annotation_scope','message':'A local/leader annotation has no supported scope interpretation'}])
    packet=dict(packet,notes=notes)
    language=check_language(packet)
    if language: return language
    if any(v['kind']!='ortho' for v in packet['views']):
        return result('unsupported',[{'code':'supplementary_definition','message':'Section/detail reconstruction needs a cutting-plane correspondence grammar'}])
    if any(e['kind']=='unsupported' for v in packet['views'] for e in v['edges']):
        return result('unsupported',[{'code':'unsupported_curve','message':'An unhandled drawn curve must not be omitted from reconstruction'}])
    for dim in packet['dimensions']:
        if not dim['witnesses']: continue
        view=next((v for v in packet['views'] if v['id']==dim['view']),None)
        if view is None or not witnesses_identified(dim,view):
            return result('unsupported',[{'code':'unidentified_witness','dimension':dim['id'], 'message':'A dimension witness is not attached to a drawn vertex or centre'}])
    if any("ITEM" in n and "QTY" in n for n in packet["notes"]):
        return result("unsupported", [{"code": "assembly", "message": "Assembly geometry needs separate component definitions"}])
    candidates = []
    for view in packet["views"]:
        if view["kind"] != "ortho":
            continue
        candidate = interpret_view(packet, view)
        candidates.append(candidate)
    built = [c for c in candidates if c["status"] == "planned"]
    conflicts = [c for c in candidates if c['status']=='conflict']
    if conflicts: return conflicts[0]
    if built:
        return max(built, key=lambda c: c["consumed_dimensions"])
    revolved=interpret_revolved(packet)
    if revolved is not None: return revolved
    missing = [c for c in candidates if c["status"] == "missing"]
    return min(missing, key=lambda c: len(c["gaps"])) if missing else result("unsupported", [
        {"code": "unsupported_profile", "message": "No supported fully constrained profile; bends, blends and multi-axis features need another grammar"}])


def result(status, gaps, **fields):
    return dict(status=status, scope=SCOPE, gaps=gaps, **fields)


def validate(packet):
    allowed(packet, {"schema", "units", "projection", "views", "dimensions", "notes"})
    if packet["schema"] != 1 or packet["units"] != "mm" or packet["projection"] not in ("first", "third"):
        raise ValueError("Unsupported evidence schema, units or projection")
    for view in packet["views"]:
        allowed(view, {"id", "kind", "role", "frame", "scale", "bounds", "circles", "edges",'hard_hidden'},optional={'hard_hidden'})
        frame=view['frame']
        if view['scale']<.01:raise ValueError('View scale is below the supported coordinate precision for reconstruction')
        if len(frame)!=3 or any(len(v)!=3 for v in frame) or any(abs(dot(frame[i],frame[j])-int(i==j))>1e-6 for i in range(3) for j in range(3)):
            raise ValueError('Projection frame must be orthonormal; scale fitting is forbidden')
        cross=[frame[0][1]*frame[1][2]-frame[0][2]*frame[1][1],frame[0][2]*frame[1][0]-frame[0][0]*frame[1][2],frame[0][0]*frame[1][1]-frame[0][1]*frame[1][0]]
        if dot(cross,frame[2])<.999999: raise ValueError('Mirrored projection frames are not supported')
        for circle in view["circles"]:
            allowed(circle, {"id", "ids", "center", "radius", "full", "construction",'visibility'},optional={'visibility'})
        for edge in view["edges"]:
            allowed(edge, {"id", "kind", "ends", "center", "radius",'visibility','interval'}, optional={"center", "radius",'visibility','interval'})
    for dim in packet["dimensions"]:
        allowed(dim, {"id", "view", "kind", "text", "refs", "witnesses"})
    for note in packet['notes']:
        if not isinstance(note,str): allowed(note,{'id','scope','text'})
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Non-finite drawing coordinate")
        if isinstance(value, dict):
            for child in value.values(): finite(child)
        elif isinstance(value, list):
            for child in value: finite(child)
    finite(packet)


def allowed(value, keys, optional=frozenset()):
    if not isinstance(value, dict) or set(value) - keys or keys - optional - set(value):
        raise ValueError("Evidence contains missing or forbidden fields; source/model data is not accepted")


def numeric(text):
    match = re.fullmatch(rf"\s*({NUMBER})\s*", text)
    return float(match[1].replace(",", ".")) if match else None


def near(a, b):
    return abs(a - b) <= PAPER_TOL


def same_point(a, b):
    return all(abs(x-y)<COORDINATE_EPS for x, y in zip(a, b))


def witnesses_identified(dim,view):
    anchors=[p for e in view['edges'] for p in e['ends']]
    anchors += [c['center'] for c in view['circles']]
    anchors += [e['center'] for e in view['edges'] if e.get('center')]
    return all(any(same_point(point,anchor) for anchor in anchors) for point in dim['witnesses'])


def check_language(packet):
    count=r'(?:\d+\s*[×x]\s*)?'
    diameter=rf'{count}[⌀Ø]\s*{NUMBER}(?:\s+(?:THRU|[↧↓]\s*{NUMBER}))?(?:\s*⌴\s*[⌀Ø]\s*{NUMBER}\s*[↧↓]\s*{NUMBER})?(?:\s+EQ SP)?'
    scalar=rf'(?:{NUMBER}|{count}[RC]\s*{NUMBER}|{NUMBER}°)'
    for dim in packet['dimensions']:
        if not re.fullmatch(rf'\s*(?:{diameter}|{scalar})\s*',dim['text']):
            return result('unsupported',[{'code':'unhandled_dimension_text','dimension':dim['id'],'message':'The complete dimension text is not understood: '+dim['text']}])
    thickness=[];phases=[]
    for text in packet['notes']:
        note=text.strip()
        if re.fullmatch(r'UNITS:\s*mm|TOLERANCING PRINCIPLE:\s*ISO 8015|DO NOT SCALE(?: DRAWING)?',note,re.I): continue
        if note in ('RECTANGULAR OPENINGS: THRU','SLOTS: THRU'): continue
        t=re.fullmatch(rf't\s*=?\s*({NUMBER})',note,re.I)
        phase=re.fullmatch(rf'BOLT CIRCLE START:\s*({NUMBER})° FROM VIEW HORIZONTAL',note)
        if t: thickness.append(float(t[1].replace(',','.')))
        elif phase: phases.append(float(phase[1].replace(',','.')))
        else: return result('unsupported',[{'code':'unhandled_note','message':'Potentially controlling note is not understood: '+text}])
    if len(set(thickness))>1 or len(set(phases))>1:
        return result('conflict',[{'code':'conflicting_notes','message':'Conflicting thickness or pattern-angle notes'}])
    return None


class Coordinates:
    def __init__(self, datum):
        self.positions = [datum]
        self.links = {}
        self.solved = {}

    def node(self, coordinate):
        for i, p in enumerate(self.positions):
            if abs(p-coordinate)<COORDINATE_EPS: return i
        self.positions.append(coordinate)
        return len(self.positions) - 1

    def connect(self, a, b, length, evidence):
        i, j = self.node(a), self.node(b)
        signed = length if b >= a else -length
        self.links.setdefault(i, []).append((j, signed, evidence))
        self.links.setdefault(j, []).append((i, -signed, evidence))

    def solve(self):
        self.solved = {0: (0.0, [])}
        queue = deque([0])
        while queue:
            i = queue.popleft()
            value, evidence = self.solved[i]
            for j, delta, source in self.links.get(i, []):
                proposed = value + delta
                if j in self.solved:
                    if abs(self.solved[j][0] - proposed) > 1e-5:
                        raise ValueError("Conflicting printed dimension equations")
                else:
                    self.solved[j] = (proposed, sorted(set(evidence + [source])))
                    queue.append(j)
        self.active_sources={source for i in self.solved for _,_,source in self.links.get(i,[])}
        unchecked=set(self.links)-set(self.solved)
        while unchecked:
            start=next(iter(unchecked));local={start:0.0};queue=deque([start])
            while queue:
                i=queue.popleft();unchecked.discard(i)
                for j,delta,_ in self.links.get(i,[]):
                    value=local[i]+delta
                    if j in local and abs(local[j]-value)>1e-5: raise ValueError('Conflicting disconnected dimension equations')
                    if j not in local: local[j]=value;queue.append(j)

    def value(self, coordinate):
        return self.solved.get(self.node(coordinate))


def boundary_rectangle(edges, bounds):
    if not edges or any(e["kind"] != "line" for e in edges): return False
    for axis, value, lo, hi in ((0, bounds[0], bounds[1], bounds[3]), (0, bounds[2], bounds[1], bounds[3]),
                                 (1, bounds[1], bounds[0], bounds[2]), (1, bounds[3], bounds[0], bounds[2])):
        intervals = []
        for edge in edges:
            a, b = edge["ends"]
            if near(a[axis], value) and near(b[axis], value):
                intervals.append(sorted((a[1-axis], b[1-axis])))
        reached = lo
        for a, b in sorted(intervals):
            if a > reached + PAPER_TOL: return False
            reached = max(reached, b)
        if reached < hi - PAPER_TOL: return False
    return True


def components(edges):
    remaining = list(edges)
    groups = []
    while remaining:
        group = [remaining.pop(0)]
        changed = True
        while changed:
            changed = False
            for edge in list(remaining):
                if any(same_point(a, b) for a in edge["ends"] for other in group for b in other["ends"]):
                    remaining.remove(edge); group.append(edge); changed = True
        groups.append(group)
    return groups


def bounds_of(edges):
    points = [p for edge in edges for p in edge["ends"]]
    return [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]


def circle_callout(circle, dims):
    for dim in dims:
        if set(dim["refs"]) & set(circle["ids"]):
            diameter = re.search(rf"[⌀Ø]\s*({NUMBER})", dim["text"])
            if diameter:
                count = re.match(r"\s*(\d+)\s*[×x]", dim["text"])
                primary_text=re.split('[⌴⌵]',dim['text'])[0]
                depth = re.search(rf"[↧↓]\s*({NUMBER})", primary_text)
                cbore = re.search(rf"⌴\s*[⌀Ø]\s*({NUMBER})\s*[↧↓]\s*({NUMBER})", dim["text"])
                return {"diameter": float(diameter[1].replace(",", ".")), "count": int(count[1]) if count else 1,
                        "depth": "through" if "THRU" in primary_text else float(depth[1].replace(",", ".")) if depth else None,
                        "counterbore": [float(v.replace(",", ".")) for v in cbore.groups()] if cbore else None,
                        "id": dim["id"], "text": dim["text"]}
    return None


def interpret_view(packet, view):
    dims = [d for d in packet["dimensions"] if d["view"] == view["id"]]
    if any('⌵' in d['text'] or re.search(r'\bM\d',d['text']) for d in dims):
        return result('unsupported',[{'code':'unsupported_callout','message':'Countersinks and thread geometry require another reconstruction grammar'}])
    bounds = view["bounds"]
    if not bounds: return result("unsupported", [])
    edges = [e for e in view["edges"] if e.get('visibility','solid')=='solid']
    circles = [c for c in view["circles"] if not c["construction"]]
    outer_edges = [e for e in edges if e["kind"] == "line" and any(all(near(p[a], bounds[a + side]) for p in e["ends"])
                        for a in (0, 1) for side in (0, 2))]
    outer_circle = next((c for c in circles if near(c["center"][0] - c["radius"], bounds[0])
                         and near(c["center"][0] + c["radius"], bounds[2]) and near(c["center"][1] - c["radius"], bounds[1])
                         and near(c["center"][1] + c["radius"], bounds[3])), None)
    rectangular = boundary_rectangle(outer_edges, bounds)
    if not rectangular and outer_circle is None: return result("unsupported", [])
    if any(e["kind"] == "unsupported" for e in edges): return result("unsupported", [])
    # Other end-on circular features imply cuts/bosses on another axis.
    for other in packet["views"]:
        if other["kind"] == "ortho" and abs(dot(view["frame"][2], other["frame"][2])) < 0.99 and other["circles"]:
            return result("unsupported", [{"code": "multi_axis_features", "message": "Circular features on multiple axes are not supported by this grammar"}])
    coords = [Coordinates(bounds[0]), Coordinates(bounds[1])]
    used = set()
    for dim in dims:
        value = numeric(dim["text"])
        if value is not None and dim["kind"] in ("DistanceX", "DistanceY") and len(dim["witnesses"]) == 2:
            axis = int(dim["kind"] == "DistanceY")
            coords[axis].connect(dim["witnesses"][0][axis], dim["witnesses"][1][axis], value, dim["id"])
            used.add(dim["id"])
    # The overall span of a shared axis can be stated on another standard view.
    for other in packet['views']:
        if other['kind'] != 'ortho' or other['id'] == view['id']: continue
        for dim in packet['dimensions']:
            value=numeric(dim['text'])
            if dim['view'] != other['id'] or value is None or dim['kind'] not in ('DistanceX','DistanceY') or len(dim['witnesses'])!=2: continue
            oa=int(dim['kind']=='DistanceY')
            ends=sorted(p[oa] for p in dim['witnesses'])
            if not near(ends[0],other['bounds'][oa]) or not near(ends[1],other['bounds'][oa+2]): continue
            for axis in (0,1):
                if abs(dot(view['frame'][axis],other['frame'][oa]))>.99:
                    coords[axis].connect(bounds[axis],bounds[axis+2],value,dim['id']);used.add(dim['id'])
    radii={}
    arcs=[edge for edge in edges if edge['kind']=='arc']
    for dim in dims:
        match=re.search(rf'\bR\s*({NUMBER})',dim['text'])
        reference=next((arc for arc in arcs if arc['id'] in dim['refs']),None)
        if not match or reference is None: continue
        radius=float(match[1].replace(',','.'))
        count=re.match(r'\s*(\d+)\s*[×x]',dim['text'])
        required=int(count[1]) if count else 1
        matching=[arc for arc in arcs if abs(arc['radius']-reference['radius'])<COORDINATE_EPS]
        centers={tuple(round(v,9) for v in arc['center']) for arc in matching}
        if len(centers)!=required:
            component=next((g for g in components(edges) if reference in g),[])
            matching=[arc for arc in matching if arc in component]
            centers={tuple(round(v,9) for v in arc['center']) for arc in matching}
        if len(centers)!=required:
            return result('unsupported',[{'code':'radius_count','message':'Radius multiplicity does not uniquely cover the drawn arc instances'}])
        for arc in matching:
            radii[arc['id']]=(radius,dim['id'])
            for axis in (0,1):
                for point in arc['ends']:
                    if near(abs(point[axis]-arc['center'][axis]),arc['radius']):
                        coords[axis].connect(arc['center'][axis],point[axis],radius,dim['id'])
        used.add(dim['id'])
    gaps, provenance = [], {}
    exterior = None
    if outer_circle is not None and not rectangular:
        exterior = circle_callout(outer_circle, dims)
        if not exterior:
            exterior = outer_profile_diameter(packet, view)
        if not exterior:
            gaps.append({"code": "missing_diameter", "view": view["id"], "refs": outer_circle["ids"][:1], "message": "Outer circle diameter is not stated on this view"})
        else:
            for axis in (0, 1):
                coords[axis].connect(bounds[axis], bounds[axis+2], exterior["diameter"], exterior["id"])
                coords[axis].connect(bounds[axis], outer_circle["center"][axis], exterior["diameter"] / 2, exterior["id"])
            used.add(exterior["id"])
    try:
        for coordinate in coords: coordinate.solve()
    except ValueError as error:
        return result("conflict", [{"code": "conflicting_dimensions", "message": str(error)}])
    parsed_linear={d['id'] for d in packet['dimensions'] if d['id'] in used and numeric(d['text']) is not None}
    active=coords[0].active_sources|coords[1].active_sources
    used -= parsed_linear-active
    def locate(axis, paper, subject):
        solved = coords[axis].value(paper)
        if solved is None:
            a = bounds[:2]
            b = list(a); b[axis] = paper
            gaps.append({"code": "missing_location", "view": view["id"], "axis": axis, "a": a, "b": b,
                         "message": f"Missing printed {['horizontal', 'vertical'][axis]} dimension for {subject}"})
            return None
        provenance[subject + ':' + str(axis)] = solved[1]
        return solved[0]
    width, height = locate(0, bounds[2], 'outer profile'), locate(1, bounds[3], 'outer profile')
    thickness, thickness_ids = find_thickness(packet, view)
    if thickness is None:
        gaps.append({"code": "missing_thickness", "view": view["id"], "message": "Depth/thickness is not explicitly dimensioned or stated"})
    else:
        provenance['thickness'] = thickness_ids
        used.update(thickness_ids)
    foreign_controls=[d['id'] for d in packet['dimensions'] if d['view']!=view['id'] and d['id'] not in used and numeric(d['text']) is None]
    if foreign_controls:
        return result('unsupported',[{'code':'cross_view_feature_controls','message':'Feature controls in another view need a supported correspondence before repair',
                                      'dimensions':foreign_controls}])
    inner_edges = [e for e in edges if e not in outer_edges]
    openings = []
    for group in components(inner_edges):
        box = bounds_of(group)
        if boundary_rectangle(group,box):
            limits = [locate(0, box[0], 'opening left'), locate(1, box[1], 'opening bottom'),
                      locate(0, box[2], 'opening right'), locate(1, box[3], 'opening top')]
            opening={'kind':'rectangle','bounds':limits,'depth':'through'}
            note='RECTANGULAR OPENINGS: THRU'
        else:
            curved=[e for e in group if e['kind']=='arc']
            centers=[]
            for arc in curved:
                if not any(same_point(arc['center'],p) for p in centers): centers.append(arc['center'])
            if len(centers)!=2 or sum(e['kind']=='line' for e in group)!=2 or not curved:
                return result('unsupported',[{'code':'profile_curves','message':'Internal profile is not a supported rectangle or capsule slot'}])
            definitions=[radii.get(e['id']) for e in curved]
            if any(d is None for d in definitions) or len({d[0] for d in definitions if d})!=1:
                return result('missing',[{'code':'missing_radius','view':view['id'],'refs':[curved[0]['id']], 'message':'Slot end radius is not explicitly dimensioned'}])
            solved=[[locate(axis,p[axis],'slot centre '+str(i)) for axis in (0,1)] for i,p in enumerate(centers)]
            opening={'kind':'slot','centers':solved,'radius':definitions[0][0],'depth':'through'}
            note='SLOTS: THRU'
        if not any(n.strip().upper() == note for n in packet["notes"]):
            gaps.append({"code": "ambiguous_opening_depth", "view": view["id"], "refs": [e['id'] for e in group],
                         'opening_kind':opening['kind'],"message": "Internal profile could be through or blind; state its depth"})
        openings.append(opening)
    holes, consumed = [], set()
    if exterior: consumed.add(outer_circle['id'])
    for circle in circles:
        if circle['id'] in consumed: continue
        callout = circle_callout(circle, dims)
        if callout is None:
            continue  # A grouped callout may cover this member below.
        group = [c for c in circles if c['id'] not in consumed and near(c['radius'], circle['radius'])
                 and (circle_callout(c,dims) is None or circle_callout(c,dims)['id']==callout['id'])]
        if len(group) != callout['count']:
            if callout['count']==1:
                group=[circle]
            else:
                return result('unsupported', [{'code':'callout_scope', 'message':'Callout multiplicity cannot be assigned uniquely to drawn circles'}])
        if callout['depth'] is None:
            gaps.append({'code':'missing_hole_depth','view':view['id'],'refs':[c['id'] for c in group],
                         'dimension':callout['id'],'message':'Circle must state a hole depth/THRU; a size alone does not distinguish hole and boss'})
        pattern = None
        if len(group) >= 3 and 'EQ SP' in callout['text']:
            pitch = [(c, circle_callout(c,dims)) for c in view['circles'] if c['construction']]
            pitch = [(c,d) for c,d in pitch if d]
            if len(pitch) == 1:
                pc, pd = pitch[0]
                center = [locate(a,pc['center'][a],'pitch-circle centre') for a in (0,1)]
                phase = next((re.fullmatch(rf'BOLT CIRCLE START:\s*({NUMBER})° FROM VIEW HORIZONTAL', n) for n in packet['notes']
                              if re.fullmatch(rf'BOLT CIRCLE START:\s*({NUMBER})° FROM VIEW HORIZONTAL', n)), None)
                if phase:
                    pattern = (center, pd['diameter']/2, math.radians(float(phase[1].replace(',','.'))))
                else:
                    gaps.append({'code':'missing_pattern_angle','view':view['id'],'refs':[c['id'] for c in group],
                                 'center':pc['center'],'count':len(group),'message':'State the bolt-circle start angle relative to the view horizontal'})
                    pattern = (center,pd['diameter']/2,None)
                used.add(pd['id'])
        for index, item in enumerate(group):
            if pattern:
                origin,radius,phase=pattern
                center = [origin[0]+radius*math.cos(phase+2*math.pi*index/len(group)),
                          origin[1]+radius*math.sin(phase+2*math.pi*index/len(group))] if phase is not None and None not in origin else [None,None]
            else:
                center = [locate(a, item['center'][a], 'hole centre '+item['id']) for a in (0, 1)]
            holes.append(dict(center=center, **callout))
            consumed.add(item['id'])
            if callout['counterbore']:
                rings = [c for c in circles if c['id'] not in consumed and same_point(c['center'],item['center'])]
                if len(rings) != 1: return result('unsupported',[{'code':'counterbore_scope','message':'Counterbore rings cannot be uniquely identified'}])
                consumed.add(rings[0]['id'])
        used.add(callout['id'])
    for circle in circles:
        if circle['id'] not in consumed:
            gaps.append({'code':'missing_diameter','view':view['id'],'refs':circle['ids'][:1], 'message':'A drawn circle has no controlling callout'})
    unhandled=[d['id'] for d in packet['dimensions'] if d['id'] not in used and (d['view']!=view['id'] or numeric(d['text']) is None)]
    if unhandled:
        return result('unsupported',[{'code':'unconsumed_controls','message':'Existing controls are outside this construction; do not add redundant dimensions',
                                      'dimensions':unhandled}])
    if gaps:
        unique = {repr(sorted(g.items())): g for g in gaps}
        return result('missing', list(unique.values()))
    if min(width, height, thickness) <= 0:
        return result('conflict',[{'code':'nonpositive_size','message':'Printed sizes must be positive'}])
    unconsumed=[d['id'] for d in packet['dimensions'] if d['id'] not in used]
    if unconsumed:
        return result('unsupported',[{'code':'unconsumed_controls','message':'Other views/sections contain controlling dimensions not represented by this construction',
                                      'dimensions':unconsumed}])
    return result('planned', [], plan={'profile': 'rectangle' if rectangular else 'circle', 'width':width,'height':height,
                  'thickness':thickness,'holes':holes,'openings':openings,'frame':view['frame']},
                  provenance=provenance, consumed_dimensions=len(used), view=view['id'])


def dot(a, b):
    return sum(x*y for x,y in zip(a,b))


def outer_profile_diameter(packet, profile):
    """Bind an explicit full-span side-view diameter to a circular end profile."""
    for other in packet['views']:
        if other['kind'] != 'ortho' or abs(dot(other['frame'][2],profile['frame'][2])) > .01: continue
        for dim in packet['dimensions']:
            if dim['view'] != other['id'] or dim['kind'] not in ('DistanceX','DistanceY') or len(dim['witnesses']) != 2: continue
            match=re.fullmatch(rf'[⌀Ø]\s*({NUMBER})',dim['text'])
            if not match: continue
            axis=int(dim['kind']=='DistanceY')
            ends=sorted(p[axis] for p in dim['witnesses'])
            if (abs(dot(other['frame'][axis],profile['frame'][2])) < .01
                    and near(ends[0],other['bounds'][axis]) and near(ends[1],other['bounds'][axis+2])):
                return {'diameter':float(match[1].replace(',','.')),'id':dim['id']}
    return None


def find_thickness(packet, profile):
    found = []
    for index, note in enumerate(packet['notes']):
        match = re.fullmatch(rf'\s*t\s*=?\s*({NUMBER})\s*',note,re.I)
        if match: found.append((float(match[1].replace(',','.')),f'note:{index}'))
    for view in packet['views']:
        if view['kind'] != 'ortho': continue
        for dim in packet['dimensions']:
            if dim['view'] != view['id'] or dim['kind'] not in ('DistanceX','DistanceY') or len(dim['witnesses'])!=2: continue
            axis = int(dim['kind']=='DistanceY')
            if abs(dot(view['frame'][axis],profile['frame'][2])) < .99: continue
            ends = sorted(p[axis] for p in dim['witnesses'])
            value = numeric(dim['text'])
            if value is not None and near(ends[0],view['bounds'][axis]) and near(ends[1],view['bounds'][axis+2]):
                found.append((value,dim['id']))
    if not found or max(x[0] for x in found)-min(x[0] for x in found)>1e-5: return None, []
    return found[0][0],[x[1] for x in found]


def interpret_revolved(packet):
    """A symmetric line profile controlled by explicit diameters and stations."""
    views=[v for v in packet['views'] if v['kind']=='ortho']
    if len(views)!=1: return None
    view=views[0]
    if view['circles'] or not view['edges'] or any(e['kind']!='line' for e in view['edges']): return None
    dims=[d for d in packet['dimensions'] if d['view']==view['id']]
    diameters=[]
    for d in dims:
        m=re.fullmatch(rf'[⌀Ø]\s*({NUMBER})',d['text'])
        if m and d['kind'] in ('DistanceX','DistanceY') and len(d['witnesses'])==2:
            diameters.append((d,float(m[1].replace(',','.'))))
    if not diameters: return None
    radial=int(diameters[0][0]['kind']=='DistanceY'); axial=1-radial
    if any(int(d['kind']=='DistanceY')!=radial for d,_ in diameters): return None
    centers=[sum(p[radial] for p in d['witnesses'])/2 for d,_ in diameters]
    if max(centers)-min(centers)>PAPER_TOL: return None
    center=centers[0]
    stations=Coordinates(view['bounds'][axial]); radii=Coordinates(0.0)
    provenance={}
    used=set()
    for dim, diameter in diameters:
        ordinate=max(abs(p[radial]-center) for p in dim['witnesses'])
        radii.connect(0,ordinate,diameter/2,dim['id'])
        used.add(dim['id'])
    for dim in dims:
        if len(dim['witnesses'])!=2: continue
        value=numeric(dim['text'])
        if value is not None and int(dim['kind']=='DistanceY')==axial:
            stations.connect(dim['witnesses'][0][axial],dim['witnesses'][1][axial],value,dim['id'])
            used.add(dim['id'])
        chamfer=re.search(rf'\bC\s*({NUMBER})',dim['text'])
        if chamfer:
            leg=float(chamfer[1].replace(',','.'))
            paper_leg=abs(dim['witnesses'][0][axial]-dim['witnesses'][1][axial])
            intervals=set()
            for edge in view['edges']:
                a,b=edge['ends']
                if near(abs(a[axial]-b[axial]),paper_leg) and near(abs(a[radial]-b[radial]),paper_leg):
                    stations.connect(a[axial],b[axial],leg,dim['id'])
                    radii.connect(abs(a[radial]-center),abs(b[radial]-center),leg,dim['id'])
                    used.add(dim['id'])
                    intervals.add(tuple(sorted((round(a[axial],9),round(b[axial],9)))))
            count=re.match(r'\s*(\d+)\s*[×x]',dim['text'])
            if len(intervals)!=(int(count[1]) if count else 1):
                return result('unsupported',[{'code':'chamfer_count','message':'Chamfer multiplicity does not match the drawn transitions'}])
    try:
        stations.solve();radii.solve()
    except ValueError as error:
        return result('conflict',[{'code':'conflicting_dimensions','message':str(error)}])
    used &= stations.active_sources|radii.active_sources
    unconsumed=[d['id'] for d in packet['dimensions'] if d['id'] not in used]
    if unconsumed: return result('unsupported',[{'code':'unconsumed_controls','message':'Uninterpreted profile controls','dimensions':unconsumed}])
    positions=[]
    for edge in view['edges']:
        for p in edge['ends']:
            if not any(near(p[axial],x) for x in positions): positions.append(p[axial])
    positions.sort()
    profile,gaps=[],[]
    for left,right in zip(positions,positions[1:]):
        mid=(left+right)/2
        spanning=[]
        for edge in view['edges']:
            a,b=edge['ends']
            if abs(a[axial]-b[axial])<1e-8 or not min(a[axial],b[axial])<mid<max(a[axial],b[axial]): continue
            y=a[radial]+(mid-a[axial])*(b[radial]-a[radial])/(b[axial]-a[axial])
            spanning.append((y,edge))
        if not spanning: return None
        if not near(max(y for y,_ in spanning)+min(y for y,_ in spanning),2*center): return None
        _,edge=max(spanning,key=lambda item:item[0])
        a,b=edge['ends']
        if not near(a[radial],b[radial]) and not (near(min(a[axial],b[axial]),left) and near(max(a[axial],b[axial]),right)):
            return None
        for x in (left,right):
            y=a[radial]+(x-a[axial])*(b[radial]-a[radial])/(b[axial]-a[axial])
            position=stations.value(x); radius=radii.value(abs(y-center))
            point=[0.0,0.0];point[axial]=x;point[radial]=y
            if position is None:
                start=list(point);start[axial]=view['bounds'][axial]
                gaps.append({'code':'missing_location','view':view['id'],'axis':axial,'a':start,'b':point,
                             'message':'An axial station is not dimensioned'})
            if radius is None:
                opposite=list(point);opposite[radial]=2*center-y
                gaps.append({'code':'missing_profile_diameter','view':view['id'],'axis':radial,'a':opposite,'b':point,
                             'message':'A shaft diameter/chamfer endpoint is not constrained'})
            if position is not None and radius is not None:
                vertex=[position[0],radius[0]]
                if not profile or profile[-1]!=vertex: profile.append(vertex)
                provenance[f'profile:{x:.6f}:{y:.6f}']=sorted(set(position[1]+radius[1]))
    if gaps: return result('missing',list({repr(sorted(g.items())):g for g in gaps}.values()))
    x,y=view['frame'][axial],view['frame'][radial]
    z=[x[1]*y[2]-x[2]*y[1],x[2]*y[0]-x[0]*y[2],x[0]*y[1]-x[1]*y[0]]
    return result('planned',[],plan={'profile':'revolved','points':profile,'frame':[x,y,z]},
                  provenance=provenance,consumed_dimensions=len(used),view=view['id'])

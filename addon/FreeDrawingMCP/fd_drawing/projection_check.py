"""Check every drawn orthographic curve against the independently built solid."""
import math
from pathlib import Path

import FreeCAD as App

from .paper_geometry import capture


def check_views(doc, model, packet, output_dir, tolerance=1e-5):
    page=doc.addObject('TechDraw::DrawPage','ReconstructionProjectionCheck')
    path=Path(output_dir)/'projection-template.svg'
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="420mm" height="297mm" viewBox="0 0 420 297"></svg>',encoding='utf-8')
    template=doc.addObject('TechDraw::DrawSVGTemplate','ReconstructionTemplate')
    template.Template=str(path);page.Template=template
    doc.recompute()
    rows=[]
    for expected in packet['views']:
        paper_tolerance=tolerance*expected['scale']
        view=doc.addObject('TechDraw::DrawViewPart','CheckView')
        page.addView(view)
        view.Source=[model]
        view.Direction=App.Vector(*expected['frame'][2]);view.XDirection=App.Vector(*expected['frame'][0])
        view.ScaleType='Custom';view.Scale=expected['scale']
        view.HardHidden=expected.get('hard_hidden',False);view.SmoothVisible=False;view.SeamVisible=False
        doc.recompute()
        actual=capture(view)
        if actual['bounds'] is None:
            rows.append({'view':expected['id'],'match':False,'reason':'No independent projection'})
            doc.removeObject(view.Name);continue
        dx=expected['bounds'][0]-actual['bounds'][0];dy=expected['bounds'][1]-actual['bounds'][1]
        curves=all_curves(actual)
        for curve in curves:
            if 'center' in curve:curve['center']=[curve['center'][0]+dx,curve['center'][1]+dy]
            if 'ends' in curve:curve['ends']=[[p[0]+dx,p[1]+dy] for p in curve['ends']]
        described=all_curves(expected)
        missing=[c['id'] for c in described if not covered(c,curves,paper_tolerance)]
        extra=[c['id'] for c in curves if not covered(c,described,paper_tolerance)]
        topology=topology_counts(described)==topology_counts(curves)
        rows.append({'view':expected['id'],'match':not missing and not extra and topology,
                     'paper_tolerance_mm':paper_tolerance,'topology_match':topology,
                     'unexplained_drawing_curves':missing,'extra_rebuilt_curves':extra})
        doc.removeObject(view.Name)
    template_name=template.Name
    doc.removeObject(page.Name)
    if doc.getObject(template_name) is not None: doc.removeObject(template_name)
    doc.recompute()
    return {'match':all(row['match'] for row in rows),'model_tolerance_mm':tolerance,'views':rows}


def topology_counts(curves):
    edges=[c for c in curves if c['kind']!='circle']
    parents=list(range(len(edges)))
    def root(i):
        while parents[i]!=i:i=parents[i]
        return i
    for i,a in enumerate(edges):
        for j,b in enumerate(edges[:i]):
            touching=any(math.dist(p,q)<1e-8 for p in a['ends'] for q in b['ends'])
            for line,other in ((a,b),(b,a)):
                if line['kind']!='line':continue
                p,q=line['ends'];length=math.dist(p,q)
                if length<1e-9:continue
                ux,uy=(q[0]-p[0])/length,(q[1]-p[1])/length
                touching=touching or any(abs((r[0]-p[0])*uy-(r[1]-p[1])*ux)<1e-8 and
                    -1e-8<=(r[0]-p[0])*ux+(r[1]-p[1])*uy<=length+1e-8 for r in other['ends'])
            if touching:parents[root(i)]=root(j)
    return sum(c['kind']=='circle' for c in curves),len({root(i) for i in range(len(edges))})


def all_curves(view):
    return [dict(edge) for edge in view['edges']]+[dict(circle,kind='circle',interval=[0,2*math.pi])
                                                for circle in view['circles'] if not circle['construction']]


def covered(curve, others, tolerance):
    if curve['kind']=='line':
        a,b=curve['ends'];length=math.dist(a,b)
        if length<1e-9:return True
        x,y=(b[0]-a[0])/length,(b[1]-a[1])/length
        intervals=[]
        for other in others:
            if other['kind']!='line':continue
            if any(abs((p[0]-a[0])*y-(p[1]-a[1])*x)>tolerance for p in other['ends']):continue
            intervals.append(sorted((p[0]-a[0])*x+(p[1]-a[1])*y for p in other['ends']))
        return interval_covered([0,length],intervals,tolerance)
    if curve['kind'] in ('arc','circle'):
        matches=[o for o in others if o['kind'] in ('arc','circle') and math.dist(curve['center'],o['center'])<=tolerance
                 and abs(curve['radius']-o['radius'])<=tolerance]
        intervals=[part for o in matches for part in periodic(o['interval'])]
        return all(interval_covered(part,intervals,tolerance/max(curve['radius'],.001)) for part in periodic(curve['interval']))
    return False


def periodic(interval):
    a,b=interval
    if b-a>=2*math.pi-1e-7:return [[0,2*math.pi]]
    end=a%(2*math.pi)+(b-a);start=a%(2*math.pi)
    return [[start,end]] if end<=2*math.pi else [[start,2*math.pi],[0,end-2*math.pi]]


def interval_covered(target,intervals,tolerance):
    current=target[0]
    for lo,hi in sorted(intervals):
        if hi<current:continue
        if lo>current+tolerance:return False
        current=max(current,hi)
        if current>=target[1]-tolerance:return True
    return current>=target[1]-tolerance

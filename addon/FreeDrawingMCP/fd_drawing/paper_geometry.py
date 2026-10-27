"""Capture drawn 2D curves without reading any source solid or source parameter."""
import math

from . import geometry as G


def capture(view):
    visibility=G.visibility_map(view)
    circles_by_geometry={}
    lines={}
    unsupported=[]
    points_for_bounds=[]
    scale=G.view_scale(view)
    for index,edge in G.iter_edges(view,geom_only=False):
        if index==4999:
            unsupported.append({'id':'geometry-limit','kind':'unsupported','ends':[],'visibility':'solid'})
        name=f'Edge{index}'
        auxiliary=G.auxiliary_edge(view,index)
        construction=auxiliary is not None
        solid=visibility.get(index,True)
        if not construction and not solid and not bool(view.HardHidden): continue
        curve=type(edge.Curve).__name__
        style='solid' if solid else 'hidden'
        if curve=='Circle':
            center=list(G.local_to_page(view,(edge.Curve.Center.x,edge.Curve.Center.y)))
            radius=float(edge.Curve.Radius)*scale
            key=tuple(round(x,9) for x in center+[radius])+ (construction,)
            item=circles_by_geometry.setdefault(key,{'center':center,'radius':radius,'construction':construction,'arcs':[]})
            interval=(0,2*math.pi) if edge.isClosed() else G.arc_interval(edge)
            ends=[list(G.local_to_page(view,(v.Point.x,v.Point.y))) for v in edge.Vertexes]
            item['arcs'].append((name,interval,ends,style))
            if not construction:
                points_for_bounds.extend(G.local_to_page(view,p) for p in G.discretize(edge,.2))
            continue
        if construction: continue
        ends=[list(G.local_to_page(view,(v.Point.x,v.Point.y))) for v in edge.Vertexes]
        if curve!='Line' and hasattr(edge.Curve,'getPoles'):
            poles=[G.local_to_page(view,(p.x,p.y)) for p in edge.Curve.getPoles()]
            if poles:
                axis=max((0,1),key=lambda a:max(p[a] for p in poles)-min(p[a] for p in poles))
                a,b=min(poles,key=lambda p:p[axis]),max(poles,key=lambda p:p[axis])
                length=math.hypot(b[0]-a[0],b[1]-a[1])
                if length>1e-9 and all(abs((p[0]-a[0])*(b[1]-a[1])-(p[1]-a[1])*(b[0]-a[0]))/length<1e-8 for p in poles):
                    curve='Line'
                    if len(ends)!=2 or math.dist(*ends)<1e-9:
                        box=G.exact_bbox(edge)
                        ends=[list(G.local_to_page(view,(box.XMin,box.YMin))),list(G.local_to_page(view,(box.XMax,box.YMax)))]
        if curve=='Line' and len(ends)==2 and math.dist(*ends)>1e-9:
            key=tuple(sorted(tuple(round(c,9) for c in p) for p in ends))
            if key not in lines or style=='solid': lines[key]={'id':name,'kind':'line','ends':ends,'visibility':style}
            points_for_bounds.extend(ends)
        else:
            # A closed spline/ellipse must not vanish merely because it has
            # only one vertex. Unsupported geometry remains explicit evidence.
            unsupported.append({'id':name,'kind':'unsupported','ends':ends,'visibility':style})
            points_for_bounds.extend(G.local_to_page(view,p) for p in G.discretize(edge,.2))
    circles=[]
    arcs=[]
    for item in circles_by_geometry.values():
        entries=item.pop('arcs')
        complete=G.angular_coverage([entry[1] for entry in entries])>=2*math.pi-1e-7
        visible=G.angular_coverage([entry[1] for entry in entries if entry[3]=='solid'])>=2*math.pi-1e-7
        if complete:
            circles.append(dict(item,id=entries[0][0],ids=[entry[0] for entry in entries],full=True,
                                visibility='solid' if visible else 'hidden'))
            if not item['construction']:
                x,y=item['center'];r=item['radius']
                points_for_bounds.extend([(x-r,y-r),(x+r,y+r)])
        else:
            seen=set()
            for name,interval,ends,style in entries:
                key=(tuple(round(v,9) for v in interval),style)
                if key in seen: continue
                seen.add(key)
                arcs.append({'id':name,'kind':'arc','ends':ends,'center':item['center'],'radius':item['radius'],'visibility':style,'interval':list(interval)})
    return {'edges':list(lines.values())+arcs+unsupported,'circles':circles,'bounds':G.bbox_of(points_for_bounds)}

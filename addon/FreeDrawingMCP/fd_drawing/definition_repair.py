"""Publish missing facts as visible annotations before another independent build."""
import math

import FreeCAD as App
import Part

from . import builder, dimensions, features, geometry as G, planner, sections
from .analysis import cylinders
from .snapshot import _annotation_record, _source_center, page_views


def repair(doc, page, gaps):
    doc.openTransaction('Visible definition repair')
    changes, rejected = [], []
    opening_groups={}
    for gap in gaps:
        if gap['code']=='ambiguous_opening_depth':opening_groups.setdefault(gap.get('opening_kind','rectangle'),[]).append(gap)
    # Validate the whole scope before publishing a plural manufacturing note.
    for kind, group in opening_groups.items():
        try:
            defining=[v for v in page_views(page) if v.TypeId=='TechDraw::DrawProjGroupItem' and bool(v.Visibility)]
            profile=doc.getObject(group[0]['view'])
            if any(g['view']!=profile.Name for g in group):
                raise ValueError('Multiple opening profiles need leader-scoped depth callouts')
            if len(defining)>1:
                prove_uniform_extrusion(profile.Source[0].Shape,G.axis_name(profile.Direction))
            for gap in group: prove_through(doc.getObject(gap['view']),gap['refs'])
            append_note(doc,page,'SLOTS: THRU' if kind=='slot' else 'RECTANGULAR OPENINGS: THRU')
            changes.append({'code':'ambiguous_opening_depth','published':'verified through-opening group','members':len(group)})
        except Exception as error: rejected.append('ambiguous_opening_depth: '+str(error))
    for gap in gaps:
        if gap['code']=='ambiguous_opening_depth':continue
        view=doc.getObject(gap.get('view',''))
        if view is None or not G.is_type(view,'TechDraw::DrawViewPart'):
            rejected.append(gap.get('code','unknown')); continue
        code=gap['code']
        try:
            if code in ('missing_location','missing_profile_diameter'):
                refs=[vertex(view,p) for p in (gap['a'],gap['b'])]
                axis=gap['axis']
                dim=dimensions.make_dimension(doc,page,view,'DistanceY' if axis else 'DistanceX',refs,'⌀%.6w' if code=='missing_profile_diameter' else '%.6w',
                        0 if not axis else G.view_local_bbox(view)[2]*G.view_scale(view)+15,
                        G.view_local_bbox(view)[3]*G.view_scale(view)+15 if not axis else 0,role='definition-repair')
                builder._style_dim(dim)
                changes.append({'code':code,'dimension':dim.Name})
            elif code in ('missing_diameter','missing_radius'):
                dim=dimensions.make_dimension(doc,page,view,'Radius' if code=='missing_radius' else 'Diameter',gap['refs'],
                                              'R%.6w' if code=='missing_radius' else '⌀%.6w',20,20,role='definition-repair')
                builder._style_dim(dim)
                changes.append({'code':code,'dimension':dim.Name})
            elif code == 'missing_thickness':
                original_names={obj.Name for obj in doc.Objects}
                projection=G.proj_group_of(view)
                original_views=[(v,float(v.X),float(v.Y),bool(v.Visibility)) for v in projection.Views] if projection else []
                try:
                    dim=publish_thickness_dimension(doc,page,view)
                except Exception:
                    if projection:
                        for v in list(projection.Views):
                            if v.Name not in original_names:projection.removeProjection(str(v.Type))
                    for obj in list(doc.Objects):
                        if obj.Name not in original_names:doc.removeObject(obj.Name)
                    for v,x,y,visible in original_views:v.X,v.Y,v.Visibility=x,y,visible
                    doc.recompute()
                    raise
                changes.append({'code':code,'dimension':dim.Name,'published':'dimensioned thickness view'})
            elif code == 'missing_pattern_angle':
                centers=[]
                for ref in gap['refs']:
                    curve=view.getEdgeByIndex(int(ref[4:])).Curve
                    centers.append(G.local_to_page(view,(curve.Center.x,curve.Center.y)))
                cx,cy=gap['center']
                step=360/gap['count']
                phases=[math.degrees(math.atan2(y-cy,x-cx))%step for x,y in centers]
                residuals=[(p-phases[0]+step/2)%step-step/2 for p in phases]
                if max(residuals)-min(residuals) > .02: raise ValueError('Pattern is not consistently equally spaced')
                phase=(phases[0]+sum(residuals)/len(residuals))%step
                if min(phase,step-phase)<1e-6:phase=0.0
                append_note(doc,page,f'BOLT CIRCLE START: {phase:.6f}'.rstrip('0').rstrip('.')+'° FROM VIEW HORIZONTAL')
                changes.append({'code':code,'published':'bolt-circle angle'})
            elif code == 'missing_hole_depth':
                holes=features.hole_stacks(view.Source[0].Shape,cylinders(view.Source[0].Shape))
                center3d=_source_center(view)
                x=App.Vector(view.XDirection);y=G.up_vector(view.Direction,x)
                matched=[]
                for ref in gap['refs']:
                    curve=view.getEdgeByIndex(int(ref[4:])).Curve
                    candidates=[]
                    for hole in holes:
                        delta=App.Vector(*hole['center'])-center3d
                        if (hole['axis']==G.axis_name(App.Vector(view.Direction)) and abs(hole['dia']/2-curve.Radius)<1e-5
                                and abs(delta.dot(x)-curve.Center.x)<1e-5 and abs(delta.dot(y)-curve.Center.y)<1e-5):
                            candidates.append(hole)
                    if len(candidates)!=1: raise ValueError('Circle cannot be identified as one cylindrical hole')
                    matched.append(candidates[0])
                signatures={(h['through'],round(h['depth'],6)) for h in matched}
                if len(signatures)!=1: raise ValueError('Hole members have different depths; separate callouts are needed')
                through,depth=next(iter(signatures))
                dim=doc.getObject(gap['dimension'])
                if dim is None or dim.TypeId!='TechDraw::DrawViewDimension': raise ValueError('Controlling dimension is unavailable')
                suffix=' THRU' if through else ' ↧'+f'{depth:.6f}'.rstrip('0').rstrip('.')
                # Publish depth before any secondary counterbore/countersink clause.
                text=str(dim.FormatSpec)
                position=min([text.index(symbol) for symbol in ('⌴','⌵') if symbol in text]+[len(text)])
                dim.FormatSpec=text[:position].rstrip()+suffix+(' '+text[position:] if position<len(text) else '')
                changes.append({'code':code,'dimension':dim.Name,'published':'hole depth/through callout'})
            else:
                rejected.append(code)
        except Exception as error:
            rejected.append(f'{code}: {error}')
    doc.recompute()
    if changes:
        dims=[v for v in page.Views if v.TypeId=='TechDraw::DrawViewDimension']
        views=[v for v in page_views(page) if G.is_type(v,'TechDraw::DrawViewPart') and bool(v.Visibility)]
        from .annotation_layout import clear_collisions
        frame,title=sections._sheet(page)
        keepouts=[title] if title else []
        for obj in page.Views:
            if obj.TypeId=='TechDraw::DrawViewAnnotation': keepouts.append(_annotation_record(obj)['bbox'])
            elif obj.TypeId=='TechDraw::DrawViewImage':
                x,y=G.view_origin(obj); w,h=float(obj.Width),float(obj.Height)
                keepouts.append([x-w/2,y-h/2,x+w/2,y+h/2])
        clearance=clear_collisions(page,dims,views,bounds=frame,keepouts=keepouts)
        if clearance['unresolved']:
            doc.abortTransaction()
            return {'changed':False,'changes':[],'rejected':rejected+['No clear placement for definition repair; use a larger sheet'],
                    'layout_unresolved':clearance['unresolved']}
        doc.recompute()
        doc.commitTransaction()
        doc.save()
    else:doc.abortTransaction()
    return {'changed':bool(changes),'changes':changes,'rejected':rejected}


def publish_thickness_dimension(doc,page,profile):
    shape=profile.Source[0].Shape
    axis=G.axis_name(App.Vector(profile.Direction))
    if not axis:raise ValueError('Oblique thickness requires a dimensioned auxiliary view')
    prove_stock_depth(shape,axis)
    group=G.proj_group_of(profile)
    if group is None:raise ValueError('Thickness repair requires a standard projection group')
    index='XYZ'.index(axis)
    available=[v for v in group.Views if bool(v.Visibility) and abs(v.Direction[index])<.01]
    added=None
    if not available:
        for name in ('Right','Top','Left','Bottom'):
            existing=next((v for v in group.Views if str(v.Type)==name),None)
            view=existing or group.addProjection(name)
            doc.recompute()
            if abs(view.Direction[index])<.01:
                added=view
                view.Visibility=True
                view.HardHidden=True
                view.SmoothVisible=False
                view.SeamVisible=False
                builder._style_view(view)
                view.X=view.Y=10000
                available=[view]
                break
            if existing is None:group.removeProjection(name)
    if not available:raise ValueError('No standard view exposes the thickness')
    doc.recompute()
    box=G.exact_bbox(shape)
    requirement={'kind':'loc','axis':axis,'datum':getattr(box,axis+'Min'),'coord':getattr(box,axis+'Max'),
                 'source':'thickness','center':False,'prio':4}
    geoms=[dimensions.ViewGeom(v,_source_center(v),str(v.Type)) for v in available]
    specs,unplaced=planner.realize([requirement],geoms,{})
    if unplaced or len(specs)!=1:raise ValueError('No geometric witnesses for the thickness dimension')
    created=dimensions.place(specs,page,doc)
    for dim in created:builder._style_dim(dim)
    doc.recompute()
    if added:
        from .reflow import footprint
        origin=G.view_origin(added)
        box=footprint([added],created,[])
        relative=[box[0]-origin[0],box[1]-origin[1],box[2]-origin[0],box[3]-origin[1]]
        position=sections.place_extra_views(page,[{'rel_box':relative,'near':G.view_page_bbox(profile),'room':3}])[0]
        if position is None:raise ValueError('No sheet space for a dimensioned thickness view')
        added.X=float(added.X)+position[0]-origin[0]
        added.Y=float(added.Y)+position[1]-origin[1]
    return created[0]


def prove_uniform_extrusion(shape,axis):
    """Additional edge views cannot conceal a different depth for a global note."""
    if not axis:raise ValueError('Opening depth needs an axis-aligned profile')
    box=G.exact_bbox(shape);low=getattr(box,axis+'Min');length=getattr(box,axis+'Length')
    faces=[face for face in shape.Faces if type(face.Surface).__name__=='Plane'
           and G.axis_name(face.Surface.Axis)==axis and abs(getattr(face.BoundBox,axis+'Min')-low)<1e-6]
    if not faces:raise ValueError('No planar end face for the opening scope')
    vector=App.Vector(*[length if letter==axis else 0 for letter in 'XYZ'])
    swept=Part.makeCompound([face.extrude(vector) for face in faces])
    if shape.cut(swept).Volume+swept.cut(shape).Volume>1e-6:
        raise ValueError('Multiple views do not define a uniform extrusion; use scoped opening depth callouts')


def vertex(view, point):
    local=((point[0]-G.view_origin(view)[0])/G.view_scale(view),(point[1]-G.view_origin(view)[1])/G.view_scale(view))
    for index,p in G.iter_vertices(view):
        if G.dist(p,local) < 1e-7: return f'Vertex{index}'
    center=_source_center(view)
    world=center+App.Vector(view.XDirection)*local[0]+G.up_vector(view.Direction,view.XDirection)*local[1]
    view.makeCosmeticVertex3d(world)
    view.Document.recompute()
    for index,p in G.iter_vertices(view):
        if G.dist(p,local)<1e-5: return f'Vertex{index}'
    raise ValueError('Could not attach dimension to drawing witness')


def prove_through(view,refs):
    edges=[view.getEdgeByIndex(int(ref[4:])) for ref in refs]
    wires=Part.sortEdges(edges)
    if len(wires)!=1: raise ValueError('Opening boundary is not one closed profile')
    face=Part.Face(Part.Wire(wires[0]));shape=view.Source[0].Shape
    length=shape.BoundBox.DiagonalLength+1
    face.translate(App.Vector(0,0,-length));column=face.extrude(App.Vector(0,0,2*length))
    x=App.Vector(view.XDirection);y=G.up_vector(view.Direction,x);z=App.Vector(view.Direction)
    matrix=App.Matrix()
    matrix.A11,matrix.A21,matrix.A31=x
    matrix.A12,matrix.A22,matrix.A32=y
    matrix.A13,matrix.A23,matrix.A33=z
    matrix.A14,matrix.A24,matrix.A34=_source_center(view)
    column.transformShape(matrix,True)
    if column.common(shape).Volume>1e-6:raise ValueError('At least one opening is blind; no global THRU note was added')


def prove_stock_depth(shape,axis):
    box=G.exact_bbox(shape);low=getattr(box,axis+'Min');high=getattr(box,axis+'Max')
    ends=[[],[]]
    for face in shape.Faces:
        if type(face.Surface).__name__!='Plane' or G.axis_name(face.Surface.Axis)!=axis:continue
        coordinate=getattr(face.BoundBox,axis+'Min')
        for i,value in enumerate((low,high)):
            if abs(coordinate-value)<1e-6:ends[i].append(Part.Face(face.OuterWire))
    if not all(ends):raise ValueError('No corresponding planar stock faces; add an overall-depth dimension instead')
    a,b=(Part.makeCompound(faces) for faces in ends)
    vector=App.Vector(*[(high-low) if letter==axis else 0 for letter in 'XYZ'])
    a.translate(vector)
    if a.cut(b).Area+b.cut(a).Area>1e-6:
        raise ValueError('Stepped/bossed envelope has no single stock thickness; no global thickness note was added')


def append_note(doc,page,text):
    note=next((v for v in page.Views if v.TypeId=='TechDraw::DrawViewAnnotation' and v.Name.startswith('Notes')),None)
    created=note is None
    if created: note=builder.add_note(doc,page,[],40,30)
    if text in list(note.Text): return
    previous=list(note.Text)
    note.Text=list(note.Text)+[text]
    doc.recompute()
    box=_annotation_record(note)['bbox']
    width,height=box[2]-box[0],box[3]-box[1]
    position=sections.place_extra_views(page,[{'rel_box':[-width/2,-height/2,width/2,height/2],'room':2}])[0]
    if position is None:
        if created:doc.removeObject(note.Name)
        else:note.Text=previous
        doc.recompute()
        raise ValueError('No space for the added definition note; use a larger sheet')
    note.X,note.Y=position

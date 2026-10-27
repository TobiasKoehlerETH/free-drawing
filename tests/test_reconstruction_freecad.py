"""Real geometry and independent source comparison, including an actual repair loop."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from conftest import ROOT
from free_drawing_mcp.backends import HeadlessBackend, detect_freecadcmd
from free_drawing_mcp.definition import reconstruct_evidence, verify_definition
from test_drawing_definition import plate_evidence

pytestmark=[pytest.mark.freecad,pytest.mark.skipif(detect_freecadcmd() is None,reason='FreeCADCmd unavailable')]


def test_isolated_worker_builds_without_source_and_wrong_equal_volume_geometry_fails(tmp_path):
    packet=plate_evidence()
    evidence=tmp_path/'drawing.json';evidence.write_text(json.dumps(packet),encoding='utf-8')
    a=reconstruct_evidence(evidence,tmp_path/'a')
    assert a['status']=='rebuilt' and Path(a['step']).is_file()
    # Change only a printed hole position. Volume and overall extents stay equal.
    packet['dimensions'][2]['text']='5'
    packet['dimensions'][2]['witnesses'][1][0]=110
    packet['dimensions'][3]['witnesses'][1][0]=110
    packet['views'][0]['circles'][0]['center'][0]=110
    changed=tmp_path/'changed.json';changed.write_text(json.dumps(packet),encoding='utf-8')
    b=reconstruct_evidence(changed,tmp_path/'b')
    assert b['status']=='rebuilt'
    frame=tmp_path/'frame.json';frame.write_text(json.dumps({'basis':[[1,0,0],[0,1,0],[0,0,1]]}))
    backend=HeadlessBackend(timeout=120)
    same,different=backend.run([
        {'op':'compare_definition','args':{'source':a['step'],'rebuilt':a['step'],'frame_path':str(frame)}},
        {'op':'compare_definition','args':{'source':a['step'],'rebuilt':b['step'],'frame_path':str(frame)}},
    ])
    assert same['status']=='match_within_scope'
    assert different['status']=='mismatch'
    assert different['absolute_volume_difference_mm3']<1e-6
    assert different['symmetric_difference_mm3']>1
    job=json.loads((tmp_path/'a/job.json').read_text())
    assert set(job)=={'evidence','output','result','tolerance'}


def test_deleted_thickness_requires_visible_repair_before_rebuild(tmp_path):
    drawing=tmp_path/'plate.FCStd'
    source=ROOT/'samples/parts/plate_hole_pattern.step'
    backend=HeadlessBackend(timeout=300)
    created,snap=backend.run([
        {'op':'create_drawing','args':{'source':str(source),'output':str(drawing)}},
        {'op':'snapshot','args':{'doc':str(drawing)}},
    ])
    thickness=[d['name'] for d in snap['dimensions'] if d['type'] in ('DistanceX','DistanceY') and abs(d['value']-12)<1e-6]
    assert thickness
    backend.run([{'op':'apply_actions','args':{'doc':str(drawing),'actions':[{'op':'delete','names':thickness}]}}])
    report=verify_definition(backend,drawing,source,tmp_path/'verified',max_repairs=2)
    assert report['status']=='match_within_scope',report
    assert report['source_unchanged']
    assert len(report['attempts'])==2
    assert report['attempts'][0]['reconstruction']['status']=='missing'
    assert any(g['code']=='missing_thickness' for g in report['attempts'][0]['reconstruction']['gaps'])
    reopened=backend.run([{'op':'snapshot','args':{'doc':str(drawing)}}])[0]
    assert any(d['type'] in ('DistanceX','DistanceY') and abs(d['value']-12)<1e-6 for d in reopened['dimensions'])
    assert not any('t12' in a.get('text',[]) for a in reopened['annotations'])
    assert any(v['kind']=='pictorial' and v['shaded'] for v in reopened['views'])


def test_missing_thickness_view_is_added_with_a_dimension_instead_of_a_note(tmp_path):
    source=ROOT/'samples/parts/plate_hole_pattern.step'
    drawing=tmp_path/'single-view.FCStd';backend=HeadlessBackend(timeout=300)
    backend.run([{'op':'create_drawing','args':{'source':str(source),'output':str(drawing),
                 'views':['Front'],'sections':False,'details':False}}])
    report=verify_definition(backend,drawing,source,tmp_path/'definition')
    assert report['status']=='match_within_scope',report
    reopened=backend.run([{'op':'snapshot','args':{'doc':str(drawing)}}])[0]
    assert len([v for v in reopened['views'] if v['kind']=='ortho'])==2
    assert any(d['type'] in ('DistanceX','DistanceY') and abs(d['value']-12)<1e-6 for d in reopened['dimensions'])
    assert not any('t12' in a.get('text',[]) for a in reopened['annotations'])


@pytest.mark.parametrize('name,folder',[('turned_shaft','parts'),('micro_grooves','complex')])
def test_dimensioned_revolved_profiles_rebuild_from_drawing(tmp_path,name,folder):
    drawing=tmp_path/(name+'.FCStd');source=ROOT/'samples'/folder/(name+'.step')
    backend=HeadlessBackend(timeout=300)
    backend.run([{'op':'create_drawing','args':{'source':str(source),'output':str(drawing)}}])
    report=verify_definition(backend,drawing,source,tmp_path/'definition',max_repairs=0)
    assert report['status']=='match_within_scope',report
    assert report['attempts'][-1]['comparison']['symmetric_difference_mm3']<1e-6


def test_counterbore_depth_callout_removes_redundant_hidden_floor_dimension(tmp_path):
    from free_drawing_mcp.rules import inspect
    source=ROOT/'samples/parts/counterbored_block.step';drawing=tmp_path/'block.FCStd'
    backend=HeadlessBackend(timeout=300)
    created,snap=backend.run([{'op':'create_drawing','args':{'source':str(source),'output':str(drawing)}},
                              {'op':'snapshot','args':{'doc':str(drawing)}}])
    assert not created['sections']
    assert any('6.8' in d['text'] and '⌴' in d['text'] for d in snap['dimensions'])
    assert not any(d.get('value')==18.2 for d in snap['dimensions'])
    assert not [f for f in inspect(snap).findings if f.rule in ('D.1','E.5') and f.status in ('fail','warn')]
    report=verify_definition(backend,drawing,source,tmp_path/'definition',max_repairs=0)
    assert report['status']=='match_within_scope',report


def test_other_view_with_unexplained_geometry_blocks_reconstruction(tmp_path):
    import copy
    packet=plate_evidence();rear=copy.deepcopy(packet['views'][0])
    rear.update(id='rear',role='Rear',frame=[[-1,0,0],[0,1,0],[0,0,-1]])
    rear['circles'][0]['center'][0]=116
    rear['circles'].append({'id':'extra','ids':['extra'],'center':[104,110],'radius':2,'full':True,'construction':False})
    packet['views'].append(rear)
    evidence=tmp_path/'drawing.json';evidence.write_text(json.dumps(packet),encoding='utf-8')
    built=reconstruct_evidence(evidence,tmp_path/'rebuild')
    assert built['status']=='drawing_conflict',built
    assert not built['projection_check']['match']


def freecad_probe(tmp_path, body):
    output=tmp_path/'probe.json';script=tmp_path/'probe.py'
    script.write_text(f"import sys,json\nimport FreeCAD as App, Part\nsys.path.insert(0,{str(ROOT/'addon/FreeDrawingMCP')!r})\nsys.path.insert(0,{str(ROOT/'src')!r})\n"+body+f"\nwith open({str(output)!r},'w',encoding='utf-8') as stream: json.dump(result,stream)\n",encoding='utf-8')
    process=subprocess.run(detect_freecadcmd()+['-c',f"exec(open({str(script)!r},encoding='utf-8').read())"],
                           capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120,
                           env=dict(os.environ,QT_QPA_PLATFORM='offscreen'))
    assert process.returncode==0 and output.is_file(),process.stdout+process.stderr
    return json.loads(output.read_text(encoding='utf-8'))


def test_angle_clearance_exports_arc_and_preserves_measurement_after_reopen(tmp_path):
    result = freecad_probe(tmp_path, f'''
from fd_drawing import page as pages, dimensions, snapshot, annotation_layout, render
doc=App.newDocument('AnglePlacement')
model=doc.addObject('Part::Feature','Model')
wire=Part.makePolygon([App.Vector(0,0,0),App.Vector(60,0,0),App.Vector(0,40,0),App.Vector(0,0,0)])
model.Shape=Part.Face(wire).extrude(App.Vector(0,0,10))
sheet,_,_=pages.create_page(doc)
group=doc.addObject('TechDraw::DrawProjGroup','Group');sheet.addView(group);group.Source=[model]
view=group.addProjection('Front');doc.recompute()
view.Direction=App.Vector(0,0,1);view.XDirection=App.Vector(1,0,0)
group.X=130;group.Y=170;group.Scale=1;doc.recompute()
dim=dimensions.make_dimension(doc,sheet,view,'Angle',['Edge1','Edge2'],x=0,y=0)
doc.recompute()
before=float(dim.getRawValue())
initial=snapshot._dimension_record(dim)
vertex=initial['angle_geometry']['vertex']
origin=snapshot.G.view_origin(view)
dim.X=vertex[0]-origin[0];dim.Y=vertex[1]-origin[1];doc.recompute()
clearance=annotation_layout.clear_collisions(sheet,[dim],[view])
path={str(tmp_path/'angular.FCStd')!r};doc.saveAs(path);App.closeDocument(doc.Name)
doc=App.openDocument(path);doc.recompute()
dim=next(o for o in doc.Objects if o.TypeId=='TechDraw::DrawViewDimension')
record=snapshot._dimension_record(dim)
svg=render._dimension_svg(record,297)
result={{'before':before,'after':float(dim.getRawValue()),'record':record,'clearance':clearance,'svg':svg}}
''')
    assert result['after'] == pytest.approx(result['before'], abs=1e-7)
    assert result['record']['angle_geometry']
    assert len(result['record']['ink_lines']) >= 8
    assert result['clearance']['moves'] > 0
    assert result['clearance']['unresolved'] == []
    assert result['svg'].count('<polygon') == 2


def test_mixed_through_and_blind_openings_do_not_get_a_global_thru_note(tmp_path):
    result=freecad_probe(tmp_path,f'''
from fd_drawing import page as pages, builder, definition_repair, paper_geometry
from free_drawing_mcp.drawing_definition import components, bounds_of, boundary_rectangle
doc=App.newDocument('MixedDepths')
model=doc.addObject('Part::Feature','Model')
model.Shape=Part.makeBox(40,20,10).cut(Part.makeBox(8,8,10,App.Vector(4,6,0))).cut(Part.makeBox(8,8,4,App.Vector(24,6,6)))
sheet,_,_=pages.create_page(doc)
group=doc.addObject('TechDraw::DrawProjGroup','Group');sheet.addView(group);group.Source=[model]
view=group.addProjection('Front');doc.recompute()
view.Direction=App.Vector(0,0,1);view.XDirection=App.Vector(1,0,0);view.HardHidden=False
doc.recompute()
note=builder.add_note(doc,sheet,['UNITS: mm'],40,25)
doc.recompute();doc.saveAs({str(tmp_path/'mixed.FCStd')!r})
geometry=paper_geometry.capture(view)
groups=components(geometry['edges'])
inside=[g for g in groups if boundary_rectangle(g,bounds_of(g)) and bounds_of(g)!=geometry['bounds']]
inside.sort(key=lambda g:bounds_of(g)[0])
assert len(inside)==2
gaps=[{{'code':'ambiguous_opening_depth','opening_kind':'rectangle','view':view.Name,'refs':[e['id'] for e in g]}} for g in inside]
report=definition_repair.repair(doc,sheet,gaps)
result={{'report':report,'notes':list(note.Text)}}
''')
    assert result['report']['changed'] is False
    assert result['notes']==['UNITS: mm']


def test_bossed_envelope_does_not_get_a_false_uniform_thickness_note(tmp_path):
    result=freecad_probe(tmp_path,f'''
from fd_drawing import page as pages, builder, definition_repair
doc=App.newDocument('Boss')
model=doc.addObject('Part::Feature','Model')
model.Shape=Part.makeBox(20,20,5).fuse(Part.makeCylinder(5,5,App.Vector(10,10,5)))
sheet,_,_=pages.create_page(doc)
group=doc.addObject('TechDraw::DrawProjGroup','Group');sheet.addView(group);group.Source=[model]
view=group.addProjection('Front');doc.recompute()
view.Direction=App.Vector(0,0,1);view.XDirection=App.Vector(1,0,0)
doc.recompute();note=builder.add_note(doc,sheet,['UNITS: mm'],40,25)
doc.recompute();doc.saveAs({str(tmp_path/'boss.FCStd')!r})
report=definition_repair.repair(doc,sheet,[{{'code':'missing_thickness','view':view.Name}}])
result={{'report':report,'notes':list(note.Text)}}
''')
    assert not result['report']['changed']
    assert result['notes']==['UNITS: mm']


def test_closed_ellipse_is_exported_as_unsupported_not_discarded(tmp_path):
    result=freecad_probe(tmp_path,'''
from fd_drawing import page as pages,paper_geometry
doc=App.newDocument('Ellipse')
model=doc.addObject('Part::Feature','Model')
ellipse=Part.Ellipse();ellipse.MajorRadius=10;ellipse.MinorRadius=5
model.Shape=Part.Face(Part.Wire([ellipse.toShape()])).extrude(App.Vector(0,0,4))
sheet,_,_=pages.create_page(doc)
view=doc.addObject('TechDraw::DrawViewPart','View');sheet.addView(view);view.Source=[model]
view.Direction=App.Vector(0,0,1);view.XDirection=App.Vector(1,0,0)
doc.recompute();result=paper_geometry.capture(view)
''')
    assert any(e['kind']=='unsupported' for e in result['edges'])


def test_blind_and_intersecting_holes_keep_real_depths(tmp_path):
    result=freecad_probe(tmp_path,'''
from fd_drawing.analysis import cylinders
from fd_drawing.features import hole_stacks
v=App.Vector
blind=Part.makeBox(20,20,20).cut(Part.makeCylinder(2,15,v(10,10,5))).cut(Part.makeCylinder(4,4,v(10,10,16)))
opposite=Part.makeBox(20,20,30).cut(Part.makeCylinder(2,5,v(10,10,0))).cut(Part.makeCylinder(2,7,v(10,10,23)))
manifold=Part.makeBox(80,55,40)
for x in (18,40,62):
    manifold=manifold.cut(Part.makeCylinder(4,29,v(x,18,40),v(0,0,-1))).cut(Part.makeCylinder(7,6,v(x,18,40),v(0,0,-1)))
manifold=manifold.cut(Part.makeCylinder(4,70,v(5,18,14),v(1,0,0)))
result={name:hole_stacks(shape,cylinders(shape)) for name,shape in [('blind',blind),('opposite',opposite),('manifold',manifold)]}
''')
    assert len(result['blind'])==1 and result['blind'][0]['depth']==pytest.approx(15)
    assert result['blind'][0]['cbore']==[8,4]
    assert not result['blind'][0]['through']
    assert len(result['opposite'])==2
    assert sorted(h['depth'] for h in result['opposite'])==pytest.approx([5,7])
    assert not any(h['through'] for h in result['opposite'])
    vertical=[h for h in result['manifold'] if h['axis']=='Z']
    assert len(vertical)==3 and all(h['depth']==pytest.approx(29) and not h['through'] for h in vertical)


def test_outer_chamfer_is_not_misidentified_as_a_hole_countersink(tmp_path):
    result=freecad_probe(tmp_path,'''
from fd_drawing.analysis import cylinders
from fd_drawing.features import hole_stacks
v=App.Vector
body=Part.makeCylinder(10,10)
edges=[e for e in body.Edges if type(e.Curve).__name__=='Circle' and abs(e.Curve.Center.z-10)<1e-6]
body=body.makeChamfer(1,edges).cut(Part.makeCylinder(2,10))
result=hole_stacks(body,cylinders(body))
''')
    assert len(result)==1 and result[0]['through']
    assert result[0]['csk'] is None


def test_countersinks_at_both_ends_do_not_turn_a_through_hole_into_a_blind_one(tmp_path):
    result=freecad_probe(tmp_path,'''
from fd_drawing.analysis import cylinders
from fd_drawing.features import hole_stacks,hole_text
v=App.Vector
shape=Part.makeBox(20,20,10).cut(Part.makeCylinder(2,10,v(10,10,0)))
shape=shape.cut(Part.makeCone(3,2,1,v(10,10,0))).cut(Part.makeCone(2,3,1,v(10,10,9)))
holes=hole_stacks(shape,cylinders(shape))
result={'holes':holes,'text':hole_text(holes[0],1)}
''')
    assert len(result['holes'])==1
    assert result['holes'][0]['through'] and result['holes'][0]['depth']==pytest.approx(10)
    assert result['holes'][0]['csk_both_ends']
    assert 'THRU' in result['text'] and 'BOTH SIDES' in result['text']

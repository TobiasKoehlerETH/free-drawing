"""Printed facts, not measurable paper geometry, control independent rebuilding."""
import copy

import pytest

from free_drawing_mcp.drawing_definition import interpret


def plate_evidence():
    corners=[[100,100],[124,100],[124,120],[100,120]]
    view={'id':'front','kind':'ortho','role':'Front','frame':[[1,0,0],[0,1,0],[0,0,1]],'scale':2,
          'bounds':[100,100,124,120], 'edges':[{'id':f'e{i}','kind':'line','ends':[corners[i],corners[(i+1)%4]]} for i in range(4)],
          'circles':[{'id':'hole','ids':['hole'],'center':[108,110],'radius':2,'full':True,'construction':False}]}
    def dim(name,kind,text,points,refs=()):
        return {'id':name,'view':'front','kind':kind,'text':text,'witnesses':points,'refs':list(refs)}
    return {'schema':1,'units':'mm','projection':'first','views':[view], 'notes':['UNITS: mm','t3'],
            'dimensions':[dim('width','DistanceX','12',[[100,100],[124,100]]),
                          dim('height','DistanceY','10',[[100,100],[100,120]]),
                          dim('hole-x','DistanceX','4',[[100,100],[108,110]]),
                          dim('hole-y','DistanceY','5',[[100,100],[108,110]]),
                          dim('hole-size','Diameter','⌀2 THRU',[],['hole'])]}


def test_explicit_constraints_define_plate_and_hole():
    result=interpret(plate_evidence())
    assert result['status']=='planned'
    assert (result['plan']['width'],result['plan']['height'],result['plan']['thickness'])==(12,10,3)
    assert result['plan']['holes'][0]['center']==[4,5]
    assert result['plan']['holes'][0]['diameter']==2
    assert result['plan']['holes'][0]['depth']=='through'


@pytest.mark.parametrize('missing,code',[('width','missing_location'),('hole-x','missing_location'),('hole-size','missing_diameter')])
def test_removed_dimension_is_not_recovered_from_precise_drawing_coordinates(missing,code):
    packet=plate_evidence()
    packet['dimensions']=[d for d in packet['dimensions'] if d['id']!=missing]
    result=interpret(packet)
    assert result['status']=='missing'
    assert any(g['code']==code for g in result['gaps'])


def test_missing_thickness_does_not_get_a_default():
    packet=plate_evidence();packet['notes']=['UNITS: mm']
    assert any(g['code']=='missing_thickness' for g in interpret(packet)['gaps'])


def test_printed_number_controls_plan_even_when_outline_is_unchanged():
    packet=plate_evidence();packet['dimensions'][0]['text']='13'
    assert interpret(packet)['plan']['width']==13


@pytest.mark.parametrize('field',['model','meta','source','source_shape','raw_value'])
def test_source_metadata_is_not_an_accepted_input(field):
    packet=plate_evidence();packet[field]={'width':999}
    with pytest.raises(ValueError,match='forbidden'):interpret(packet)


def test_hidden_radius_field_is_rejected():
    packet=plate_evidence();packet['views'][0]['circles'][0]['model_radius']=123
    with pytest.raises(ValueError,match='forbidden'):interpret(packet)


def test_conflicting_numbers_do_not_form_a_solved_plan():
    packet=plate_evidence();duplicate=copy.deepcopy(packet['dimensions'][0]);duplicate.update(id='other-width',text='13')
    packet['dimensions'].append(duplicate)
    assert interpret(packet)['status']=='conflict'


def test_removing_thru_makes_depth_unknown():
    packet=plate_evidence();packet['dimensions'][-1]['text']='⌀2'
    assert any(g['code']=='missing_hole_depth' for g in interpret(packet)['gaps'])


def test_disconnected_control_cannot_be_claimed_as_consumed():
    packet=plate_evidence()
    packet['dimensions'].append({'id':'floating','view':'front','kind':'DistanceX','text':'999','refs':[],
                                 'witnesses':[[500,500],[550,500]]})
    assert interpret(packet)['status']!='planned'


def test_extra_unsupported_view_cannot_be_ignored():
    packet=plate_evidence();extra=copy.deepcopy(packet['views'][0])
    extra.update(id='rear',role='Rear',edges=[{'id':'spline','kind':'unsupported','ends':[[100,100]]}],circles=[])
    packet['views'].append(extra)
    assert interpret(packet)['status']=='unsupported'


@pytest.mark.parametrize('suffix',[' BLIND DEPTH 1',' UNKNOWN'])
def test_partial_hole_callout_is_not_accepted(suffix):
    packet=plate_evidence();packet['dimensions'][-1]['text']+=suffix
    assert interpret(packet)['status']=='unsupported'


def test_geometric_notes_cannot_be_silently_ignored():
    packet=plate_evidence();packet['notes'].append('ALL HOLES BLIND; DEPTH 1')
    assert interpret(packet)['status']=='unsupported'


def test_local_thickness_note_is_not_treated_as_global():
    packet=plate_evidence();packet['notes'][-1]={'id':'local','scope':'view:other','text':'t3'}
    assert interpret(packet)['status']=='unsupported'


def test_nearby_coordinate_is_not_an_unprinted_equality():
    packet=plate_evidence()
    packet['views'][0]['circles'][0]['center'][0]=100.04
    packet['dimensions'][3]['witnesses'][1][0]=100.04
    packet['dimensions']=[d for d in packet['dimensions'] if d['id']!='hole-x']
    result=interpret(packet)
    assert result['status']=='missing'
    assert any(g['code']=='missing_location' for g in result['gaps'])


def test_counterbore_depth_cannot_supply_missing_primary_hole_depth():
    packet=plate_evidence()
    packet['dimensions'][-1]['text']='⌀2 ⌴⌀4 ↧1'
    packet['views'][0]['circles'].append({'id':'ring','ids':['ring'],'center':[108,110], 'radius':4,'full':True,'construction':False})
    assert any(g['code']=='missing_hole_depth' for g in interpret(packet)['gaps'])


def shaft_evidence():
    points=[[0,-4],[1,-5],[19,-5],[20,-4],[20,4],[19,5],[1,5],[0,4]]
    view={'id':'front','kind':'ortho','role':'Front','scale':1,'frame':[[1,0,0],[0,1,0],[0,0,1]],
          'bounds':[0,-5,20,5],'circles':[],
          'edges':[{'id':f'e{i}','kind':'line','ends':[p,points[(i+1)%8]]} for i,p in enumerate(points)]}
    dims=[{'id':name,'view':'front','kind':kind,'text':text,'refs':[],'witnesses':ends} for name,kind,text,ends in [
        ('length','DistanceX','20',[[0,4],[20,4]]),('diameter','DistanceY','⌀10',[[1,-5],[1,5]]),
        ('chamfer','DistanceX','2× C1',[[0,4],[1,5]])]]
    return {'schema':1,'units':'mm','projection':'first','views':[view],'dimensions':dims,'notes':['UNITS: mm']}


def test_revolved_path_cannot_ignore_a_contradictory_radial_control():
    packet=shaft_evidence()
    assert interpret(packet)['status']=='planned'
    packet['dimensions'].append({'id':'bogus','view':'front','kind':'DistanceY','text':'123456','refs':[],
                                  'witnesses':[[1,-5],[1,5]]})
    assert interpret(packet)['status']!='planned'


def test_chamfer_count_is_a_real_constraint():
    packet=shaft_evidence();packet['dimensions'][-1]['text']='3× C1'
    assert interpret(packet)['status']=='unsupported'

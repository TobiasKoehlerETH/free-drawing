"""Regenerate and independently audit all twelve local and six online drawing cases.

    uv run python samples/audit_reconstruction.py --out samples/validation/reconstruction-audit
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import sys

from validate_view_policy import CASES, ROOT, check_policy, code_hashes, write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--engine',type=Path,default=ROOT)
    parser.add_argument('--jobs',type=int,default=2)
    parser.add_argument('--names',nargs='+')
    args=parser.parse_args()
    if args.out.exists() and any(args.out.iterdir()):parser.error('Use a new or empty output directory')
    sources=[(name,ROOT/'samples'/folder/(name+'.step')) for name,folder,_ in CASES]
    for name in ['part_0001','part_0013','part_0026','part_0039','part_0052','part_0065']:
        source=ROOT/'.scratch/drawing-references/cadsy/samples'/name/'model.step'
        if not source.is_file():parser.error('Fetch the pinned reference dataset first with samples/reference_dataset.py fetch')
        sources.append(('cadsy_'+name,source))
    if args.names:
        unknown=set(args.names)-{name for name,_ in sources}
        if unknown:parser.error('Unknown cases: '+', '.join(sorted(unknown)))
        sources=[row for row in sources if row[0] in args.names]
    if not sources or args.jobs<1:parser.error('Select at least one known case and a positive job count')
    engine=args.engine.resolve();sys.path.insert(0,str(engine/'src'))
    output=args.out.resolve();output.mkdir(parents=True,exist_ok=True)
    before=code_hashes(engine)
    results=[]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending=[pool.submit(audit,name,source,output) for name,source in sources]
        for future in as_completed(pending):
            row=future.result();results.append(row);results.sort(key=lambda r:r['part'])
            write_json(output/'results.json',{'engine':str(engine),'code_sha256':before,
                       'engine_unchanged':before==code_hashes(engine),'cases':results})
            print(row['part']+': '+row['status']+'; attempts='+str(row.get('attempts',0)),flush=True)
    return int(any(r['status']=='error' for r in results))


def audit(name,source,output):
    from free_drawing_mcp.backends import HeadlessBackend
    from free_drawing_mcp.definition import verify_definition
    from free_drawing_mcp.rules import inspect
    folder=output/name;folder.mkdir()
    drawing=folder/(name+'.FCStd')
    row={'part':name,'source':str(source),'drawing':str(drawing)}
    try:
        backend=HeadlessBackend(timeout=300)
        created=backend.run([{'op':'create_drawing','args':{'source':str(source),'output':str(drawing),'title':name}}])[0]
        report=verify_definition(backend,drawing,source,folder/'definition',max_repairs=2)
        snap=backend.run([{'op':'snapshot','args':{'doc':str(drawing)}},
                          {'op':'export','args':{'doc':str(drawing),'format':'pdf','path':str(folder/(name+'.pdf'))}},
                          {'op':'render','args':{'doc':str(drawing),'width':1800,'out_path':str(folder/(name+'.png'))}}])[0]
        write_json(folder/'snapshot.json',snap)
        inspection=inspect(snap)
        write_json(folder/'inspection.json',inspection.to_dict(include_pass=True))
        row.update(status=report['status'],attempts=len(report['attempts']),view_checks=check_policy(snap),
                   source_sha256=report['source_sha256'],source_unchanged=report['source_unchanged'],
                   dimensions=len(snap['dimensions']),scale=created['scale'],
                   gaps=report['attempts'][-1].get('reconstruction',{}).get('gaps',[]),
                   comparison=report['attempts'][-1].get('comparison'),
                   repairs=[change for attempt in report['attempts'] for change in attempt.get('repair',{}).get('changes',[])],
                   findings=[{'rule':f.rule,'status':f.status,'message':f.message} for f in inspection.findings
                             if f.status in ('fail','warn') and f.rule not in ('F.1','K.1')])
    except Exception as error:
        row.update(status='error',error=f'{type(error).__name__}: {error}')
    write_json(folder/'result.json',row)
    return row


if __name__=='__main__':raise SystemExit(main())

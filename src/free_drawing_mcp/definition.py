"""A bounded drawing -> independent solid -> source comparison feedback loop."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from .backends import ADDON_DIR, BackendCancelled, HeadlessBackend, detect_freecadcmd
from .drawing_definition import SCOPE


def verify_definition(backend, drawing, source, output_dir, max_repairs=2, tolerance=1e-5, cancel_event=None):
    if not 0 <= max_repairs <= 3:
        raise ValueError('max_repairs must be between 0 and 3')
    if not 1e-5 <= tolerance <= .01:
        raise ValueError('Drawing verification supports numerical tolerances from 0.00001 to 0.01 mm')
    output = Path(output_dir).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a new or empty verification output directory')
    output.mkdir(parents=True, exist_ok=True)
    drawing, source = str(Path(drawing).resolve()), str(Path(source).resolve())
    initial_source_hash = sha256(Path(source))
    attempts, seen, seen_gaps = [], set(), set()
    exported=None
    report = {'status':'unverified','drawing':drawing,'source':source,'source_sha256':initial_source_hash,
              'scope':SCOPE,'attempts':attempts}
    for index in range(max_repairs + 1):
        if cancel_event is not None and cancel_event.is_set(): raise BackendCancelled('Reconstruction cancelled')
        directory = output / f'attempt-{index + 1}'
        directory.mkdir()
        evidence, frame = directory / 'drawing.json', directory / 'comparison-frame.json'
        # Separate calls/processes: reconstruction never opens the source-bearing drawing.
        try:
            exported = backend.run([{'op':'definition_evidence','args':{'doc':drawing,'path':str(evidence),'frame_path':str(frame)}}])[0]
        except BackendCancelled: raise
        except Exception as error:
            report.update(status='error',stop_reason='evidence export failed: '+str(error))
            break
        attempt = {'number':index + 1,'evidence_sha256':exported['sha256']}
        attempts.append(attempt)
        if exported['sha256'] in seen:
            report.update(status='stalled',stop_reason='drawing evidence did not change')
            break
        seen.add(exported['sha256'])
        try:
            built = reconstruct_evidence(evidence, directory / 'reconstruction',cancel_event=cancel_event,tolerance=tolerance)
        except BackendCancelled: raise
        except Exception as error:
            report.update(status='error',stop_reason='reconstruction failed: '+str(error))
            attempt['reconstruction']={'status':'error','error':str(error)}
            break
        attempt['reconstruction'] = built
        if built['status'] == 'rebuilt':
            try:
                comparison = backend.run([{'op':'compare_definition','args':{
                    'source':source,'rebuilt':built['step'],'frame_path':str(frame),'tolerance':tolerance}}])[0]
            except BackendCancelled: raise
            except Exception as error:
                comparison={'status':'inconclusive','error':str(error)}
            attempt['comparison'] = comparison
            report.update(status=comparison['status'],stop_reason='independent geometry comparison completed')
            break
        report.update(status=built['status'],stop_reason='reconstruction needs definition or unsupported geometry')
        if built['status'] != 'missing' or index == max_repairs:
            break
        gap_key=json.dumps(built['gaps'],sort_keys=True)
        if gap_key in seen_gaps:
            report['stop_reason']='same definition gaps remain; no further duplicate repairs'
            break
        seen_gaps.add(gap_key)
        try:
            repair = backend.run([{'op':'repair_definition','args':{'doc':drawing,'gaps':built['gaps']}}])[0]
        except BackendCancelled: raise
        except Exception as error:
            report.update(status='error',stop_reason='visible repair failed: '+str(error))
            break
        attempt['repair'] = repair
        if not repair['changed']:
            report['stop_reason'] = 'no supported visible repair'
            break
    report['source_unchanged'] = initial_source_hash == sha256(Path(source))
    report['final_dimension_count']=exported['dimensions'] if exported else None
    if not report['source_unchanged']:
        report.update(status='stale',stop_reason='input STEP changed during verification')
    if report['status']=='match_within_scope':
        try:
            final=backend.run([{'op':'definition_evidence','args':{'doc':drawing,'path':str(output/'final-drawing.json'),
                           'frame_path':str(output/'final-comparison-frame.json')}}])[0]
            if final['sha256']!=attempts[-1]['evidence_sha256']:
                report.update(status='stale',stop_reason='drawing evidence changed after reconstruction')
        except BackendCancelled: raise
        except Exception as error:
            report.update(status='error',stop_reason='final drawing freshness check failed: '+str(error))
    report['drawing_sha256'] = sha256(Path(drawing))
    (output / 'verification.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    return report


def reconstruct_evidence(evidence, output_dir, timeout=120, cancel_event=None, tolerance=1e-5):
    """Launch a worker whose job contains only the drawing packet and output paths."""
    executable = detect_freecadcmd()
    if executable is None: raise RuntimeError('FreeCADCmd is required for independent reconstruction')
    output = Path(output_dir).resolve()
    output.mkdir(parents=True,exist_ok=True)
    result = output / 'result.json'
    job = output / 'job.json'
    if not 1e-5<=tolerance<=.01:raise ValueError('Unsupported numerical reconstruction tolerance')
    job.write_text(json.dumps({'evidence':str(Path(evidence).resolve()),'output':str(output),'result':str(result),'tolerance':tolerance}),encoding='utf-8')
    worker = ADDON_DIR / 'fd_drawing' / 'rebuild_worker.py'
    environment = dict(os.environ, FD_REBUILD_JOB=str(job), QT_QPA_PLATFORM='offscreen')
    command = executable + ['-c',f"exec(compile(open({str(worker)!r},encoding='utf-8').read(),{str(worker)!r},'exec'),{{'__name__':'__main__','__file__':{str(worker)!r}}})"]
    started=time.monotonic()
    proc=subprocess.Popen(command,env=environment,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set(): raise BackendCancelled('Reconstruction cancelled')
            if time.monotonic()-started>timeout: raise TimeoutError('Independent reconstruction exceeded its time limit')
            try:
                _,stderr=proc.communicate(timeout=.2)
                break
            except subprocess.TimeoutExpired: pass
    finally:
        if proc.poll() is None: proc.kill()
        proc.communicate()
    if proc.returncode or not result.is_file():
        raise RuntimeError(f'Independent reconstruction worker failed: {stderr[-1000:]}')
    return json.loads(result.read_text(encoding='utf-8'))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--drawing',required=True)
    parser.add_argument('--source',required=True)
    parser.add_argument('--out',required=True)
    parser.add_argument('--max-repairs',type=int,default=2)
    args=parser.parse_args()
    report=verify_definition(HeadlessBackend(timeout=300),args.drawing,args.source,args.out,max_repairs=args.max_repairs)
    print(json.dumps(report,indent=2,ensure_ascii=True))
    return int(report['status']!='match_within_scope')


if __name__=='__main__':
    raise SystemExit(main())

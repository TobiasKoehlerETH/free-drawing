"""Generate machining regression parts and inspect the reopened drawing exports.

    uv run python samples/audit_production.py --out samples/validation/production-audit
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from pathlib import Path
import subprocess
import sys

from validate_view_policy import ROOT, code_hashes, run_case, write_json, write_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=2)
    args = parser.parse_args()
    output = args.out.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error('Choose a new or empty output directory')
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    sys.path.insert(0, str(ROOT / 'src'))
    from free_drawing_mcp.backends import detect_freecadcmd

    command = detect_freecadcmd()
    if not command:
        parser.error('FreeCADCmd is required')
    sources = output / 'sources'
    sources.mkdir(parents=True)
    generate = f"import sys; sys.path.insert(0, {str(ROOT / 'samples')!r}); from generate_production_parts import main; main({str(sources)!r})"
    generated = subprocess.run(command + ['-c', generate], capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=120,
                               env=dict(os.environ, QT_QPA_PLATFORM='offscreen'))
    if generated.returncode or len(list(sources.glob('*.step'))) != 5:
        raise RuntimeError('Production part generation failed: ' + generated.stdout + generated.stderr)

    cases = [(path.stem, str(sources), 'machining feature regression') for path in sorted(sources.glob('*.step'))]
    cases += [(name, 'parts', name) for name in (
        'plate_hole_pattern', 'flange', 'counterbored_block', 'turned_shaft', 'slotted_plate', 'threaded_shaft')]
    cases.append(('rotated_manifold', 'complex', 'intersecting passages'))
    before = code_hashes(ROOT)
    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending = [pool.submit(run_case, case, output) for case in cases]
        for future in as_completed(pending):
            row = future.result()
            results.append(row)
            results.sort(key=lambda item: item['part'])
            write_json(output / 'results.json', {
                'engine': str(ROOT), 'code_sha256': before,
                'engine_unchanged': before == code_hashes(ROOT), 'cases': results})
            print(f"{row['part']}: {row['status']}; policy={row.get('policy_passed')}; unplaced={row.get('unplaced')}", flush=True)
    write_report(results, output)
    return int(any(row['status'] != 'ok' or not row.get('policy_passed')
                   or row.get('unplaced') or row.get('layout_findings') for row in results))


if __name__ == '__main__':
    raise SystemExit(main())

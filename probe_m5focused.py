"""M5 focused census: status-let byte_at probes in small batches.

Targets still-failing census rows whose body contains `byte_at` in a
`let status<int>` shape (or bare status-let byte_at generally), and
re-runs them through the current backend in batches of 5.
Run: python -S -B probe_m5focused.py  (repo root)
"""
import sys
sys.path.insert(0, '.')
sys.path.insert(0, 'tools')
import json
import glob
import re
import census

if __name__ == '__main__':
    rows = {}
    for f in sorted(glob.glob('censusA*.jsonl')):
        for l in open(f):
            l = l.strip()
            if l:
                r = json.loads(l)
                rows[(r['sub'], r['name'])] = r
    known = set(census.sig_table())
    fns = {(s, n): src for s, n, src in census.split_functions()}
    targets = []
    for (sub, name), r in sorted(rows.items()):
        if r['code'] == 0:
            continue
        src = fns[(sub, name)]
        body = src.split('{', 1)[1] if '{' in src else ''
        if 'byte_at' not in body:
            continue
        prog, base = census.probe_program(sub, name, src, known)
        targets.append((sub, name, prog, r['code']))
    print('byte_at still-failing rows:', len(targets))
    now0 = 0
    still = []
    for i in range(0, len(targets), 5):
        grp = targets[i:i + 5]
        try:
            results = census.run_batch([t[2] for t in grp])
        except AssertionError as e:
            print('  batch %d BATCH-FAIL: %s' % (i // 5 + 1, str(e)[:200]))
            for (sub, name, _p, was) in grp:
                still.append((sub, name, was, 'batch', -1))
            continue
        for (sub, name, _p, was), (code, off) in zip(grp, results):
            if code == 0:
                now0 += 1
            else:
                still.append((sub, name, was, code, off))
        print('  batch %d/%d green-now=%d' % (i // 5 + 1, (len(targets) + 4) // 5, now0))
    print('NOW-COMPILABLE:', now0, '/', len(targets))
    print('STILL-BLOCKED:', len(still))
    for s, n, was, code, off in still[:30]:
        print('  ', s, n, 'was=%s now=(%s,%s)' % (was, code, off))

"""Full post-G3 census analysis -> ranked blocker tables.

Usage: python -S -B tools/census_analyze.py census0.jsonl [...]
"""
import json
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, '.')
sys.path.insert(0, 'tools')
import census

RECW = {'D': 3, 'Tok': 4, 'BZ': 4, 'H8': 8, 'AR': 7, 'SR': 5, 'FR': 4,
        'TI': 8, 'VS': 9, 'TR': 29}


def words_of(ty):
    ty = ty.strip()
    if ty in ('int', 'bool'):
        return 1
    if ty == 'str':
        return 2
    m = re.match(r'list\s*<\s*(.+)\s*,\s*(\d+)\s*>$', ty)
    if m:
        return 1 + int(m.group(2)) * words_of(m.group(1))
    m = re.match(r'status\s*<\s*(.+)\s*>$', ty)
    if m:
        return 2 + words_of(m.group(1))
    return RECW.get(ty.split('<', 1)[0], 99)


def split_top(s):
    depth = 0
    parts = []
    cur = ''
    for ch in s:
        if ch in '<([':
            depth += 1
        elif ch in '>)]':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(cur)
            cur = ''
        else:
            cur += ch
    parts.append(cur)
    return parts


def gate_issues(src):
    m = re.match(r'(?m)^fn (\w+)\(([^)]*)\)(\s*:\s*[^\{]+)?\{', src)
    params, ret = m.group(2), (m.group(3) or ': int').lstrip(': ').strip()
    out = []
    w = 0
    np = 0
    for p in split_top(params):
        p = p.strip()
        if not p or ':' not in p:
            continue
        np += 1
        ty = p.split(':', 1)[1].strip()
        b = ty.split('<', 1)[0]
        if b == 'bool':
            out.append('bool-param')
        w += words_of(ty)
    if np > 12:
        out.append('nparams>12')
    if w > 12:
        out.append('words>12')
    rb = ret.split('<', 1)[0]
    if rb == 'bool':
        out.append('bool-ret')
    if rb == 'str':
        out.append('str-ret')
    return out


def body_label(src, name, off):
    if off < 0 or off >= len(src):
        return 'harness-stub'
    ls = src.rfind('\n', 0, off) + 1
    le = src.find('\n', off)
    line = src[ls:le if le != -1 else len(src)]
    s = line.strip()
    if re.match(r'\s*fn\b', line):
        return 'gate'
    if 'byte_at' in s:
        return 'byte_at'
    if 'str_len' in s and 'be_str_len' not in s:
        return 'str_len'
    if 'unwrap_or' in s:
        return 'unwrap_or'
    if 'print(' in s:
        return 'print'
    if re.search(r'\blen\s*\(', s):
        return 'len-str'
    if re.search(r'\b' + re.escape(name) + r'\s*\(', s):
        return 'self-call'
    if re.search(r'\b\w+\s*\([^)]*\)\s*\[', s):
        return 'call-index'
    if re.search(r'(?<![\w.])[A-Z]\w*\s*\(', s):
        return 'ctor'
    if '->' in s:
        return 'field'
    if re.match(r'\s*return\b', line):
        return 'return-shape'
    if re.match(r'\s*(if|while)\b', line):
        return 'control'
    if re.match(r'\s*let\b', line):
        return 'let-rhs'
    return 'other'


def main():
    paths = sys.argv[1:]
    rows = []
    for p in paths:
        with open(p, encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    fns = census.split_functions()
    known = set(census.sig_table())
    srcmap = {(s, n): src for s, n, src in fns}
    print('rows: %d (universe %d)' % (len(rows), len(fns)))
    have = set((r['sub'], r['name']) for r in rows)
    missing = [(s, n) for s, n, _src in fns if (s, n) not in have]
    print('missing: %d %s' % (len(missing), missing[:12]))
    # dedupe (reruns): last wins
    bykey = {}
    for r in rows:
        bykey[(r['sub'], r['name'])] = r
    rows = list(bykey.values())
    print('dedup rows: %d' % len(rows))
    nok = sum(1 for r in rows if r['code'] == 0)
    print('COMPILABLE: %d  BLOCKED: %d' % (nok, len(rows) - nok))
    first = Counter()
    ex = defaultdict(list)
    beim = {}
    for r in rows:
        if r['code'] == 0:
            first[('ok', '')] += 1
            continue
        if r['code'] == -1:
            first[('infra', r['note'][:24])] += 1
            continue
        src = srcmap[(r['sub'], r['name'])]
        prog, base = census.probe_program(r['sub'], r['name'], src, known)
        lab = body_label(src, r['name'], r['off'] - base)
        first[(r['code'], lab)] += 1
        ex[(r['code'], lab)].append('%s %s' % (r['sub'], r['name']))
        beim[(r['sub'], r['name'])] = lab
    print('--- first-blocker ranking ---')
    for k, v in first.most_common(30):
        print('  %5d %-22s e.g. %s' % (v, k, (ex.get(k) or [''])[0]))
    # static gate sub-causes for gate-labeled
    gates = Counter()
    for (code, lab), names in ex.items():
        if code == 25 and lab == 'gate':
            for nm in names:
                sub, fn = nm.split(' ', 1)
                for g in gate_issues(srcmap[(sub, fn)]):
                    gates[g] += 1
    print('--- gate-static sub-causes ---')
    for g, v in gates.most_common():
        print('  %-14s %d' % (g, v))
    # stub-dependency classes for harness-stub rows (static: failing stub sig)
    deps = Counter()
    depex = defaultdict(list)
    for r in rows:
        src = srcmap.get((r['sub'], r['name']), '')
        if not src:
            continue
        prog, base = census.probe_program(r['sub'], r['name'], src, known)
        if r['code'] in (25, 11, 10) and (r['off'] - base < 0):
            # find failing stub span
            callees, _ = census.referenced_names(src, r['name'], known)
            pre = census.record_decls()
            hit = None
            for c in callees:
                s0 = len(pre)
                stub = census.stub_for(c) + '\n'
                if s0 <= r['off'] < s0 + len(stub):
                    params, ret = census.sig_table()[c]
                    gg = tuple(gate_issues('fn %s(%s)%s {}' % (c, params, ret)))
                    hit = (c, gg)
                    break
                pre += stub
            key = ('stub', hit[0] if hit else '?', hit[1] if hit else ())
            deps[key] += 1
            depex[key].append('%s %s' % (r['sub'], r['name']))
    print('--- stub-dependency classes (top) ---')
    for k, v in deps.most_common(15):
        print('  %5d %s e.g. %s' % (v, k, depex[k][0]))
    # arg-cap static
    big = sum(1 for r in rows if r['code'] != 0 and r['args'] > 12)
    print('blocked rows with args>12 (nparams): %d' % big)


if __name__ == '__main__':
    main()

"""Analytic remap of census rows to final verdicts (no baby reruns).

Old batches used first-generation stubs ([] list literals, byte_at status
stubs). New stubs (exact-count literals, index/push-var status idioms) fix
pure stub artifacts. This script remaps each row to a FINAL verdict:

  ok                              target compiled in probe
  gate:<classes>                  target signature gate-blocked (static)
  self-call                       target fails at own recursive call
  byte_at / len-str / ...         target fails at body construct
  dependent:<class>(<stub>)        env stub uncompilable; target unjudged
  infra                           driver/baby infrastructure failure

Stub verdicts come from direct single-stub probes (probe_stubs2 results)
plus static gate analysis, NOT guesswork.
"""
import json
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, '.')
sys.path.insert(0, 'tools')
import census

# Directly measured single-stub probe results (code, off-region):
# 0,0 = compiles; 25@start = gate; 25@body/11@body = body feature.
STUB_VERDICT = {
    # compiles with new stubs:
    'tscal': ('ok',), 'pgm_brace_end': ('ok',), 'be_callee_ret': ('ok',),
    'pgm_body_ret': ('ok',), 'be_fn_ret': ('ok',),
    # gate-blocked signatures (static, deterministic):
    'beq': ('gate', 'bool-ret'), 'has_err': ('gate', 'bool-ret'),
    'pgm_is_cbrace': ('gate', 'bool-ret'), 'pgm_is_semi': ('gate', 'bool-ret'),
    'pgm_is_obrace': ('gate', 'bool-ret'), 'pgm_is_close': ('gate', 'bool-ret'),
    'pgm_is_lt': ('gate', 'bool-ret'), 'pgm_mid': ('gate', 'bool-ret'),
    'is_alpha': ('gate', 'bool-ret'), 'is_alnum': ('gate', 'bool-ret'),
    'path_seg_bad': ('gate', 'bool-ret'),
    'infer_var_ty': ('gate', 'words>12'),
    'check_binop': ('gate', 'words>12'),
    'arm_after_pat': ('gate', 'nparams>12+words>12'),
    'arm_binds': ('gate', 'nparams>12+words>12'),
    'tcons': ('gate', 'words>12'), 'tsub': ('gate', 'words>12'),
    'tbase': ('gate', 'words>12'), 'tcat': ('gate', 'words>12'),
    'tsubcat': ('gate', 'words>12'), 'tsublist': ('gate', 'words>12'),
    'be_e_aggex': ('gate', 'words>12'), 'be_s_aggex': ('gate', 'words>12'),
    # body-feature-blocked stubs (measured 25@body with new stubs):
    'intern_ty': ('body', 'record-list-ctor'),
    'x_or': ('body', 'record-list-ctor'),
    'pgm_hparam_ty': ('body', 'record-list-ctor'),
}

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


def gate_issues_of(sig_params, sig_ret):
    out = []
    w = 0
    np = 0
    for p in split_top(sig_params):
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
    rb = sig_ret.split('<', 1)[0]
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
    files = sys.argv[1:]
    rows = []
    for p in files:
        with open(p, encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    fns = census.split_functions()
    known = set(census.sig_table())
    srcmap = {(s, n): src for s, n, src in fns}
    tab = census.sig_table()
    bykey = {}
    for r in rows:
        bykey[(r['sub'], r['name'])] = r
    rows = list(bykey.values())
    print('rows: %d' % len(rows))
    final = Counter()
    ex = defaultdict(list)
    # per-fn static gate issues (for gate-labeled rows)
    for r in rows:
        key = (r['sub'], r['name'])
        src = srcmap[key]
        if r['code'] == 0:
            final[('ok',)] += 1
            continue
        if r['code'] == -1:
            final[('infra', r['note'][:20])] += 1
            continue
        if r['code'] == 26:
            final[('cap-26-frame', '')] += 1
            continue
        if r['code'] == 10:
            # checker codes: need host diagnostic mapping; keep raw
            final[('check10', body_label(src, r['name'], 0))] += 1
            ex[('check10', body_label(src, r['name'], 0))].append('%s %s off=%d' % (r['sub'], r['name'], r['off']))
            continue
        prog, base = census.probe_program(r['sub'], r['name'], src, known)
        rel = r['off'] - base
        if rel < 0 or rel >= len(src):
            # stub region: attribute failing stub
            callees, _ = census.referenced_names(src, r['name'], known)
            pre = census.record_decls()
            hit = None
            for c in callees:
                s0 = len(pre)
                stub = census.stub_for(c) + '\n'
                if s0 <= r['off'] < s0 + len(stub):
                    hit = c
                    break
                pre += stub
            if hit is None:
                final[('harness-records', '')] += 1
                continue
            v = STUB_VERDICT.get(hit)
            if v is None:
                # static gate fallback for unmeasured stubs
                params, ret = tab[hit]
                g = gate_issues_of(params, ret.lstrip(': ').strip())
                if g:
                    final[('dependent:gate', '%s(%s)' % (hit, '+'.join(g)))] += 1
                else:
                    final[('dependent:stub-unmeasured', hit)] += 1
                ex[('dependent:stub-unmeasured', hit)].append('%s %s' % (r['sub'], r['name']))
            elif v[0] == 'ok':
                final[('dependent:stub-now-ok-RERUN-NEEDED', hit)] += 1
                ex[('dependent:stub-now-ok-RERUN-NEEDED', hit)].append('%s %s' % (r['sub'], r['name']))
            elif v[0] == 'gate':
                final[('dependent:gate-' + v[1], hit)] += 1
            else:
                final[('dependent:' + v[1], hit)] += 1
            continue
        lab = body_label(src, r['name'], rel)
        if lab == 'gate':
            m = re.match(r'(?m)^fn (\w+)\(([^)]*)\)(\s*:\s*[^\{]+)?\{', src)
            # NOTE: ([^)]*) breaks on list<..,..> params; use split_top on full head
            head = src.split('{', 1)[0]
            inner = head.split('(', 1)[1].rsplit(')', 1)[0]
            ret = head.rsplit(')', 1)[1].lstrip(': ').strip() or 'int'
            g = gate_issues_of(inner, ret)
            final[('gate:' + '+'.join(g), '')] += 1
            ex[('gate:' + '+'.join(g), '')].append('%s %s' % (r['sub'], r['name']))
        elif lab == 'harness-stub':
            final[('harness-?', '')] += 1
        else:
            final[(r['code'], lab)] += 1
            ex[(r['code'], lab)].append('%s %s' % (r['sub'], r['name']))
    print('--- FINAL ranking ---')
    for k, v in final.most_common(60):
        print('  %5d %-44s e.g. %s' % (v, k, (ex.get(k) or [''])[0]))
    print('--- dependent detail ---')
    for k in sorted(ex, key=repr):
        v = ex[k]
        if isinstance(k, tuple) and k and str(k[0]).startswith('dependent'):
            print('  %s x%d: %s' % (k, len(v), v[:6]))


if __name__ == '__main__':
    main()

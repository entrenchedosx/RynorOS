"""Census batch worker: probe functions [lo:hi) and append JSON results.

Usage: python -S -B tools/census_batch.py <lo> <hi> <out.jsonl>
Each line: {"sub":..,"name":..,"args":..,"code":..,"off":..,"note":..}
Batches of ~28; runtime ~5-10 min per batch.
"""
import json
import re
import sys
import time

sys.path.insert(0, '.')
sys.path.insert(0, 'tools')
import census
from tests.repository import test_rynorlang_selfhost_emit as bea
from tools.rynorlang import analyze as analyzer
from tools.rynorlang import rir
from tools.rynorlang import interp as oracle

PER = 7  # driver probes per run (keeps baby runtime manageable)


def classify_at(fn_src, off):
    """Describe the source construct at fn-relative offset off.

    Returns a short taxonomy label + snippet. off < 0 means the error is
    outside the target (stub/main) -> 'harness'.
    """
    if off is None or off < 0 or off >= len(fn_src):
        return 'harness|off=%s' % (off,)
    # walk back to line start
    ls = fn_src.rfind('\n', 0, off) + 1
    le = fn_src.find('\n', off)
    line = fn_src[ls:le if le != -1 else len(fn_src)]
    tok = fn_src[off:off + 12]
    s = line.strip()
    if re.match(r'\s*fn\b', line):
        return 'gate|%s' % s[:80]
    if 'byte_at' in s:
        return 'byte_at|%s' % s[:80]
    if 'str_len' in s:
        return 'str_len|%s' % s[:80]
    if 'unwrap_or' in s:
        return 'unwrap_or|%s' % s[:80]
    if 'print(' in s:
        return 'print|%s' % s[:80]
    if re.search(r'\blen\s*\(', s):
        return 'len-str|%s' % s[:80]
    # record/list construction or call-result projection
    if re.search(r'\b[A-Z]\w*\s*\(', s):
        return 'ctor|%s' % s[:80]
    if '->' in s:
        return 'field|%s' % s[:80]
    if re.search(r'\b\w+\s*\([^)]*\)\s*\[', s):
        return 'call-index|%s' % s[:80]
    if re.match(r'\s*return\b', line):
        return 'return-shape|%s' % s[:80]
    if re.match(r'\s*(if|while)\b', line):
        return 'control|%s' % s[:80]
    if re.match(r'\s*let\b', line):
        return 'let-rhs|%s' % s[:80]
    return 'other|tok=%s|line=%s' % (tok, s[:60])


GATE_RE = re.compile(r'(?m)^fn (\w+)\(([^)]*)\)(\s*:\s*[^\{]+)?\{')


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


def gate_class(fn_src):
    """Static gate prediction: signature-level reject reasons (backend 25/26).

    Returns list of gate blocker labels (may be empty).
    """
    m = GATE_RE.match(fn_src)
    if not m:
        return ['gate-parse']
    params, ret = m.group(2), (m.group(3) or ': int').lstrip(': ').strip()
    out = []
    # word count: int/bool=1, str=2, list=N?, record=?, status=?
    words = 0
    nparams = 0
    for p in split_top(params):
        p = p.strip()
        if not p or ':' not in p:
            continue
        nparams += 1
        ty = p.split(':', 1)[1].strip()
        b = ty.split('<', 1)[0].split('[', 1)[0]
        if b == 'bool':
            out.append('gate-bool-param')
        if b == 'str':
            words += 2
        elif b in ('int', 'bool'):
            words += 1
        else:
            words += 3  # aggregate-ish placeholder
    if nparams > 12:
        out.append('gate-nparams>12')
    if words > 12:
        out.append('gate-words>12')
    rb = ret.split('<', 1)[0].split('[', 1)[0]
    if rb == 'bool':
        out.append('gate-bool-ret')
    if rb == 'str':
        out.append('gate-str-ret')
    return out


def run_group(progs):
    drv = ['fn be_probe(src: str, f: int): int {',
           '  let d: D = be_main(src, f);',
           '  print("|");',
           '  print(d->c);',
           '  print(",");',
           '  print(d->o);',
           '  print(";");',
           '  return 0;',
           '}',
           'fn main(): int {']
    for prog in progs:
        drv.append('  be_probe("%s", 0);' % bea._esc(prog))
    drv.append('  return 0;')
    drv.append('}')
    full = bea._combo_text() + '\n'.join(drv) + '\n'
    result = analyzer.analyze(full, 'census.rl', profile='core')
    if not result.ok:
        return [('DRIVER-REJECT', str(result.diagnostic))] * len(progs)
    module, error = rir.build_rir(result.ast, 'census.rl')
    if error is not None:
        return [('DRIVER-RIR', str(error))] * len(progs)
    if rir.verify_module(module):
        return [('DRIVER-VERIFY', '')] * len(progs)
    emitted = []
    try:
        outcome = oracle.run_rir(module, out=emitted, step_limit=500000000)
    except Exception as ex:  # noqa: BLE001 - record host-side failure
        return [('HOST-EXC', repr(ex))] * len(progs)
    if outcome['trapped'] is not None or outcome['exit'] != 0:
        return [('BABY-TRAP', repr(outcome))] * len(progs)
    chunks = ''.join(emitted).split(';')
    if len(chunks) != len(progs) + 1:
        return [('CHUNK-MISMATCH', '%d!=%d' % (len(chunks), len(progs)))] * len(progs)
    out = []
    for chunk in chunks[:len(progs)]:
        _hex, _, tail = chunk.rpartition('|')
        code, _, off = tail.partition(',')
        try:
            out.append((int(code), int(off), _surround(_hex)))
        except ValueError:
            out.append(('BAD-TAIL', tail[-40:]))
    return out


def _surround(_hex):
    return ''


def main():
    lo, hi, outpath = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    fns = census.split_functions()
    known = set(census.sig_table())
    items = fns[lo:hi]
    rows = []
    t0 = time.time()
    for g in range(0, len(items), PER):
        grp = items[g:g + PER]
        built = [census.probe_program(s, n, src, known) for s, n, src in grp]
        progs = [b[0] for b in built]
        bases = [b[1] for b in built]
        try:
            res = run_group(progs)
        except Exception as ex:  # noqa: BLE001
            res = [('HOST-EXC2', repr(ex))] * len(grp)
        for (sub, name, src), base, r in zip(grp, bases, res):
            if isinstance(r, tuple) and isinstance(r[0], int) and len(r) == 3:
                code, off, _ctx = r
                note = ''
            elif isinstance(r, tuple) and isinstance(r[0], int):
                code, off = r
                note = ''
            else:
                code, off, note = -1, -1, '%s:%s' % (r[0], r[1])
            rows.append({'sub': sub, 'name': name, 'args': census.arg_words(src),
                         'code': code, 'off': off, 'note': note,
                         'at': classify_at(src, off - base) if code else '',
                         'gate': gate_class(src) if code == 25 and (off - base) <= 0 else []})
        print('  group %d/%d done %.0fs' % (g // PER + 1, (len(items) + PER - 1) // PER, time.time() - t0), flush=True)
    with open(outpath, 'a', encoding='utf-8') as fh:
        for row in rows:
            fh.write(json.dumps(row) + '\n')
    print('wrote %d rows %.0fs' % (len(rows), time.time() - t0), flush=True)


if __name__ == '__main__':
    main()

"""Post-G3 whole-function backend census tooling.

For every function in the self-compiler bundle (util/lex/check/prog/emit),
build a probe program (fn source + trivial main) and run it through the
baby backend `be_main` under the HOST oracle. Record per function:
  compilable yes/no, backend code, error offset, arg words, subsystem.

Blocker taxonomy is derived from (code, source-at-offset) pairs.
This file is census TOOLING (host-side Python); it changes no product code.
"""
import re
import sys

sys.path.insert(0, '.')
from tests.repository import test_rynorlang_selfhost_emit as bea
from tools.rynorlang import analyze as analyzer
from tools.rynorlang import rir
from tools.rynorlang import interp as oracle

FILES = ('util.rl', 'lex.rl', 'check.rl', 'prog.rl', 'emit.rl')
MAIN = '\nfn main(): int { return 0; }\n'


def split_functions():
    """Return list of (subsystem, name, source) for every fn in the bundle."""
    out = []
    for fname in FILES:
        with open('rynorlang/selfhost/%s' % fname, encoding='utf-8') as fh:
            text = fh.read()
        # core style: fn starts at line start; body braces balanced on lines.
        # Find 'fn NAME' at line starts, slice to next such line.
        marks = [(m.start(), m.group(1)) for m in re.finditer(r'(?m)^fn (\w+)', text)]
        for i, (off, name) in enumerate(marks):
            end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
            out.append((fname[:-3], name, text[off:end]))
    return out


# Name -> (params, ret, record-decl?) for stub generation, built once.
SIG_CACHE = []


def sig_table():
    if SIG_CACHE:
        return SIG_CACHE[0]
    tab = {}
    for fname in FILES:
        with open('rynorlang/selfhost/%s' % fname, encoding='utf-8') as fh:
            text = fh.read()
        for m in re.finditer(r'(?m)^fn (\w+)\(([^)]*)\)(\s*:\s*[^\{]+)?\{', text):
            tab[m.group(1)] = (m.group(2), (m.group(3) or '').strip())
    SIG_CACHE.append(tab)
    return tab


RE_CHECKS = [
    'byte_at', 'unwrap_or', 'len', 'push', 'print', 'is_ok', 'is_err',
    'str_len',
]


def referenced_names(fn_src, name, known):
    body = fn_src.split('{', 1)[1] if '{' in fn_src else ''
    toks = set(re.findall(r'\b([A-Za-z_]\w*)\s*\(', body))
    toks.discard(name)
    out = sorted(t for t in toks if t in known and t not in RE_CHECKS)
    # record constructors: RecName( used but never declared as fn
    recs = sorted(t for t in toks if t not in known and t not in RE_CHECKS
                  and re.match(r'^[A-Z]', t))
    return out, recs


def stub_for(callee):
    params, ret = sig_table()[callee]
    body = _dummy_ret(ret, params)
    if body == 'STATUSIDX':
        return ('fn %s(%s)%s { let cl: list<int,1> = [7]; return cl[0]; }'
                % (callee, params, ret))
    if body == 'STATUSBOOL':
        return ('fn %s(%s)%s { let cl: list<bool,1> = [true]; return cl[0]; }'
                % (callee, params, ret))
    if body == 'STATUSPUSH':
        inner = ret[len('status<'):-1].strip()
        return ('fn %s(%s)%s { let cl: %s = %s; let cs: %s = push(cl, %s); return cs; }'
                % (callee, params, ret, inner, _zero_list(inner),
                   ret, _zero_elem(inner)))
    return 'fn %s(%s)%s { return %s; }' % (callee, params, ret, body)


def _zero_list(ty):
    m = re.match(r'list\s*<\s*(.+)\s*,\s*(\d+)\s*>$', ty.strip())
    if m:
        cap = int(m.group(2))
        elem = m.group(1).strip()
        if elem == 'int':
            return '[%s]' % ','.join(['0'] * cap)
        if elem == 'bool':
            return '[%s]' % ','.join(['false'] * cap)
    return '[]'


def _zero_elem(ty):
    m = re.match(r'list\s*<\s*(.+)\s*,', ty.strip())
    elem = m.group(1).strip() if m else 'int'
    return '0' if elem == 'int' else 'false'


def record_decls():
    decls = []
    for fname in FILES:
        with open('rynorlang/selfhost/%s' % fname, encoding='utf-8') as fh:
            text = fh.read()
        for m in re.finditer(r'(?m)^record\s+\w+\s*\{[^}]*\}', text):
            decls.append(m.group(0))
    return '\n'.join(decls) + '\n'


def forward_decls():
    """All record decls for the whole bundle (tiny, always included)."""
    return record_decls()


def _dummy_ret(ret, params=''):
    r = ret.lstrip(': ').strip()
    if r.startswith('bool'):
        return 'false'
    if r.startswith('str'):
        return '"x"'
    if r.startswith('status<int>'):
        return 'STATUSIDX'
    if r.startswith('status<bool>'):
        return 'STATUSBOOL'
    if r.startswith('status<'):
        return 'STATUSPUSH'
    if re.match(r'^(D|Tok|BZ|TR|SR|FR|TI|VS|H8|AR)\b', r):
        return '%s(%s)' % (r.split('<', 1)[0].split('[', 1)[0], _dummy_fields(r))
    if r.startswith('list'):
        m = re.match(r'list\s*<\s*(.+)\s*,\s*(\d+)\s*>$', r)
        if m:
            cap = int(m.group(2))
            elem = m.group(1).strip()
            if elem == 'int':
                return '[%s]' % ','.join(['0'] * cap)
            if elem == 'bool':
                return '[%s]' % ','.join(['false'] * cap)
        # fall back to same-typed param passthrough
        for p in params.split(','):
            p = p.strip()
            if ':' in p and p.split(':', 1)[1].strip() == r:
                return p.split(':', 1)[0].strip()
        return '[]'
    # same-typed param passthrough for other types
    for p in params.split(','):
        p = p.strip()
        if ':' in p and p.split(':', 1)[1].strip() == r:
            return p.split(':', 1)[0].strip()
    return '0'


def _dummy_fields(rec):
    base = rec.split('<', 1)[0].split('[', 1)[0]
    shapes = {
        'D': 'c: 0, f: 0, o: 0',
        'Tok': 'k: 0, s: 0, l: 0, p: 0',
        'BZ': 'p: 0, n: 0, c: 0, o: 0',
        'TR': 't: [%s], p: 0, d: D(c: 0, f: 0, o: 0)' % ','.join(['0'] * 24),
        'SR': 'p: 0, d: D(c: 0, f: 0, o: 0)',
        'FR': 'i: 0, d: D(c: 0, f: 0, o: 0)',
        'TI': 'k: 0, s: 0, l: 0, p: 0, d: D(c: 0, f: 0, o: 0)',
        'VS': 'off: 0, k: 0, ts: 0, tl: 0, slot: 0, d: D(c: 0, f: 0, o: 0)',
        'H8': 'a: 0, b: 0, c: 0, d: 0, e: 0, f: 0, g: 0, h: 0',
        'AR': 'p: 0, d: D(c: 0, f: 0, o: 0), a0: 0, a1: 0, a4: 0, a5: 0, a6: 0',
    }
    return shapes.get(base, '0')


def arg_words(fn_src):
    """Count declared params (rough: idents with : before first { or =)."""
    head = fn_src.split('{', 1)[0] if '{' in fn_src else fn_src
    # params are name: type pairs inside (...)
    inner = head.split('(', 1)[1].rsplit(')', 1)[0] if '(' in head else ''
    inner = inner.strip()
    if not inner:
        return 0
    # split top-level commas (types contain commas inside list<..> / <>)
    depth = 0
    parts = []
    cur = ''
    for ch in inner:
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
    return sum(1 for p in parts if ':' in p)


def run_batch(progs):
    """Run be_main on many probe programs in ONE driver run.

    Returns list of (code, off) aligned with progs. Amortizes the
    analyze/RIR fixed cost (~5s) across the batch.
    """
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
    for i, prog in enumerate(progs):
        drv.append('  be_probe("%s", 0);' % bea._esc(prog))
    drv.append('  return 0;')
    drv.append('}')
    full = bea._combo_text() + '\n'.join(drv) + '\n'
    result = analyzer.analyze(full, 'census.rl', profile='core')
    assert result.ok, result.diagnostic
    module, error = rir.build_rir(result.ast, 'census.rl')
    assert error is None, error
    assert not rir.verify_module(module)
    emitted = []
    outcome = oracle.run_rir(module, out=emitted, step_limit=500000000)
    assert outcome['trapped'] is None and outcome['exit'] == 0, outcome
    chunks = ''.join(emitted).split(';')
    assert len(chunks) == len(progs) + 1, (len(chunks), len(progs))
    out = []
    for chunk in chunks[:len(progs)]:
        hexstr, _, tail = chunk.rpartition('|')
        code, _, off = tail.partition(',')
        for ch in hexstr:
            assert ch in '0123456789abcdef', chunk[-60:]
        out.append((int(code), int(off)))
    return out


def run_probe(combo_unused, prog):
    """Run be_main(prog) under host oracle; return (code, off)."""
    return run_batch([prog])[0]


def probe_program(sub, name, fn_src, known=None):
    """Probe program: records + stubs for referenced callees + target + main.

    Returns (program, target_offset): byte offset where the target fn starts
    inside the program, so backend error offsets convert to fn-relative.
    """
    if known is None:
        known = set(sig_table())
    callees, _recs = referenced_names(fn_src, name, known)
    pre = record_decls()
    for c in callees:
        pre += stub_for(c) + '\n'
    return pre + '\n' + fn_src + MAIN, len(pre) + 1


def main():
    import json
    fns = split_functions()
    print('total functions: %d' % len(fns))
    from collections import Counter
    print(Counter(s for s, _n, _src in fns))


if __name__ == '__main__':
    main()

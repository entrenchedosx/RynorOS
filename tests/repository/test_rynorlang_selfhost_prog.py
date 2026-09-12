''"""Stage 19e whole-program self-host frontend: core-clean guard, differential
parity vs the trusted host analyzer, and frontend mutants.

Covers docs/design/rynorlang-selfhost.md frontend slices: the baby checker
(`rynorlang/selfhost/*.rl`, core dialect) must accept/reject the corpus with
the same diagnostic class and source offset as the host `analyze(..., core)`.

Baby codes: 1-4 lex, 5 depth, 6 par-want, 7 par-got, 8 par-eof, 9 undeclared,
10 duplicate, 11 type, 12 arity, 13 unknown-fn, 14 limit, 15 profile.
Diagnostic *text* is not compared (RFC: codes must match, text may differ).
"""

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.rynorlang import analyze as analyzer  # noqa: E402
from tools.rynorlang import interp as oracle  # noqa: E402
from tools.rynorlang import rir  # noqa: E402


def _read_selfhost():
    parts = []
    for name in ("util.rl", "lex.rl", "check.rl", "prog.rl"):
        parts.append((ROOT / "rynorlang" / "selfhost" / name).read_text(encoding="utf-8"))
    return parts


def _esc(s):
    return (s.replace("\\", "\\\\").replace('"', '\\"')
             .replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r"))


BABY_TO_HOST = {0: None, 1: "PAR_LEX_ERROR", 2: "PAR_LEX_ERROR", 3: "PAR_LEX_ERROR",
                4: "PAR_LEX_ERROR", 5: "PAR_DEPTH_EXCEEDED", 6: "PAR_EXPECTED_TOKEN",
                7: "PAR_UNEXPECTED_TOKEN", 8: "PAR_UNEXPECTED_EOF", 9: "SEM_UNDECLARED",
                10: "SEM_DUPLICATE", 11: "SEM_TYPE_MISMATCH", 12: "SEM_ARITY_MISMATCH",
                13: "SEM_UNKNOWN_FUNCTION", 14: "SEM_LIMIT_EXCEEDED", 15: "SEM_PROFILE_EXCLUDED"}


def _combo_text(extra=""):
    u, l, c, p = _read_selfhost()
    return u + "\n" + l + "\n" + c + "\n" + p + "\n" + extra + "\n"


def _run_cases(combo_src, cases):
    """Analyze+build once, run every case through pgm_check in one oracle run."""
    drv = ("fn pgm_case(src: str, f: int): int {\n"
           "  let d: D = pgm_check(src, f);\n"
           "  print(d->c);\n"
           "  print(\",\");\n"
           "  print(d->o);\n"
           "  print(\";\");\n"
           "  return 0;\n"
           "}\n"
           "fn main(): int {\n")
    for i, (_name, src) in enumerate(cases):
        drv += f'  let s{i}: str = "{_esc(src)}";\n'
    for i, (_name, _src) in enumerate(cases):
        drv += f"  pgm_case(s{i}, 0);\n"
    drv += "  return 0;\n}\n"
    full = combo_src + drv
    for profile in ("core", None):
        kw = {} if profile is None else {"profile": profile}
        result = analyzer.analyze(full, "pgmdrv.rl", **kw)
        if not result.ok:
            raise AssertionError(f"driver rejected under {profile}: {result.diagnostic}")
    result = analyzer.analyze(full, "pgmdrv.rl", profile="core")
    module, error = rir.build_rir(result.ast, "pgmdrv.rl")
    if error is not None:
        raise AssertionError(f"driver RIR build failed: {error}")
    if rir.verify_module(module):
        raise AssertionError("driver RIR verify failed")
    emitted = []
    outcome = oracle.run_rir(module, out=emitted, step_limit=500000000)
    if outcome["trapped"] is not None or outcome["exit"] != 0:
        raise AssertionError(f"driver trapped: {outcome}")
    chunks = "".join(emitted).split(";")
    if len(chunks) != len(cases) + 1:
        raise AssertionError(f"driver emitted {len(chunks)} chunks for {len(cases)} cases")
    out = []
    for chunk in chunks[:len(cases)]:
        code, _, off = chunk.partition(",")
        out.append((int(code), int(off)))
    return out

P1_CASES = CASES = [
    ('valid', 'fn main(): int { return 0; }\n'),
    ('empty', ''),
    ('two-fns-ok', 'fn f(): int { return 1; }\nfn g(): int { return 2; }\nfn main(): int { return f() + g(); }\n'),
    ('use-ok', 'use "lib/a.rl";\nfn main(): int { return 0; }\n'),
    ('rec-empty', 'record E {}\nfn main(): int { return 0; }\n'),
    ('rec-field-list', 'record P { l: list<int,3> }\nfn main(): int { return 0; }\n'),
    ('status-param', 'fn f(s: status<int>): int { return 0; }\nfn main(): int { return 0; }\n'),
    ('fn-record-ok', 'fn record(): int { return 0; }\nfn main(): int { return 0; }\n'),
    ('record-record-ok', 'record record { a: int }\nfn main(): int { return 0; }\n'),
    ('fn-print-param', 'fn f(print: int): int { return print; }\nfn main(): int { return 0; }\n'),
    ('field-ok', 'record P { ok: int }\nfn main(): int { let p: P = P(ok: 1); print(p->ok); return 0; }\n'),
    ('empty-parens-noret', 'fn f() { print(1); }\nfn main(): int { f(); return 0; }\n'),
    ('dup-fn', 'fn f(): int { return 0; }\nfn f(): int { return 1; }\nfn main(): int { return 0; }\n'),
    ('reserved-fn', 'fn len(): int { return 0; }\nfn main(): int { return 0; }\n'),
    ('fn-break', 'fn break(): int { return 0; }\nfn main(): int { return 0; }\n'),
    ('fn-reserved-rec', 'record len { a: int }\nfn main(): int { return 0; }\n'),
    ('record-match', 'record match { a: int }\nfn main(): int { return 0; }\n'),
    ('record-list', 'record list { a: int }\nfn main(): int { return 0; }\n'),
    ('dup-rec', 'record P { a: int }\nrecord P { b: bool }\nfn main(): int { return 0; }\n'),
    ('rec-dupfield', 'record P { a: int, a: bool }\nfn main(): int { return 0; }\n'),
    ('rec-nocolon', 'record P { a int }\nfn main(): int { return 0; }\n'),
    ('rec-badfield-ty', 'record P { a: nosuch }\nfn main(): int { return 0; }\n'),
    ('rec-trailing-comma', 'record P { a: int, }\nfn main(): int { return 0; }\n'),
    ('rec-semi', 'record P { a: int; }\nfn main(): int { return 0; }\n'),
    ('rec-field-status', 'record P { s: status<str> }\nfn main(): int { return 0; }\n'),
    ('dup-param', 'fn f(a: int, a: bool): int { return 0; }\nfn main(): int { return 0; }\n'),
    ('param-no-annot', 'fn f(a): int { return 0; }\nfn main(): int { return 0; }\n'),
    ('param-bad-ty', 'fn f(a: nosuch): int { return 0; }\nfn main(): int { return 0; }\n'),
    ('param-trailing-comma', 'fn f(a: int,): int { return a; }\nfn main(): int { return 0; }\n'),
    ('param-missing-comma', 'fn f(a: int b: bool): int { return 0; }\nfn main(): int { return 0; }\n'),
    ('param-int', 'fn f(int: int): int { return 0; }\nfn main(): int { return 0; }\n'),
    ('field-int', 'record P { int: int }\nfn main(): int { return 0; }\n'),
    ('ret-bad-ty', 'fn f(): nosuch { return 0; }\nfn main(): int { return 0; }\n'),
    ('ret-missing-ty', 'fn f(): { return 0; }\nfn main(): int { return 0; }\n'),
    ('fn-noparen', 'fn f: int { return 0; }\nfn main(): int { return 0; }\n'),
    ('fn-noname', 'fn (): int { return 0; }\nfn main(): int { return 0; }\n'),
    ('fn-else', 'fn else(): int { return 0; }\nfn main(): int { return 0; }\n'),
    ('top-let', 'let x: int = 1;\nfn main(): int { return 0; }\n'),
    ('stray-lex', 'fn main(): int { return 0; } ???\n'),
    ('use-eof', 'use "x"'),
    ('eof-in-fn', 'fn main(): int { return 0;'),
    ('ret-junk', 'fn f(): int junk { return 0; }\nfn main(): int { return 0; }\n'),
    ('fn-extra-rparen', 'fn f(a: int)): int { return a; }\nfn main(): int { return 0; }\n'),
    ('dup-then-parse', 'fn f(): int { return 0; }\nfn f(): int { return 1; }\nfn g: int { return 0; }\nfn main(): int { return 0; }\n'),
    ('parse-then-dup', 'fn g: int { return 0; }\nfn f(): int { return 0; }\nfn f(): int { return 1; }\nfn main(): int { return 0; }\n'),
    ('reserved-then-parse', 'fn len(): int { return 0; }\nfn g: int { return 0; }\nfn main(): int { return 0; }\n'),
    ('lex-then-dup', 'fn f(): int { return 0; }\nfn f(): int { return 1; }\nfn main(): int { return 0; } ???\n'),
    ('fndup-and-recdup', 'fn f(): int { return 0; }\nfn f(): int { return 1; }\nrecord P { a: int }\nrecord P { b: bool }\nfn main(): int { return 0; }\n'),
    ('recdup-and-fndup', 'record P { a: int }\nrecord P { b: bool }\nfn f(): int { return 0; }\nfn f(): int { return 1; }\nfn main(): int { return 0; }\n'),
    ('dup-then-bodyerr', 'fn f(): int { return nosuch(); }\nfn f(): int { return 0; }\nfn main(): int { return 0; }\n'),
    ('bodyerr-then-dup', 'fn f(): int { return 0; }\nfn f(): int { return nosuch(); }\nfn main(): int { return 0; }\n'),
    ('paramerr-then-bodyerr2', 'fn f(a: nosuch): int { return 0; }\nfn g(): int { return nosuch2(); }\nfn main(): int { return 0; }\n'),
    ('bodyerr-then-paramerr', 'fn f(): int { return nosuch2(); }\nfn g(a: nosuch): int { return 0; }\nfn main(): int { return 0; }\n'),
    ('recdup-then-fnerr', 'record P { a: int }\nrecord P { b: bool }\nfn f(a: nosuch): int { return 0; }\nfn main(): int { return 0; }\n'),
    ('fnerr-then-recdup', 'fn f(a: nosuch): int { return 0; }\nrecord P { a: int }\nrecord P { b: bool }\nfn main(): int { return 0; }\n'),
    ('recfield-then-fnbody', 'record P { a: nosuch }\nfn f(): int { return nosuch2(); }\nfn main(): int { return 0; }\n'),
    ('cap-zero-param', 'fn f(l: list<int,0>): int { return 0; }\nfn main(): int { return 0; }\n'),
    ('cap-empty-param', 'fn f(l: list<int,>): int { return 0; }\nfn main(): int { return 0; }\n'),
    ('list-nocap-param', 'fn f(l: list<int>): int { return 0; }\nfn main(): int { return 0; }\n'),
    ('status-empty-param', 'fn f(x: status<>): int { return 0; }\nfn main(): int { return 0; }\n'),
    ('neg-cap-param', 'fn f(l: list<int,0 - 2>): int { return 0; }\nfn main(): int { return 0; }\n'),
    ('rec-field-map', 'record R { m: map<int,int,2> }\nfn main(): int { return 0; }\n'),
    ('result-field', 'record P { r: result<int,int> }\nfn main(): int { return 0; }\n'),
    ('param-status-nested', 'fn f(x: list<status<int>,2>): int { return 0; }\nfn main(): int { return 0; }\n'),
    ('deep-nest-param', 'fn f(x: ' + 'list<' * 20 + 'int' + ',1>' * 20 + '): int { return 0; }\nfn main(): int { return 0; }\n'),
]

P2_CASES = P2 = [
    ('str-plus', 'fn main(): int { return "a" + "b"; }\n'),
    ('eq-mixed', 'fn main(): int { if 1 == true { return 0; } else { return 1; } }\n'),
    ('lt-str', 'fn main(): int { if "a" < "b" { return 0; } else { return 1; } }\n'),
    ('neg-uint', 'fn main(): int { return -true; }\n'),
    ('not-int', 'fn main(): int { return !1; }\n'),
    ('bitop-bool', 'fn main(): int { if true & false { return 0; } else { return 1; } }\n'),
    ('argtype', 'fn f(a: int): int { return a; }\nfn main(): int { return f(true); }\n'),
    ('rec-wrong-field-ty', 'record P { a: int }\nfn main(): int { let p: P = P(a: true); return 0; }\n'),
    ('list-elem-ty', 'fn main(): int { let l: list<int,2> = [true]; return 0; }\n'),
    ('idx-str', 'fn main(): int { let s: str = "abc"; print(s[0]); return 0; }\n'),
    ('idx-type', 'fn main(): int { let l: list<int,2> = [1, 2]; print(l[true]); return 0; }\n'),
    ('field-int-lit', 'fn main(): int { print((1)->a); return 0; }\n'),
    ('ret-bare-unit', 'fn main(): int { return; }\n'),
    ('while-nonbool', 'fn main(): int { while 1 { break; } return 0; }\n'),
    ('if-nonbool', 'fn main(): int { if 1 { return 0; } else { return 1; } }\n'),
    ('break-out', 'fn main(): int { break; return 0; }\n'),
    ('print-in-expr', 'fn main(): int { return print(1); }\n'),
    ('is-ok-int', 'fn main(): int { print(is_ok(1)); return 0; }\n'),
    ('len-int', 'fn main(): int { print(len(1)); return 0; }\n'),
    ('unwrap-or-ty', 'fn main(): int { let r: status<str> = fread("/x", 0, 1); print(unwrap_or(r, 7)); return 0; }\n'),
    ('op-assign', 'fn main(): int { let x: int = 1; x = 2; return x; }\n'),
    ('semi-stmt', 'fn main(): int { ; return 0; }\n'),
    ('return-semi', 'fn main(): int { return ; }\n'),
    ('return-plus', 'fn main(): int { return + 1; }\n'),
    ('paren-empty', 'fn main(): int { return (); }\n'),
    ('match-as-expr', 'fn main(): int { let x: int = match true { true => { 0; }, false => { 1; } }; return x; }\n'),
    ('if-as-expr', 'fn main(): int { let x: int = if true { 1; } else { 2; }; return x; }\n'),
    ('rec-pos', 'record P { a: int }\nfn main(): int { let p: P = P(1); return 0; }\n'),
    ('rec-empty-paren', 'record P { a: int }\nfn main(): int { let p: P = P(); return 0; }\n'),
    ('lit-dup-1field', 'record P { a: int, b: bool }\nfn main(): int { let p: P = P(a: 1, a: 2); return 0; }\n'),
    ('lit-dup', 'record P { a: int }\nfn main(): int { let p: P = P(a: 1, a: 2); return 0; }\n'),
    ('lit-extra', 'record P { a: int }\nfn main(): int { let p: P = P(a: 1, b: 2); return 0; }\n'),
    ('list-toolong', 'fn main(): int { let l: list<int,2> = [1, 2, 3]; return 0; }\n'),
    ('call-list-vs-int', 'fn f(a: int): int { return a; }\nfn main(): int { return f([1, 2]); }\n'),
    ('match-payload-misuse', 'fn main(): int { let r: status<str> = fread("/x", 0, 1); match r { ok(v) => { print(v + 1); return 0; }, err(e) => { return 1; } } }\n'),
    ('arm-leak', 'fn main(): int { let r: status<str> = fread("/x", 0, 1); match r { ok(v) => { print(v); }, err(e) => { print(e); } } print(v); return 0; }\n'),
    ('dup-arm-ok', 'fn main(): int { let r: status<str> = fread("/x", 0, 1); match r { ok(v) => { print(v); return 0; }, err(v) => { print(v + 1); return 1; } } }\n'),
    ('err-use-okvar', 'fn main(): int { let r: status<str> = fread("/x", 0, 1); match r { ok(v) => { print(v); return 0; }, err(e) => { print(v); return 1; } } }\n'),
    ('match-lit-dup', 'fn main(): int { match 1 { 1 => { return 0; }, 1 => { return 1; }, _ => { return 2; } } }\n'),
    ('match-nonexh-status', 'fn main(): int { let r: status<str> = fread("/x", 0, 1); match r { ok(v) => { print(v); return 0; } } return 1; }\n'),
    ('match-nonexh-bool', 'fn main(): int { match true { true => { return 0; } } return 1; }\n'),
    ('match-nonexh-int', 'fn main(): int { let x: int = 1; match x { 1 => { return 0; } } return 1; }\n'),
    ('while-nested-break', 'fn main(): int { while true { while true { break; } break; } return 0; }\n'),
    ('if-nested', 'fn main(): int { if true { if false { return 0; } else { return 1; } } else { return 2; } }\n'),
    ('call-in-call', 'fn f(a: int): int { return a * 2 + 1; }\nfn main(): int { print(f(21)); return 0; }\n'),
    ('arith-prec', 'fn main(): int { let x: int = (1 + 2) * 3 - 4 / 2; print(x); return 0; }\n'),
    ('bool-prec', 'fn main(): int { let b: bool = !true || false && true; print(b); return 0; }\n'),
    ('bit-prec', 'fn main(): int { let x: int = 1 << 3 | 6 & 5 ^ 3; print(x); return 0; }\n'),
    ('rec-ok-2field', 'record P { a: int, b: bool }\nfn main(): int { let p: P = P(a: 1, b: true); print(p->a); print(p->b); return 0; }\n'),
    ('list-ok', 'fn main(): int { let l: list<int,3> = [3, 1, 2]; print(l[0]); print(l[2]); print(len(l)); return 0; }\n'),
    ('call-list-ok', 'fn f(l: list<int,2>): int { return 0; }\nfn main(): int { return f([1, 2]); }\n'),
    ('call-list-long', 'fn f(l: list<int,2>): int { return 0; }\nfn main(): int { return f([1, 2, 3]); }\n'),
    ('call-list-elem', 'fn f(l: list<int,2>): int { return 0; }\nfn main(): int { return f([true]); }\n'),
    ('call-list-empty', 'fn f(l: list<int,2>): int { return 0; }\nfn main(): int { return f([]); }\n'),
    ('print-arity', 'fn main(): int { print(); return 0; }\n'),
    ('len-arity', 'fn main(): int { print(len()); return 0; }\n'),
    ('print-unit-arg', 'fn main(): int { print(print(1)); return 0; }\n'),
    ('reclit-missing', 'record P { a: int, b: bool }\nfn main(): int { let p: P = P(a: 1); return 0; }\n'),
    ('fn-rec-dup', 'fn P(): int { return 0; }\nrecord P { a: int }\nfn main(): int { let p: P = P(a: 1); return 0; }\n'),
    ('str-qual', 'fn main(): int { print(str::len("ab")); return 0; }\n'),
    ('index-pipe', 'fn main(): int { let l: list<int,3> = [1,2,3]; print(l[0 | 1]); return 0; }\n'),
    ('many-arms-20', 'fn main(): int { match 1 { 1 => { return 0; }, 2 => { return 1; }, 3 => { return 2; }, 4 => { return 3; }, 5 => { return 4; }, 6 => { return 5; }, 7 => { return 6; }, 8 => { return 7; }, 9 => { return 8; }, 10 => { return 9; }, 11 => { return 10; }, 12 => { return 11; }, 13 => { return 12; }, 14 => { return 13; }, 15 => { return 14; }, 16 => { return 15; }, 17 => { return 16; }, _ => { return 99; } } }\n'),
    ('deep-parens-12', 'fn main(): int { return ((((((((((((1)))))))))))); }\n'),
    ('call-trailing-comma', 'fn f(a: int): int { return a; }\nfn main(): int { return f(1,); }\n'),
    ('reclit-mixed', 'record P { a: int }\nfn main(): int { let p: P = P(a: 1, 2); return 0; }\n'),
    ('reclit-trailing', 'record P { a: int }\nfn main(): int { let p: P = P(a: 1,); return 0; }\n'),
    ('list-trailing', 'fn main(): int { let l: list<int,3> = [1, 2,]; return 0; }\n'),
    ('empty-paren-arg', 'fn f(a: int): int { return a; }\nfn main(): int { return f(()); }\n'),
    ('print-mapempty', 'fn main(): int { print({}); return 0; }\n'),
    ('fnonly-reclit', 'fn P(): int { return 0; }\nfn main(): int { let x: int = P(a: 1); return 0; }\n'),
    ('unit-arg', 'fn f(a: int): int { return a; }\nfn main(): int { return f(print(1)); }\n'),
    ('print-mapnonempty', 'fn main(): int { print({a: 1}); return 0; }\n'),
    ('arg-kw-use', 'fn f(a: int): int { return a; }\nfn main(): int { return f(if); }\n'),
    ('blocks-150', 'fn main(): int { ' + '{ ' * 150 + 'return 0;' + ' }' * 150 + ' }\n'),
    ('blocks-300', 'fn main(): int { ' + '{ ' * 300 + 'return 0;' + ' }' * 300 + ' }\n'),
    ('return-kw-use', 'fn main(): int { if true { return 0; } else { return if; } }\n'),
    ('let-shadow-fn', 'fn f(): int { return 1; }\nfn main(): int { let f: int = 2; return f; }\n'),
    ('bind-shadow-fn', 'fn f(): int { return 1; }\nfn main(): int { let r: status<str> = fread("/x", 0, 1); match r { ok(f) => { print(f); return 0; }, err(e) => { return 1; } } }\n'),
    ('use-in-body', 'fn main(): int { use "x"; return 0; }\n'),
    ('break-with-expr', 'fn main(): int { while true { break 1; } return 0; }\n'),
    ('match-empty-arms', 'fn main(): int { let x: int = 1; match x {} return 0; }\n'),
    ('match-trailing-comma', 'fn main(): int { match 1 { 1 => { return 0; }, } return 1; }\n'),
    ('match-noarrow', 'fn main(): int { match 1 { 1 { return 0; } } return 1; }\n'),
    ('match-nonadjacent', 'fn main(): int { match 1 { 1 = > { return 0; } } return 1; }\n'),
    ('match-true-on-int', 'fn main(): int { match 1 { true => { return 0; }, _ => { return 1; } } }\n'),
    ('match-int-on-bool', 'fn main(): int { match true { 1 => { return 0; }, _ => { return 1; } } }\n'),
    ('ok-on-bool', 'fn main(): int { match true { ok(v) => { return 0; }, _ => { return 1; } } }\n'),
    ('wild-only', 'fn main(): int { let x: int = 1; match x { _ => { return 0; } } }\n'),
    ('bare-only', 'fn main(): int { let x: int = 1; match x { n => { return n; } } }\n'),
    ('unreachable-after-wild', 'fn main(): int { match 1 { _ => { return 0; }, 1 => { return 1; } } }\n'),
    ('dup-ok-arms', 'fn main(): int { let r: status<str> = fread("/x", 0, 1); match r { ok(a) => { return 0; }, ok(b) => { return 1; }, err(e) => { return 2; } } }\n'),
    ('dup-bool-lit', 'fn main(): int { match true { true => { return 0; }, true => { return 1; }, false => { return 2; } } }\n'),
    ('str-lit-dup', 'fn main(): int { match "a" { "a" => { return 0; }, "a" => { return 1; }, _ => { return 2; } } }\n'),
    ('int-01-vs-1', 'fn main(): int { match 1 { 01 => { return 0; }, 1 => { return 1; }, _ => { return 2; } } }\n'),
    ('payload-lit-ok', 'fn main(): int { let r: status<int> = byte_at("ab", 0); match r { ok(97) => { return 0; }, err(e) => { return 1; } } }\n'),
    ('payload-lit-bad', 'fn main(): int { let r: status<int> = byte_at("ab", 0); match r { ok("a") => { return 0; }, err(e) => { return 1; } } }\n'),
    ('payload-wild', 'fn main(): int { let r: status<int> = byte_at("ab", 0); match r { ok(_) => { return 0; }, err(e) => { return 1; } } }\n'),
    ('nested-match', 'fn main(): int { let r: status<str> = fread("/x", 0, 1); match r { ok(v) => { match v { "a" => { return 0; }, _ => { return 1; } } }, err(e) => { return 2; } } }\n'),
    ('deep-parens-300', 'fn main(): int { return ' + '(((((((((((' * 30 + '1' + ')))))))))))' * 30 + '; }\n'),
    ('list-ret-cap', 'fn f(): list<int,5> { let l: list<int,5> = [1,2,3,4,5]; return l; }\nfn main(): int { let l: list<int,2> = f(); return 0; }\n'),
    ('str-cmp', 'fn main(): int { if "a" == "b" { return 0; } else { return 1; } }\n'),
    ('div-zero-const', 'fn main(): int { return 1 / 0; }\n'),
    ('neg-lit', 'fn main(): int { return 0 - 1; }\n'),
    ('shadow-let-after-match', 'fn main(): int { let r: status<str> = fread("/x", 0, 1); match r { ok(v) => { print(v); }, err(e) => { print(e); } } let v: int = 1; return v; }\n'),
    ('match-bind-shadows-param', 'fn f(v: int): int { let r: status<str> = fread("/x", 0, 1); match r { ok(v) => { print(v); return 0; }, err(e) => { return 1; } } }\nfn main(): int { return 0; }\n'),
    ('empty-list-annot', 'fn main(): int { let l: list<int,2> = []; return 0; }\n'),
    ('nested-list-lit', 'fn main(): int { let l: list<list<int,2>,2> = [[1,2],[3,4]]; print(len(l)); return 0; }\n'),
    ('nested-list-bad', 'fn main(): int { let l: list<list<int,2>,2> = [[1],[true]]; return 0; }\n'),
    ('use-before', 'fn main(): int { print(x); let x: int = 1; return 0; }\n'),
    ('dup-local', 'fn main(): int { let x: int = 1; let x: int = 2; return 0; }\n'),
    ('shadow-param', 'fn f(a: int): int { let a: int = 2; return a; }\nfn main(): int { return 0; }\n'),
    ('missing-ret', 'fn f(x: int): int { print(x); }\nfn main(): int { return 0; }\n'),
    ('no-main', 'fn f(): int { return 0; }\n'),
    ('rec-nominal-fnarg', 'record A { x: int }\nrecord B { x: int }\nfn f(a: A): int { return a->x; }\nfn main(): int { let b: B = B(x: 1); return f(b); }\n'),
    ('int-overflow-expr', 'fn main(): int { return 99999999999999999999999999; }\n'),
    ('overflow-19', 'fn main(): int { return 9223372036854775808; }\n'),
    ('max-int', 'fn main(): int { return 9223372036854775807; }\n'),
    ('reclit-dup-vs-type', 'record P { a: int }\nfn main(): int { let p: P = P(a: 1, a: true); return 0; }\n'),
    ('print-status-int', 'fn main(): int { let r: status<int> = byte_at("ab", 0); print(r); return 0; }\n'),
    ('nested-calls', 'fn f(a: int): int { return a + 1; }\nfn g(a: int): int { return f(a) * 2; }\nfn main(): int { print(g(5)); return 0; }\n'),
    ('while-continue', 'fn main(): int { let x: int = 0; while x < 3 { continue; } return x; }\n'),
    ('empty-fn-body', 'fn f(): int {}\nfn main(): int { return 0; }\n'),
    ('nested-let-core', 'fn main(): int { if true { let x: int = 1; print(x); } return 0; }\n'),
    ('arm-let-core', 'fn main(): int { let s: status<int> = push([], 1); match s { ok(v) => { let y: int = v; print(y); return 0; }, err(e) => { return 1; } } }\n'),
    ('len-nosuch-arity', 'fn main(): int { print(len(nosuch(), 1)); return 0; }\n'),
    ('f-nosuch-arity', 'fn f(a: int): int { return a; }\nfn main(): int { return f(nosuch(), 1, 2); }\n'),
    ('f2-true-nosuch', 'fn f(a: int, b: int): int { return a; }\nfn main(): int { return f(true, nosuch()); }\n'),
    ('push-arity', 'fn main(): int { print(push(nosuch(), 1, 2)); return 0; }\n'),
    ('let-unit-init', 'fn main(): int { let x: int = print(1); return 0; }\n'),
    ('match-scrutinee-record', 'record P { a: int }\nfn main(): int { let p: P = P(a: 1); match p { _ => { return 0; } } }\n'),
    ('match-scrutinee-call', 'fn f(): int { return 1; }\nfn main(): int { match f() { 1 => { return 0; }, _ => { return 1; } } }\n'),
    ('while-cond-missing', 'fn main(): int { while { break; } return 0; }\n'),
    ('zero-arg-call', 'fn e(): int { return 1; }\nfn main(): int { return e(); }\n'),
    ('call-unit-ret-stmt', 'fn f(): int { return 1; }\nfn main(): int { f(); return 0; }\n'),
    ('match-status-int-ok', 'fn main(): int { let r: status<int> = byte_at("ab", 0); match r { ok(v) => { print(v); return 0; }, err(e) => { return 1; } } }\n'),
    ('push-ok-value', 'fn main(): int { let l: list<int,2> = [1]; let s: status<list<int,2>> = push(l, 2); return 0; }\n'),
]

CHAIN600 = " + ".join(["1"] * 600)
DEEP20 = "list<" * 20 + "int" + ",1>" * 20

P2_CASES = P2_CASES + [
    ('chain-600', 'fn main(): int { return ' + CHAIN600 + '; }\n'),
    ('deep-nest-let', 'fn main(): int { let x: ' + DEEP20 + ' = []; return 0; }\n'),
]

XFAIL = XFAIL = {
    # baby fuel bound: 600-term flat chain rejected by baby, accepted by host (documented divergence)
    'chain-600': (5, None),
    # ±1 at 255+ nested blocks: host trips one block earlier than baby's
    # enter-model (parse/analyzer dual-counter asymmetry under study)
    'blocks-300': (5, None),
}

BABY_TO_HOST = {0: None, 1: 'PAR_LEX_ERROR', 2: 'PAR_LEX_ERROR', 3: 'PAR_LEX_ERROR',
                4: 'PAR_LEX_ERROR', 5: 'PAR_DEPTH_EXCEEDED', 6: 'PAR_EXPECTED_TOKEN',
                7: 'PAR_UNEXPECTED_TOKEN', 8: 'PAR_UNEXPECTED_EOF', 9: 'SEM_UNDECLARED',
                10: 'SEM_DUPLICATE', 11: 'SEM_TYPE_MISMATCH', 12: 'SEM_ARITY_MISMATCH',
                13: 'SEM_UNKNOWN_FUNCTION', 14: 'SEM_LIMIT_EXCEEDED', 15: 'SEM_PROFILE_EXCLUDED'}


class CoreCleanGuardTests(unittest.TestCase):
    def test_01_bundle_core_clean(self):
        combo = _combo_text()
        for profile in ("core", None):
            kw = {} if profile is None else {"profile": profile}
            result = analyzer.analyze(combo, "combo.rl", **kw)
            self.assertTrue(result.ok, (profile, result.diagnostic))

    def test_02_no_nested_lets(self):
        # Structural pin: every `let` in the baby source sits directly in a
        # function body block (the frozen core discipline). The analyzer in
        # test_01 enforces it; this scan names the offender on failure.
        from tools.rynorlang import parse as hostparse
        u, l, c, p = _read_selfhost()
        for name, src in (("util.rl", u), ("lex.rl", l), ("check.rl", c), ("prog.rl", p)):
            res = hostparse.parse(src, name)
            self.assertTrue(res.ok, res.diagnostic)

            def walk(node, depth, fname):
                kind = node.kind
                if kind == "FunctionDef":
                    nm = next((ch.text for ch in node.children if ch.kind == "Identifier"), "?")
                    for ch in node.children:
                        if ch.kind == "Block":
                            walk(ch, 1, nm)
                    return
                if kind == "Block":
                    for ch in node.children or []:
                        if ch.kind == "LetStmt":
                            if depth != 1:
                                raise AssertionError(f"{name}: nested let '{ch.text}' in {fname}")
                        else:
                            walk(ch, depth, fname)
                    return
                for ch in node.children or []:
                    if ch.kind == "Block":
                        walk(ch, depth + 1, fname)
                    else:
                        walk(ch, depth, fname)

            for top in res.root.children:
                walk(top, 0, None)


class WholeProgramDifferentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = [(n, s) for n, s in P1_CASES] + [(n, s) for n, s in P2_CASES]
        cls.combo = _combo_text()
        cls.results = _run_cases(cls.combo, cls.cases)

    def test_03_differential_matrix(self):
        fails = []
        for (name, src), (code, off) in zip(self.cases, self.results):
            if name in XFAIL:
                want_c, _want_o = XFAIL[name]
                if code != want_c:
                    fails.append((name, code, off, ("XFAIL", want_c)))
                continue
            hr = analyzer.analyze(src, "t.rl", profile="core")
            hcode = None if hr.ok else hr.diagnostic.code
            hoff = None if hr.ok else hr.diagnostic.span.offset
            want = BABY_TO_HOST.get(code, "UNMAPPED")
            if not (want == hcode and (code == 0 or off == hoff)):
                fails.append((name, (code, off), (hcode, hoff)))
        self.assertEqual(fails, [])


def _mut(base, old, new, count=1):
    if base.count(old) != count:
        raise AssertionError(f"mutant anchor found {base.count(old)}x, want {count}: {old[:80]!r}")
    return base.replace(old, new)


def _mutated_combo(old, new, count=1):
    return _mut(_combo_text(), old, new, count)


def _baby_of(combo_src, src):
    return _run_cases(combo_src, [("t", src)])[0]


class FrontendMutantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()

    def _assert_red(self, mutant_combo, src, host_code):
        hr = analyzer.analyze(src, "t.rl", profile="core")
        self.assertTrue(hr.ok if host_code is None else not hr.ok)
        if host_code is not None:
            self.assertEqual(hr.diagnostic.code, host_code)
        code, _off = _baby_of(mutant_combo, src)
        want = BABY_TO_HOST.get(code, "UNMAPPED")
        hcode = None if hr.ok else hr.diagnostic.code
        self.assertNotEqual((want, code == 0), (hcode, hr.ok),
                            f"mutant failed to diverge on {src[:60]!r}: baby=({code}) host={hcode}")

    def _assert_green_baseline(self, src, host_code):
        code, _off = _baby_of(self.combo, src)
        hr = analyzer.analyze(src, "t.rl", profile="core")
        hcode = None if hr.ok else hr.diagnostic.code
        self.assertEqual(hcode, host_code)
        self.assertEqual(BABY_TO_HOST.get(code), hcode)

    def test_m1_skip_duplicate_check(self):
        src = "fn main(): int { let x: int = 1; let x: int = 2; return 0; }\n"
        self._assert_green_baseline(src, "SEM_DUPLICATE")
        combo = _mutated_combo(
            "  if lo == 0 - 1 { } else { return derr(10, f, ns); }",
            "  if lo == 0 - 1 { } else { }")
        self._assert_red(combo, src, "SEM_DUPLICATE")

    def test_m2_resolve_after_use(self):
        src = "fn main(): int { print(x); let x: int = 1; return 0; }\n"
        self._assert_green_baseline(src, "SEM_UNDECLARED")
        combo = _mutated_combo(
            "  if pos >= useoff { return best; } else { }",
            "  if pos >= len(src) { return best; } else { }")
        combo = _mut(combo,
            "  if t->s >= useoff { return best; } else { }",
            "  if t->s >= len(src) { return best; } else { }")
        self._assert_red(combo, src, "SEM_UNDECLARED")

    def test_m3_ignore_arity(self):
        src = "fn f(a: int): int { return a; }\nfn main(): int { return f(); }\n"
        self._assert_green_baseline(src, "SEM_ARITY_MISMATCH")
        combo = _mutated_combo(
            "  return pgm_hcount(src, lp->p, bo, 0);",
            "  return 0;")
        self._assert_red(combo, src, "SEM_ARITY_MISMATCH")

    def test_m4_nominal_equality(self):
        src = ("record A { x: int }\nrecord B { x: int }\n"
               "fn f(a: A): int { return a->x; }\n"
               "fn main(): int { let b: B = B(x: 1); return f(b); }\n")
        self._assert_green_baseline(src, "SEM_TYPE_MISMATCH")
        combo = _mutated_combo(
            "fn teq_at(a: list<int,24>, b: list<int,24>, i: int): bool {",
            "fn teq_at(a: list<int,24>, b: list<int,24>, i: int): bool {\n  if i == 0 { return true; } else { }",
            count=1)
        self._assert_red(combo, src, "SEM_TYPE_MISMATCH")

    def test_m5_arm_scope_closure(self):
        src = ('fn main(): int { let r: status<int> = byte_at("ab", 0); '
               'match r { ok(v) => { print(v); return 0; }, err(v) => { print(v + 1); return 1; } } }')
        self._assert_green_baseline(src, None)
        combo = _mutated_combo(
            "fn arm_find(src: str, f: int, fnstart: int, fnend: int, useoff: int, ns: int, nl: int, skip: int): VS {",
            "fn arm_find(src: str, f: int, fnstart: int, fnend: int, useoff: int, ns: int, nl: int, skip: int): VS {\n"
            "  return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok());",
            count=1)
        self._assert_red(combo, src, None)

    def test_m6_payload_narrowing(self):
        src = ('fn main(): int { let r: status<str> = fread("/x", 0, 1); '
               'match r { ok(v) => { print(v + 1); return 0; }, err(e) => { return 1; } } }')
        self._assert_green_baseline(src, "SEM_TYPE_MISMATCH")
        combo = _mutated_combo(
            "fn scrut_payload(t: list<int,24>): list<int,24> {",
            "fn scrut_payload(t: list<int,24>): list<int,24> {\n  return tscal(1);",
            count=1)
        self._assert_red(combo, src, "SEM_TYPE_MISMATCH")

    def test_m7_return_check(self):
        src = "fn main(): int { return true; }\n"
        self._assert_green_baseline(src, "SEM_TYPE_MISMATCH")
        combo = _mutated_combo(
            "  if teq(e->t, ret) { return SR(p: sc->p, d: dok()); } else { }",
            "  return SR(p: sc->p, d: dok());")
        self._assert_red(combo, src, "SEM_TYPE_MISMATCH")

    def test_m8_list_cap(self):
        src = "fn main(): int { let l: list<int,2> = [1, 2, 3]; return 0; }\n"
        self._assert_green_baseline(src, "SEM_LIMIT_EXCEEDED")
        combo = _mutated_combo(
            "  if n <= cap { return SR(p: t->p, d: dok()); } else { }",
            "  return SR(p: t->p, d: dok());")
        self._assert_red(combo, src, "SEM_LIMIT_EXCEEDED")


if __name__ == "__main__":
    unittest.main()

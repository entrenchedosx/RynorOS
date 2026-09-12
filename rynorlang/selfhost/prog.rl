// selfhost whole-program driver: lex -> shape -> collect -> heads (+bodies P2).
// Entry: pgm_check(src, f) -> D, first diagnostic in frozen phase order.
// Phase order mirrors the host: lex errors beat parse errors beat collection
// (duplicates/reserved) beat header resolution beat bodies. Bodies skipped here.
// Core-dialect: top-level lets only.
fn pgm_check(src: str, f: int): D {
  let e0: D = pgm_lex(src, f, 0);
  if has_err(e0) { return e0; } else { }
  let e1: D = pgm_shape(src, f, 0, len(src));
  if has_err(e1) { return e1; } else { }
  let best: int = pgm_collect(src, f, 0, len(src), 0 - 1);
  if best == 0 - 1 { } else { return derr(10, f, best); }
  let e3: D = pgm_heads(src, f, 0, len(src));
  if has_err(e3) { return e3; } else { }
  return pgm_bodies(src, f, 0, len(src));
}
fn pgm_bodies(src: str, f: int, pos: int, end: int): D {
  let it: TI = tl_next(src, f, pos, end);
  if it->k == 0 { return dok(); } else { }
  if it->k == 1 { return pgm_body_item(src, f, it, end); } else { }
  return pgm_bodies(src, f, it->p, end);
}
fn pgm_body_item(src: str, f: int, it: TI, end: int): D {
  let d: D = pgm_body_fn(src, f, it->s, it->s + it->l);
  if has_err(d) { return d; } else { }
  return pgm_bodies(src, f, it->p, end);
}
fn pgm_body_fn(src: str, f: int, cs: int, ce: int): D {
  let kw: Tok = next_tok(src, cs);
  let nm: Tok = next_tok(src, kw->p);
  let lp: Tok = next_tok(src, nm->p);
  let bo: int = pgm_body_open(src, lp->p, ce);
  if bo == 0 - 1 { return dok(); } else { }
  let be: int = pgm_brace_end(src, bo, ce);
  if be == 0 - 1 { return dok(); } else { }
  let rt: list<int,24> = pgm_body_ret(src, f, lp->p, bo);
  return pgm_block(src, f, cs, ce, rt, bo + 1, be, 2, 1, 0);
}
fn pgm_body_ret(src: str, f: int, pos: int, bo: int): list<int,24> {
  let cp: int = pgm_close_paren(src, pos, bo);
  if cp == 0 - 1 { return tscal(0); } else { }
  let t: Tok = next_tok(src, cp + 1);
  if pgm_is_obrace(src, t) { return tscal(0); } else { }
  if pgm_is_colon(src, t) { return pgm_body_ret_ty(src, f, t->p, bo); } else { }
  return tscal(0);
}
fn pgm_body_ret_ty(src: str, f: int, pos: int, bo: int): list<int,24> {
  let ts: Tok = next_tok(src, pos);
  if ts->k == 1 { } else { return tscal(0); }
  let ty: TR = intern_ty(src, f, ts->s, bo, 0);
  if has_err(ty->d) { return tscal(0); } else { }
  return ty->t;
}
fn pgm_brace_end(src: str, open: int, end: int): int {
  return pgm_brace2(src, open + 1, end, 1);
}
fn pgm_brace2(src: str, pos: int, end: int, depth: int): int {
  if pos >= end { return 0 - 1; } else { }
  let t: Tok = next_tok(src, pos);
  if t->k == 0 - 2 { return pgm_brace2(src, t->p, end, depth); } else { }
  if t->k == 0 { return 0 - 1; } else { }
  if t->k == 4 { return pgm_brace2op(src, t->p, end, depth, t); } else { }
  return pgm_brace2(src, t->p, end, depth);
}
fn pgm_brace2op(src: str, pos: int, end: int, depth: int, t: Tok): int {
  if t->l == 1 { if tok_byte(src, t->s) == 123 { return pgm_brace2(src, t->p, end, depth + 1); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 125 { if depth == 1 { return t->p; } else { } return pgm_brace2(src, t->p, end, depth - 1); } else { } } else { }
  return pgm_brace2(src, t->p, end, depth);
}
fn pgm_is_kw(src: str, s: int, l: int): bool {
  if l == 2 {
    if beq(src, s, "fn", 0, 2) { return true; } else { }
    if beq(src, s, "if", 0, 2) { return true; } else { }
  } else { }
  if l == 3 {
    if beq(src, s, "let", 0, 3) { return true; } else { }
    if beq(src, s, "int", 0, 3) { return true; } else { }
    if beq(src, s, "str", 0, 3) { return true; } else { }
  } else { }
  if l == 4 {
    if beq(src, s, "else", 0, 4) { return true; } else { }
    if beq(src, s, "true", 0, 4) { return true; } else { }
    if beq(src, s, "bool", 0, 4) { return true; } else { }
  } else { }
  if l == 5 {
    if beq(src, s, "while", 0, 5) { return true; } else { }
    if beq(src, s, "false", 0, 5) { return true; } else { }
  } else { }
  if l == 6 {
    if beq(src, s, "return", 0, 6) { return true; } else { }
  } else { }
  return false;
}
fn pgm_is_fnreserved(src: str, s: int, l: int): bool {
  if is_reserved_w(src, s, l) { return true; } else { }
  if l == 5 {
    if beq(src, s, "print", 0, 5) { return true; } else { }
    if beq(src, s, "match", 0, 5) { return true; } else { }
    if beq(src, s, "break", 0, 5) { return true; } else { }
  } else { }
  if l == 8 {
    if beq(src, s, "continue", 0, 8) { return true; } else { }
  } else { }
  return false;
}
fn pgm_is_typreserved(src: str, s: int, l: int): bool {
  if pgm_is_fnreserved(src, s, l) { return true; } else { }
  if l == 4 {
    if beq(src, s, "list", 0, 4) { return true; } else { }
  } else { }
  if l == 3 {
    if beq(src, s, "map", 0, 3) { return true; } else { }
  } else { }
  if l == 6 {
    if beq(src, s, "status", 0, 6) { return true; } else { }
    if beq(src, s, "result", 0, 6) { return true; } else { }
  } else { }
  return false;
}
fn pgm_min(a: int, b: int): int {
  if a == 0 - 1 { return b; } else { }
  if b == 0 - 1 { return a; } else { }
  if b < a { return b; } else { }
  return a;
}
fn pgm_lex(src: str, f: int, pos: int): D {
  let t: Tok = next_tok(src, pos);
  if t->k == 0 - 2 { return pgm_lex_err(src, f, t); } else { }
  if t->k == 0 { return dok(); } else { }
  if t->p <= pos { return derr(1, f, pos); } else { }
  return pgm_lex(src, f, t->p);
}
fn pgm_lex_err(src: str, f: int, t: Tok): D {
  if t->l == 2 { return derr(2, f, pgm_numstart(src, t->s)); } else { }
  return derr(t->l, f, t->s);
}
fn pgm_is_lparen(src: str, t: Tok): bool {
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 40 { return true; } else { } } else { } } else { }
  return false;
}
fn pgm_is_close(src: str, t: Tok): bool {
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 41 { return true; } else { } } else { } } else { }
  return false;
}
fn pgm_is_colon(src: str, t: Tok): bool {
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 58 { return true; } else { } } else { } } else { }
  return false;
}
fn pgm_is_comma(src: str, t: Tok): bool {
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 44 { return true; } else { } } else { } } else { }
  return false;
}
fn pgm_is_obrace(src: str, t: Tok): bool {
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 123 { return true; } else { } } else { } } else { }
  return false;
}
fn pgm_is_cbrace(src: str, t: Tok): bool {
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 125 { return true; } else { } } else { } } else { }
  return false;
}
fn pgm_is_lt(src: str, t: Tok): bool {
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 60 { return true; } else { } } else { } } else { }
  return false;
}
fn pgm_is_semi(src: str, t: Tok): bool {
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 59 { return true; } else { } } else { } } else { }
  return false;
}
fn pgm_is_eq(src: str, t: Tok): bool {
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 61 { return true; } else { } } else { } } else { }
  return false;
}
fn pgm_block(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, end: int, depth: int, blk: int, brk: int): D {
  if depth > 256 { return derr(5, f, pos); } else { }
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return dok(); } else { }
  if pgm_is_cbrace(src, t) { return dok(); } else { }
  let s: SR = pgm_stmt(src, f, fs, fe, ret, t->s, end, depth, blk, brk);
  if has_err(s->d) { return s->d; } else { }
  return pgm_block(src, f, fs, fe, ret, s->p, end, depth, blk, brk);
}
fn pgm_block_in(src: str, f: int, fs: int, fe: int, ret: list<int,24>, t: Tok, end: int, depth: int, blk: int, brk: int): SR {
  let be: int = pgm_brace_end(src, t->s, end);
  if be == 0 - 1 { return SR(p: t->s, d: derr(8, f, end)); } else { }
  let d: D = pgm_block(src, f, fs, fe, ret, t->p, be, depth + 1, blk + 1, brk);
  if has_err(d) { return SR(p: t->s, d: d); } else { }
  return SR(p: be, d: dok());
}
fn pgm_stmt(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, end: int, depth: int, blk: int, brk: int): SR {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return SR(p: pos, d: derr(8, f, end)); } else { }
  if t->k == 1 { return pgm_stmt_kw(src, f, fs, fe, ret, pos, end, depth, blk, brk, t); } else { }
  if pgm_is_obrace(src, t) { return pgm_block_in(src, f, fs, fe, ret, t, end, depth, blk, brk); } else { }
  return pgm_exprstmt(src, f, fs, fe, pos, end, depth);
}
fn pgm_stmt_kw(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, end: int, depth: int, blk: int, brk: int, t: Tok): SR {
  if t->l == 3 { if beq(src, t->s, "let", 0, 3) { return pgm_let(src, f, fs, fe, ret, pos, end, depth, blk, brk, t); } else { } } else { }
  if t->l == 6 { if beq(src, t->s, "return", 0, 6) { return pgm_return(src, f, fs, fe, ret, pos, end, depth, t); } else { } } else { }
  if t->l == 2 { if beq(src, t->s, "if", 0, 2) { return pgm_if(src, f, fs, fe, ret, pos, end, depth, blk, brk, t); } else { } } else { }
  if t->l == 5 {
    if beq(src, t->s, "while", 0, 5) { return pgm_while(src, f, fs, fe, ret, pos, end, depth, blk, t); } else { }
    if beq(src, t->s, "match", 0, 5) { return pgm_match_try(src, f, fs, fe, ret, pos, end, depth, blk, brk, t); } else { }
    if beq(src, t->s, "break", 0, 5) { return pgm_jump(src, f, pos, end, brk, t); } else { }
  } else { }
  if t->l == 8 { if beq(src, t->s, "continue", 0, 8) { return pgm_jump(src, f, pos, end, brk, t); } else { } } else { }
  if t->l == 3 { if beq(src, t->s, "use", 0, 3) { return pgm_usebody(src, f, fs, fe, pos, end, depth, t); } else { } } else { }
  return pgm_exprstmt(src, f, fs, fe, pos, end, depth);
}
fn pgm_jump(src: str, f: int, pos: int, end: int, brk: int, t: Tok): SR {
  let nx: Tok = pgm_tok(src, t->p, end);
  if pgm_is_semi(src, nx) { } else { return SR(p: pos, d: derr(6, f, nx->s)); }
  if brk == 0 { return SR(p: pos, d: derr(11, f, t->s)); } else { }
  return SR(p: nx->p, d: dok());
}
fn pgm_usebody(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, t: Tok): SR {
  let nx: Tok = pgm_tok(src, t->p, end);
  if nx->k == 3 { return pgm_usebody_semi(src, f, pos, end, nx); } else { }
  return pgm_exprstmt(src, f, fs, fe, pos, end, depth);
}
fn pgm_usebody_semi(src: str, f: int, pos: int, end: int, nx: Tok): SR {
  let sc: Tok = pgm_tok(src, nx->p, end);
  if pgm_is_semi(src, sc) { return SR(p: sc->p, d: dok()); } else { }
  return SR(p: pos, d: derr(6, f, sc->s));
}
fn pgm_exprstmt(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int): SR {
  let ee: int = pgm_expr_end(src, pos, end, 0, 0);
  if ee == 0 - 1 { return SR(p: pos, d: derr(6, f, end)); } else { }
  if ee < 0 - 1 { return SR(p: pos, d: derr(7, f, 0 - ee - 100)); } else { }
  let ec: Tok = pgm_tok(src, ee, end);
  if pgm_is_semi(src, ec) { } else { return SR(p: pos, d: derr(6, f, ec->s)); }
  let e: TR = x_or(src, f, fs, fe, pos, end, depth, 512);
  if has_err(e->d) { return SR(p: pos, d: e->d); } else { }
  let sc: Tok = pgm_tok(src, e->p, end);
  if pgm_is_semi(src, sc) { return SR(p: sc->p, d: dok()); } else { }
  return SR(p: pos, d: derr(6, f, sc->s));
}
fn pgm_let(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, end: int, depth: int, blk: int, brk: int, t: Tok): SR {
  let nm: Tok = pgm_tok(src, t->p, end);
  if nm->k == 1 { } else { return SR(p: pos, d: derr(6, f, nm->s)); }
  if blk > 1 { return SR(p: pos, d: derr(15, f, nm->s)); } else { }
  if pgm_is_kw(src, nm->s, nm->l) { return SR(p: pos, d: derr(6, f, nm->s)); } else { }
  if pgm_is_break(src, nm) { return SR(p: pos, d: derr(10, f, nm->s)); } else { }
  let cn: Tok = pgm_tok(src, nm->p, end);
  if pgm_is_colon(src, cn) { } else { return SR(p: pos, d: derr(6, f, cn->s)); }
  let ts: Tok = pgm_tok(src, cn->p, end);
  if ts->k == 1 { } else { return SR(p: pos, d: derr(6, f, ts->s)); }
  let ty: TR = intern_ty(src, f, ts->s, end, 0);
  if has_err(ty->d) { return SR(p: pos, d: pgm_tyerr(src, f, ty, ts->s)); } else { }
  let eq: Tok = pgm_tok(src, ty->p, end);
  if pgm_is_eq(src, eq) { } else { return SR(p: pos, d: derr(6, f, eq->s)); }
  let ee: int = pgm_expr_end(src, eq->p, end, 0, 0);
  if ee == 0 - 1 { return SR(p: pos, d: derr(6, f, end)); } else { }
  if ee < 0 - 1 { return SR(p: pos, d: derr(7, f, 0 - ee - 100)); } else { }
  let ec: Tok = pgm_tok(src, ee, end);
  if pgm_is_semi(src, ec) { } else { return SR(p: pos, d: derr(6, f, ec->s)); }
  return pgm_let_init(src, f, fs, fe, ret, nm, ty->t, ts->s, eq->p, end, depth, blk, brk, t->s);
}
fn pgm_let_dup(src: str, f: int, fs: int, fe: int, ns: int, nl: int, letpos: int): D {
  let lo: int = flat_let(src, fs, letpos, ns, nl, fs, 0, 0 - 1);
  if lo == 0 - 1 { } else { return derr(10, f, ns); }
  let po: VS = hdr_find(src, fs, fe, ns, nl);
  if po->off == 0 - 1 { } else { return derr(10, f, ns); }
  let fr: FR = res_fn(src, f, ns, nl, ns);
  if fr->i == 0 - 1 { return dok(); } else { }
  return derr(10, f, ns);
}
fn pgm_let_init(src: str, f: int, fs: int, fe: int, ret: list<int,24>, nm: Tok, want: list<int,24>, ts: int, pos: int, end: int, depth: int, blk: int, brk: int, letpos: int): SR {
  let lt: Tok = pgm_tok(src, pos, end);
  if lt->k == 4 { if lt->l == 1 { if tok_byte(src, lt->s) == 91 { return pgm_let_list(src, f, fs, fe, nm, want, ts, pos, end, depth, letpos); } else { } } else { } } else { }
  let e: TR = x_or(src, f, fs, fe, pos, end, depth, 512);
  if has_err(e->d) { return SR(p: pos, d: e->d); } else { }
  let sc: Tok = pgm_tok(src, e->p, end);
  if pgm_is_semi(src, sc) { } else { return SR(p: pos, d: derr(6, f, sc->s)); }
  let es: Tok = pgm_tok(src, pos, end);
  if tbase(e->t) == 0 { return SR(p: pos, d: derr(11, f, es->s)); } else { }
  if tbase(e->t) == 5 { return pgm_let_lit(src, f, fs, fe, nm, e->t, want, ts, es->s, sc->p, end, depth, letpos); } else { }
  if teq(e->t, want) { } else { return SR(p: pos, d: derr(11, f, es->s)); }
  return pgm_let_done(src, f, fs, fe, nm, sc->p, letpos);
}
fn pgm_let_list(src: str, f: int, fs: int, fe: int, nm: Tok, want: list<int,24>, ts: int, pos: int, end: int, depth: int, letpos: int): SR {
  if tbase(want) == 5 { return pgm_wantlist_ok(src, f, fs, fe, nm, want, pos, end, depth, letpos); } else { }
  let e: TR = x_or(src, f, fs, fe, pos, end, depth, 512);
  if has_err(e->d) { return SR(p: pos, d: e->d); } else { }
  let sc: Tok = pgm_tok(src, e->p, end);
  if pgm_is_semi(src, sc) { } else { return SR(p: pos, d: derr(6, f, sc->s)); }
  return SR(p: pos, d: derr(11, f, pos));
}
fn pgm_let_lit(src: str, f: int, fs: int, fe: int, nm: Tok, lit: list<int,24>, want: list<int,24>, ts: int, pos: int, scp: int, end: int, depth: int, letpos: int): SR {
  if teq(lit, want) { return pgm_let_done(src, f, fs, fe, nm, scp, letpos); } else { }
  return SR(p: pos, d: derr(11, f, pos));
}
fn pgm_let_done(src: str, f: int, fs: int, fe: int, nm: Tok, scp: int, letpos: int): SR {
  let d: D = pgm_let_dup(src, f, fs, fe, nm->s, nm->l, letpos);
  if has_err(d) { return SR(p: nm->s, d: d); } else { }
  return SR(p: scp, d: dok());
}
fn pgm_last(t: list<int,24>): int {
  return unwrap_or(t[len(t) - 1], 0);
}
fn pgm_mid(t: list<int,24>): list<int,24> {
  return pgm_mid_at(t, 1, len(t) - 1, []);
}
fn pgm_mid_at(t: list<int,24>, i: int, end: int, acc: list<int,24>): list<int,24> {
  if i >= end { return acc; } else { }
  return pgm_mid_at(t, i + 1, end, unwrap_or(push(acc, unwrap_or(t[i], 0)), acc));
}
fn pgm_return(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, end: int, depth: int, t: Tok): SR {
  let nx: Tok = pgm_tok(src, t->p, end);
  if pgm_is_semi(src, nx) { return pgm_return_bare(src, f, ret, pos, t, nx); } else { }
  let ee: int = pgm_expr_end(src, nx->s, end, 0, 0);
  if ee == 0 - 1 { return SR(p: pos, d: derr(6, f, end)); } else { }
  if ee < 0 - 1 { return SR(p: pos, d: derr(7, f, 0 - ee - 100)); } else { }
  let ec: Tok = pgm_tok(src, ee, end);
  if pgm_is_semi(src, ec) { } else { return SR(p: pos, d: derr(6, f, ec->s)); }
  let e: TR = x_or(src, f, fs, fe, nx->s, end, depth, 512);
  if has_err(e->d) { return SR(p: pos, d: e->d); } else { }
  let sc: Tok = pgm_tok(src, e->p, end);
  if pgm_is_semi(src, sc) { } else { return SR(p: pos, d: derr(6, f, sc->s)); }
  if teq(e->t, ret) { return SR(p: sc->p, d: dok()); } else { }
  return SR(p: pos, d: derr(11, f, nx->s));
}
fn pgm_return_bare(src: str, f: int, ret: list<int,24>, pos: int, t: Tok, nx: Tok): SR {
  if tbase(ret) == 0 { return SR(p: nx->p, d: dok()); } else { }
  return SR(p: pos, d: derr(11, f, t->s));
}
fn pgm_if(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, end: int, depth: int, blk: int, brk: int, t: Tok): SR {
  let cs: Tok = pgm_tok(src, t->p, end);
  if pgm_is_obrace(src, cs) { return pgm_if_mapcond(src, f, fs, fe, ret, pos, end, depth, blk, brk, t); } else { }
  let ce: int = pgm_expr_end(src, t->p, end, 0, 0);
  if ce == 0 - 1 { return SR(p: pos, d: derr(6, f, end)); } else { }
  if ce < 0 - 1 { return SR(p: pos, d: derr(7, f, 0 - ce - 100)); } else { }
  let cb: Tok = pgm_tok(src, ce, end);
  if pgm_is_obrace(src, cb) { } else { return SR(p: pos, d: derr(6, f, cb->s)); }
  return pgm_if_go(src, f, fs, fe, ret, pos, end, depth, blk, brk, t, cs);
}
fn pgm_if_go(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, end: int, depth: int, blk: int, brk: int, t: Tok, cs: Tok): SR {
  let c: TR = x_or(src, f, fs, fe, t->p, end, depth + 1, 512);
  if has_err(c->d) { return SR(p: pos, d: c->d); } else { }
  if tbase(c->t) == 2 { } else { return SR(p: pos, d: derr(11, f, cs->s)); }
  let bo: Tok = pgm_tok(src, c->p, end);
  if pgm_is_obrace(src, bo) { } else { return SR(p: pos, d: derr(6, f, bo->s)); }
  return pgm_if_then(src, f, fs, fe, ret, pos, end, depth, blk, brk, t, bo);
}
fn pgm_if_mapcond(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, end: int, depth: int, blk: int, brk: int, t: Tok): SR {
  let cs: Tok = pgm_tok(src, t->p, end);
  return pgm_if_go(src, f, fs, fe, ret, pos, end, depth, blk, brk, t, cs);
}
fn pgm_if_then(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, end: int, depth: int, blk: int, brk: int, t: Tok, bo: Tok): SR {
  let be: int = pgm_brace_end(src, bo->s, end);
  if be == 0 - 1 { return SR(p: pos, d: derr(8, f, end)); } else { }
  let d: D = pgm_block(src, f, fs, fe, ret, bo->p, be, depth + 2, blk + 1, brk);
  if has_err(d) { return SR(p: pos, d: d); } else { }
  return pgm_if_else(src, f, fs, fe, ret, be, end, depth + 1, blk, brk);
}
fn pgm_if_else(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, end: int, depth: int, blk: int, brk: int): SR {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 1 { if t->l == 4 { if beq(src, t->s, "else", 0, 4) { return pgm_else(src, f, fs, fe, ret, pos, end, depth, blk, brk, t); } else { } } else { } } else { }
  return SR(p: pos, d: dok());
}
fn pgm_else(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, end: int, depth: int, blk: int, brk: int, t: Tok): SR {
  let nx: Tok = pgm_tok(src, t->p, end);
  if nx->k == 1 { if nx->l == 2 { if beq(src, nx->s, "if", 0, 2) { return pgm_if(src, f, fs, fe, ret, nx->s, end, depth, blk, brk, nx); } else { } } else { } } else { }
  if pgm_is_obrace(src, nx) { return pgm_else_block(src, f, fs, fe, ret, pos, end, depth, blk, brk, nx); } else { }
  return SR(p: pos, d: derr(6, f, nx->s));
}
fn pgm_else_block(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, end: int, depth: int, blk: int, brk: int, nx: Tok): SR {
  let be: int = pgm_brace_end(src, nx->s, end);
  if be == 0 - 1 { return SR(p: pos, d: derr(8, f, end)); } else { }
  let d: D = pgm_block(src, f, fs, fe, ret, nx->p, be, depth + 1, blk + 1, brk);
  if has_err(d) { return SR(p: pos, d: d); } else { }
  return SR(p: be, d: dok());
}
fn pgm_while(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, end: int, depth: int, blk: int, t: Tok): SR {
  let cs: Tok = pgm_tok(src, t->p, end);
  if pgm_is_obrace(src, cs) { return pgm_while_mapcond(src, f, fs, fe, ret, pos, end, depth, blk, t); } else { }
  let ce: int = pgm_expr_end(src, t->p, end, 0, 0);
  if ce == 0 - 1 { return SR(p: pos, d: derr(6, f, end)); } else { }
  if ce < 0 - 1 { return SR(p: pos, d: derr(7, f, 0 - ce - 100)); } else { }
  let cb: Tok = pgm_tok(src, ce, end);
  if pgm_is_obrace(src, cb) { } else { return SR(p: pos, d: derr(6, f, cb->s)); }
  return pgm_while_go(src, f, fs, fe, ret, pos, end, depth, blk, t, cs);
}
fn pgm_while_mapcond(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, end: int, depth: int, blk: int, t: Tok): SR {
  let cs: Tok = pgm_tok(src, t->p, end);
  return pgm_while_go(src, f, fs, fe, ret, pos, end, depth, blk, t, cs);
}
fn pgm_match_mapcond(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, end: int, depth: int, blk: int, brk: int, t: Tok): SR {
  let st: Tok = pgm_tok(src, t->p, end);
  let s: TR = x_or(src, f, fs, fe, t->p, end, depth, 512);
  if has_err(s->d) { return SR(p: pos, d: s->d); } else { }
  let bo: Tok = pgm_tok(src, s->p, end);
  if pgm_is_obrace(src, bo) { return pgm_match(src, f, fs, fe, ret, pos, end, depth, blk, brk, t, s, bo, st->s); } else { }
  return SR(p: pos, d: derr(6, f, bo->s));
}
fn pgm_while_go(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, end: int, depth: int, blk: int, t: Tok, cs: Tok): SR {
  let c: TR = x_or(src, f, fs, fe, t->p, end, depth, 512);
  if has_err(c->d) { return SR(p: pos, d: c->d); } else { }
  if tbase(c->t) == 2 { } else { return SR(p: pos, d: derr(11, f, cs->s)); }
  let bo: Tok = pgm_tok(src, c->p, end);
  if pgm_is_obrace(src, bo) { } else { return SR(p: pos, d: derr(6, f, bo->s)); }
  let be: int = pgm_brace_end(src, bo->s, end);
  if be == 0 - 1 { return SR(p: pos, d: derr(8, f, end)); } else { }
  let d: D = pgm_block(src, f, fs, fe, ret, bo->p, be, depth + 1, blk + 1, 1);
  if has_err(d) { return SR(p: pos, d: d); } else { }
  return SR(p: be, d: dok());
}
fn x_or(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, fuel: int): TR {
  let lt: Tok = pgm_tok(src, pos, end);
  let l: TR = x_and(src, f, fs, fe, pos, end, depth, fuel);
  if has_err(l->d) { return l; } else { }
  return x_or_rest(src, f, fs, fe, l->t, lt->s, l->p, end, depth, fuel);
}
fn x_or_rest(src: str, f: int, fs: int, fe: int, left: list<int,24>, ls: int, pos: int, end: int, depth: int, fuel: int): TR {
  if fuel <= 0 { return TR(t: left, p: pos, d: derr(5, f, pos)); } else { }
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return TR(t: left, p: pos, d: dok()); } else { }
  if t->k == 4 { if t->l == 2 { if beq(src, t->s, "||", 0, 2) { return x_or_op(src, f, fs, fe, left, ls, t, end, depth, fuel); } else { } } else { } } else { }
  return TR(t: left, p: pos, d: dok());
}
fn x_or_op(src: str, f: int, fs: int, fe: int, left: list<int,24>, ls: int, t: Tok, end: int, depth: int, fuel: int): TR {
  let r: TR = x_and(src, f, fs, fe, t->p, end, depth, fuel - 1);
  if has_err(r->d) { return r; } else { }
  let c: TR = check_binop(src, f, t->s, t->l, left, r->t, ls);
  if has_err(c->d) { return c; } else { }
  return x_or_rest(src, f, fs, fe, c->t, ls, r->p, end, depth, fuel - 1);
}
fn x_and(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, fuel: int): TR {
  let lt: Tok = pgm_tok(src, pos, end);
  let l: TR = x_bor(src, f, fs, fe, pos, end, depth, fuel);
  if has_err(l->d) { return l; } else { }
  return x_and_rest(src, f, fs, fe, l->t, lt->s, l->p, end, depth, fuel);
}
fn x_and_rest(src: str, f: int, fs: int, fe: int, left: list<int,24>, ls: int, pos: int, end: int, depth: int, fuel: int): TR {
  if fuel <= 0 { return TR(t: left, p: pos, d: derr(5, f, pos)); } else { }
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return TR(t: left, p: pos, d: dok()); } else { }
  if t->k == 4 { if t->l == 2 { if beq(src, t->s, "&&", 0, 2) { return x_and_op(src, f, fs, fe, left, ls, t, end, depth, fuel); } else { } } else { } } else { }
  return TR(t: left, p: pos, d: dok());
}
fn x_and_op(src: str, f: int, fs: int, fe: int, left: list<int,24>, ls: int, t: Tok, end: int, depth: int, fuel: int): TR {
  let r: TR = x_bor(src, f, fs, fe, t->p, end, depth, fuel - 1);
  if has_err(r->d) { return r; } else { }
  let c: TR = check_binop(src, f, t->s, t->l, left, r->t, ls);
  if has_err(c->d) { return c; } else { }
  return x_and_rest(src, f, fs, fe, c->t, ls, r->p, end, depth, fuel - 1);
}
fn x_bor(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, fuel: int): TR {
  let lt: Tok = pgm_tok(src, pos, end);
  let l: TR = x_bxor(src, f, fs, fe, pos, end, depth, fuel);
  if has_err(l->d) { return l; } else { }
  return x_bor_rest(src, f, fs, fe, l->t, lt->s, l->p, end, depth, fuel);
}
fn x_bor_rest(src: str, f: int, fs: int, fe: int, left: list<int,24>, ls: int, pos: int, end: int, depth: int, fuel: int): TR {
  if fuel <= 0 { return TR(t: left, p: pos, d: derr(5, f, pos)); } else { }
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return TR(t: left, p: pos, d: dok()); } else { }
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 124 { return x_bor_op(src, f, fs, fe, left, ls, t, end, depth, fuel); } else { } } else { } } else { }
  return TR(t: left, p: pos, d: dok());
}
fn x_bor_op(src: str, f: int, fs: int, fe: int, left: list<int,24>, ls: int, t: Tok, end: int, depth: int, fuel: int): TR {
  let r: TR = x_bxor(src, f, fs, fe, t->p, end, depth, fuel - 1);
  if has_err(r->d) { return r; } else { }
  let c: TR = check_binop(src, f, t->s, t->l, left, r->t, ls);
  if has_err(c->d) { return c; } else { }
  return x_bor_rest(src, f, fs, fe, c->t, ls, r->p, end, depth, fuel - 1);
}
fn x_bxor(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, fuel: int): TR {
  let lt: Tok = pgm_tok(src, pos, end);
  let l: TR = x_band(src, f, fs, fe, pos, end, depth, fuel);
  if has_err(l->d) { return l; } else { }
  return x_bxor_rest(src, f, fs, fe, l->t, lt->s, l->p, end, depth, fuel);
}
fn x_bxor_rest(src: str, f: int, fs: int, fe: int, left: list<int,24>, ls: int, pos: int, end: int, depth: int, fuel: int): TR {
  if fuel <= 0 { return TR(t: left, p: pos, d: derr(5, f, pos)); } else { }
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return TR(t: left, p: pos, d: dok()); } else { }
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 94 { return x_bxor_op(src, f, fs, fe, left, ls, t, end, depth, fuel); } else { } } else { } } else { }
  return TR(t: left, p: pos, d: dok());
}
fn x_bxor_op(src: str, f: int, fs: int, fe: int, left: list<int,24>, ls: int, t: Tok, end: int, depth: int, fuel: int): TR {
  let r: TR = x_band(src, f, fs, fe, t->p, end, depth, fuel - 1);
  if has_err(r->d) { return r; } else { }
  let c: TR = check_binop(src, f, t->s, t->l, left, r->t, ls);
  if has_err(c->d) { return c; } else { }
  return x_bxor_rest(src, f, fs, fe, c->t, ls, r->p, end, depth, fuel - 1);
}
fn x_band(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, fuel: int): TR {
  let lt: Tok = pgm_tok(src, pos, end);
  let l: TR = x_eq(src, f, fs, fe, pos, end, depth, fuel);
  if has_err(l->d) { return l; } else { }
  return x_band_rest(src, f, fs, fe, l->t, lt->s, l->p, end, depth, fuel);
}
fn x_band_rest(src: str, f: int, fs: int, fe: int, left: list<int,24>, ls: int, pos: int, end: int, depth: int, fuel: int): TR {
  if fuel <= 0 { return TR(t: left, p: pos, d: derr(5, f, pos)); } else { }
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return TR(t: left, p: pos, d: dok()); } else { }
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 38 { return x_band_op(src, f, fs, fe, left, ls, t, end, depth, fuel); } else { } } else { } } else { }
  return TR(t: left, p: pos, d: dok());
}
fn x_band_op(src: str, f: int, fs: int, fe: int, left: list<int,24>, ls: int, t: Tok, end: int, depth: int, fuel: int): TR {
  let r: TR = x_eq(src, f, fs, fe, t->p, end, depth, fuel - 1);
  if has_err(r->d) { return r; } else { }
  let c: TR = check_binop(src, f, t->s, t->l, left, r->t, ls);
  if has_err(c->d) { return c; } else { }
  return x_band_rest(src, f, fs, fe, c->t, ls, r->p, end, depth, fuel - 1);
}
fn x_eq(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, fuel: int): TR {
  let lt: Tok = pgm_tok(src, pos, end);
  let l: TR = x_rel(src, f, fs, fe, pos, end, depth, fuel);
  if has_err(l->d) { return l; } else { }
  return x_eq_rest(src, f, fs, fe, l->t, lt->s, l->p, end, depth, fuel);
}
fn x_eq_rest(src: str, f: int, fs: int, fe: int, left: list<int,24>, ls: int, pos: int, end: int, depth: int, fuel: int): TR {
  if fuel <= 0 { return TR(t: left, p: pos, d: derr(5, f, pos)); } else { }
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return TR(t: left, p: pos, d: dok()); } else { }
  if t->k == 4 { if t->l == 2 { if x_eq_op(src, t) == 1 { return x_eq_go(src, f, fs, fe, left, ls, t, end, depth, fuel); } else { } } else { } } else { }
  return TR(t: left, p: pos, d: dok());
}
fn x_eq_op(src: str, t: Tok): int {
  if beq(src, t->s, "==", 0, 2) { return 1; } else { }
  if beq(src, t->s, "!=", 0, 2) { return 1; } else { }
  return 0;
}
fn x_eq_go(src: str, f: int, fs: int, fe: int, left: list<int,24>, ls: int, t: Tok, end: int, depth: int, fuel: int): TR {
  let r: TR = x_rel(src, f, fs, fe, t->p, end, depth, fuel - 1);
  if has_err(r->d) { return r; } else { }
  let c: TR = check_binop(src, f, t->s, t->l, left, r->t, ls);
  if has_err(c->d) { return c; } else { }
  return x_eq_rest(src, f, fs, fe, c->t, ls, r->p, end, depth, fuel - 1);
}
fn x_rel(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, fuel: int): TR {
  let lt: Tok = pgm_tok(src, pos, end);
  let l: TR = x_shift(src, f, fs, fe, pos, end, depth, fuel);
  if has_err(l->d) { return l; } else { }
  return x_rel_rest(src, f, fs, fe, l->t, lt->s, l->p, end, depth, fuel);
}
fn x_rel_rest(src: str, f: int, fs: int, fe: int, left: list<int,24>, ls: int, pos: int, end: int, depth: int, fuel: int): TR {
  if fuel <= 0 { return TR(t: left, p: pos, d: derr(5, f, pos)); } else { }
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return TR(t: left, p: pos, d: dok()); } else { }
  if t->k == 4 { if x_rel_op(src, t) == 1 { return x_rel_go(src, f, fs, fe, left, ls, t, end, depth, fuel); } else { } } else { }
  return TR(t: left, p: pos, d: dok());
}
fn x_rel_op(src: str, t: Tok): int {
  if t->l == 1 { if tok_byte(src, t->s) == 60 { return 1; } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 62 { return 1; } else { } } else { }
  if t->l == 2 { if beq(src, t->s, "<=", 0, 2) { return 1; } else { } } else { }
  if t->l == 2 { if beq(src, t->s, ">=", 0, 2) { return 1; } else { } } else { }
  return 0;
}
fn x_rel_go(src: str, f: int, fs: int, fe: int, left: list<int,24>, ls: int, t: Tok, end: int, depth: int, fuel: int): TR {
  let r: TR = x_shift(src, f, fs, fe, t->p, end, depth, fuel - 1);
  if has_err(r->d) { return r; } else { }
  let c: TR = check_binop(src, f, t->s, t->l, left, r->t, ls);
  if has_err(c->d) { return c; } else { }
  return x_rel_rest(src, f, fs, fe, c->t, ls, r->p, end, depth, fuel - 1);
}
fn x_shift(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, fuel: int): TR {
  let lt: Tok = pgm_tok(src, pos, end);
  let l: TR = x_add(src, f, fs, fe, pos, end, depth, fuel);
  if has_err(l->d) { return l; } else { }
  return x_shift_rest(src, f, fs, fe, l->t, lt->s, l->p, end, depth, fuel);
}
fn x_shift_rest(src: str, f: int, fs: int, fe: int, left: list<int,24>, ls: int, pos: int, end: int, depth: int, fuel: int): TR {
  if fuel <= 0 { return TR(t: left, p: pos, d: derr(5, f, pos)); } else { }
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return TR(t: left, p: pos, d: dok()); } else { }
  if t->k == 4 { if t->l == 2 { if x_shift_op(src, t) == 1 { return x_shift_go(src, f, fs, fe, left, ls, t, end, depth, fuel); } else { } } else { } } else { }
  return TR(t: left, p: pos, d: dok());
}
fn x_shift_op(src: str, t: Tok): int {
  if beq(src, t->s, "<<", 0, 2) { return 1; } else { }
  if beq(src, t->s, ">>", 0, 2) { return 1; } else { }
  return 0;
}
fn x_shift_go(src: str, f: int, fs: int, fe: int, left: list<int,24>, ls: int, t: Tok, end: int, depth: int, fuel: int): TR {
  let r: TR = x_add(src, f, fs, fe, t->p, end, depth, fuel - 1);
  if has_err(r->d) { return r; } else { }
  let c: TR = check_binop(src, f, t->s, t->l, left, r->t, ls);
  if has_err(c->d) { return c; } else { }
  return x_shift_rest(src, f, fs, fe, c->t, ls, r->p, end, depth, fuel - 1);
}
fn x_add(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, fuel: int): TR {
  let lt: Tok = pgm_tok(src, pos, end);
  let l: TR = x_mul(src, f, fs, fe, pos, end, depth, fuel);
  if has_err(l->d) { return l; } else { }
  return x_add_rest(src, f, fs, fe, l->t, lt->s, l->p, end, depth, fuel);
}
fn x_add_rest(src: str, f: int, fs: int, fe: int, left: list<int,24>, ls: int, pos: int, end: int, depth: int, fuel: int): TR {
  if fuel <= 0 { return TR(t: left, p: pos, d: derr(5, f, pos)); } else { }
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return TR(t: left, p: pos, d: dok()); } else { }
  if t->k == 4 { if t->l == 1 { if x_add_op(src, t) == 1 { return x_add_go(src, f, fs, fe, left, ls, t, end, depth, fuel); } else { } } else { } } else { }
  return TR(t: left, p: pos, d: dok());
}
fn x_add_op(src: str, t: Tok): int {
  if tok_byte(src, t->s) == 43 { return 1; } else { }
  if tok_byte(src, t->s) == 45 { return 1; } else { }
  return 0;
}
fn x_add_go(src: str, f: int, fs: int, fe: int, left: list<int,24>, ls: int, t: Tok, end: int, depth: int, fuel: int): TR {
  let r: TR = x_mul(src, f, fs, fe, t->p, end, depth, fuel - 1);
  if has_err(r->d) { return r; } else { }
  let c: TR = check_binop(src, f, t->s, t->l, left, r->t, ls);
  if has_err(c->d) { return c; } else { }
  return x_add_rest(src, f, fs, fe, c->t, ls, r->p, end, depth, fuel - 1);
}
fn x_mul(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, fuel: int): TR {
  let lt: Tok = pgm_tok(src, pos, end);
  let l: TR = x_unary(src, f, fs, fe, pos, end, depth, fuel);
  if has_err(l->d) { return l; } else { }
  return x_mul_rest(src, f, fs, fe, l->t, lt->s, l->p, end, depth, fuel);
}
fn x_mul_rest(src: str, f: int, fs: int, fe: int, left: list<int,24>, ls: int, pos: int, end: int, depth: int, fuel: int): TR {
  if fuel <= 0 { return TR(t: left, p: pos, d: derr(5, f, pos)); } else { }
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return TR(t: left, p: pos, d: dok()); } else { }
  if t->k == 4 { if t->l == 1 { if x_mul_op(src, t) == 1 { return x_mul_go(src, f, fs, fe, left, ls, t, end, depth, fuel); } else { } } else { } } else { }
  return TR(t: left, p: pos, d: dok());
}
fn x_mul_op(src: str, t: Tok): int {
  if tok_byte(src, t->s) == 42 { return 1; } else { }
  if tok_byte(src, t->s) == 47 { return 1; } else { }
  if tok_byte(src, t->s) == 37 { return 1; } else { }
  return 0;
}
fn x_mul_go(src: str, f: int, fs: int, fe: int, left: list<int,24>, ls: int, t: Tok, end: int, depth: int, fuel: int): TR {
  let r: TR = x_unary(src, f, fs, fe, t->p, end, depth, fuel - 1);
  if has_err(r->d) { return r; } else { }
  let c: TR = check_binop(src, f, t->s, t->l, left, r->t, ls);
  if has_err(c->d) { return c; } else { }
  return x_mul_rest(src, f, fs, fe, c->t, ls, r->p, end, depth, fuel - 1);
}
fn x_unary(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, fuel: int): TR {
  if depth > 255 { return TR(t: tscal(1), p: pos, d: derr(5, f, pos)); } else { }
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return TR(t: tscal(1), p: pos, d: derr(7, f, pos)); } else { }
  if t->k == 4 { if t->l == 1 { if x_unary_op(src, t) == 1 { return x_unary_go(src, f, fs, fe, t, end, depth, fuel); } else { } } else { } } else { }
  return x_postfix(src, f, fs, fe, pos, end, depth, fuel);
}
fn x_unary_op(src: str, t: Tok): int {
  if tok_byte(src, t->s) == 45 { return 1; } else { }
  if tok_byte(src, t->s) == 33 { return 1; } else { }
  if tok_byte(src, t->s) == 126 { return 1; } else { }
  return 0;
}
fn x_unary_go(src: str, f: int, fs: int, fe: int, t: Tok, end: int, depth: int, fuel: int): TR {
  let r: TR = x_unary(src, f, fs, fe, t->p, end, depth + 1, fuel);
  if has_err(r->d) { return r; } else { }
  let c: TR = check_unop(src, f, t->s, t->l, r->t, t->s);
  if has_err(c->d) { return c; } else { }
  return TR(t: c->t, p: r->p, d: dok());
}
fn x_postfix(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, fuel: int): TR {
  let bt: Tok = pgm_tok(src, pos, end);
  let b: TR = x_primary(src, f, fs, fe, pos, end, depth, fuel);
  if has_err(b->d) { return b; } else { }
  return x_post_rest(src, f, fs, fe, b->t, bt->s, b->p, end, depth, fuel);
}
fn x_post_rest(src: str, f: int, fs: int, fe: int, base: list<int,24>, bs: int, pos: int, end: int, depth: int, fuel: int): TR {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return TR(t: base, p: pos, d: dok()); } else { }
  if t->k == 4 { return x_post_op(src, f, fs, fe, base, bs, pos, end, depth, fuel, t); } else { }
  return TR(t: base, p: pos, d: dok());
}
fn x_post_op(src: str, f: int, fs: int, fe: int, base: list<int,24>, bs: int, pos: int, end: int, depth: int, fuel: int, t: Tok): TR {
  if t->l == 2 { if beq(src, t->s, "->", 0, 2) { return x_field(src, f, fs, fe, base, bs, pos, end, depth, fuel, t); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 91 { return x_index(src, f, fs, fe, base, bs, pos, end, depth, fuel, t); } else { } } else { }
  return TR(t: base, p: pos, d: dok());
}
fn x_field(src: str, f: int, fs: int, fe: int, base: list<int,24>, bs: int, pos: int, end: int, depth: int, fuel: int, t: Tok): TR {
  let nm: Tok = pgm_tok(src, t->p, end);
  if nm->k == 1 { } else { return TR(t: tscal(1), p: pos, d: derr(7, f, nm->s)); }
  if pgm_is_kw(src, nm->s, nm->l) { return TR(t: tscal(1), p: pos, d: derr(6, f, nm->s)); } else { }
  if tbase(base) == 4 { return x_field_rec(src, f, fs, fe, base, bs, pos, end, depth, fuel, nm); } else { }
  return TR(t: tscal(1), p: pos, d: derr(11, f, bs));
}
fn x_field_rec(src: str, f: int, fs: int, fe: int, base: list<int,24>, bs: int, pos: int, end: int, depth: int, fuel: int, nm: Tok): TR {
  let ft: TR = rec_ftype(src, f, unwrap_or(base[1], 0), nm->s, nm->l);
  if has_err(ft->d) { return TR(t: tscal(1), p: pos, d: derr(9, f, nm->s)); } else { }
  return x_post_rest(src, f, fs, fe, ft->t, bs, nm->p, end, depth, fuel);
}
fn x_index(src: str, f: int, fs: int, fe: int, base: list<int,24>, bs: int, pos: int, end: int, depth: int, fuel: int, t: Tok): TR {
  if tbase(base) == 5 { return x_index_list(src, f, fs, fe, base, bs, pos, end, depth, fuel, t); } else { }
  return TR(t: tscal(1), p: pos, d: derr(11, f, bs));
}
fn x_index_list(src: str, f: int, fs: int, fe: int, base: list<int,24>, bs: int, pos: int, end: int, depth: int, fuel: int, t: Tok): TR {
  let ist: Tok = pgm_tok(src, t->p, end);
  let ix: TR = x_or(src, f, fs, fe, t->p, end, depth, 512);
  if has_err(ix->d) { return ix; } else { }
  if tbase(ix->t) == 1 { } else { return TR(t: tscal(1), p: pos, d: derr(11, f, ist->s)); }
  let cb: Tok = pgm_tok(src, ix->p, end);
  if cb->k == 4 { if cb->l == 1 { if tok_byte(src, cb->s) == 93 { return x_post_rest(src, f, fs, fe, tsub(base, 1, []), bs, cb->p, end, depth, fuel); } else { } } else { } } else { }
  return TR(t: tscal(1), p: pos, d: derr(6, f, cb->s));
}
fn x_primary(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, fuel: int): TR {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return TR(t: tscal(1), p: pos, d: derr(7, f, pos)); } else { }
  if t->k == 0 - 2 { return TR(t: tscal(1), p: pos, d: derr(t->l, f, t->s)); } else { }
  if t->k == 2 { return x_intlit(src, f, t); } else { }
  if t->k == 3 { return TR(t: tscal(3), p: t->p, d: dok()); } else { }
  if t->k == 1 { return x_ident(src, f, fs, fe, t, end, depth, fuel); } else { }
  if t->k == 4 { return x_punct(src, f, fs, fe, t, end, depth, fuel); } else { }
  return TR(t: tscal(1), p: pos, d: derr(7, f, pos));
}
fn x_intlit(src: str, f: int, t: Tok): TR {
  if pgm_int_ok(src, t->s, t->l) == 1 { return TR(t: tscal(1), p: t->p, d: dok()); } else { }
  return TR(t: tscal(1), p: t->s, d: derr(2, f, t->s));
}
fn pgm_int_ok(src: str, s: int, l: int): int {
  if l > 19 { return 0; } else { }
  if l < 19 { return 1; } else { }
  return pgm_int_cmp(src, s);
}
fn pgm_int_cmp(src: str, s: int): int {
  let m: str = "9223372036854775807";
  return pgm_int_cmat(src, s, m, 0);
}
fn pgm_int_cmat(src: str, s: int, m: str, i: int): int {
  if i >= 19 { return 1; } else { }
  let a: int = unwrap_or(byte_at(src, s + i), 0);
  let b: int = unwrap_or(byte_at(m, i), 0);
  if a == b { return pgm_int_cmat(src, s, m, i + 1); } else { }
  if a < b { return 1; } else { }
  return 0;
}
fn x_ident(src: str, f: int, fs: int, fe: int, t: Tok, end: int, depth: int, fuel: int): TR {
  if t->l == 4 { if beq(src, t->s, "true", 0, 4) { return TR(t: tscal(2), p: t->p, d: dok()); } else { } } else { }
  if t->l == 5 { if beq(src, t->s, "false", 0, 5) { return TR(t: tscal(2), p: t->p, d: dok()); } else { } } else { }
  if pgm_is_typeword(src, t) { return pgm_typeword_qual(src, f, t, end); } else { }
  if pgm_is_kw(src, t->s, t->l) { return TR(t: tscal(1), p: t->s, d: derr(7, f, t->s)); } else { }
  let nx: Tok = pgm_tok(src, t->p, end);
  if nx->k == 4 { if nx->l == 1 { if tok_byte(src, nx->s) == 40 { return pgm_call(src, f, fs, fe, t->s, t->l, nx->p, end, depth, fuel); } else { } } else { } } else { }
  if nx->k == 4 { if nx->l == 2 { if beq(src, nx->s, "::", 0, 2) { return TR(t: tscal(1), p: t->s, d: derr(9, f, t->s)); } else { } } else { } } else { }
  let v: VS = res_var(src, f, fs, fe, t->s, t->s, t->l);
  if has_err(v->d) { return TR(t: tscal(1), p: t->p, d: v->d); } else { }
  return x_var_ty(src, f, fs, fe, t, v, end, depth, fuel);
}
fn x_var_ty(src: str, f: int, fs: int, fe: int, t: Tok, v: VS, end: int, depth: int, fuel: int): TR {
  let ty: TR = infer_var_ty(src, f, fs, fe, v);
  if has_err(ty->d) { return ty; } else { }
  return x_post_rest(src, f, fs, fe, ty->t, t->s, t->p, end, depth, fuel);
}
fn x_punct(src: str, f: int, fs: int, fe: int, t: Tok, end: int, depth: int, fuel: int): TR {
  if t->l == 1 { if tok_byte(src, t->s) == 40 { return x_paren(src, f, fs, fe, t, end, depth); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 91 { return x_listlit(src, f, fs, fe, t, end, depth); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 123 { return x_maplit(src, f, fs, fe, t, end, depth); } else { } } else { }
  return TR(t: tscal(1), p: t->s, d: derr(7, f, t->s));
}
fn x_paren(src: str, f: int, fs: int, fe: int, t: Tok, end: int, depth: int): TR {
  if depth > 255 { return TR(t: tscal(1), p: t->s, d: derr(5, f, t->s)); } else { }
  let nx: Tok = pgm_tok(src, t->p, end);
  if nx->k == 4 { if nx->l == 1 { if tok_byte(src, nx->s) == 41 { return TR(t: tscal(1), p: t->s, d: derr(7, f, nx->s)); } else { } } else { } } else { }
  let e: TR = x_or(src, f, fs, fe, t->p, end, depth + 1, 512);
  if has_err(e->d) { return e; } else { }
  let cb: Tok = pgm_tok(src, e->p, end);
  if cb->k == 4 { if cb->l == 1 { if tok_byte(src, cb->s) == 41 { return x_post_rest(src, f, fs, fe, e->t, t->s, cb->p, end, depth, 512); } else { } } else { } } else { }
  return TR(t: tscal(1), p: t->s, d: derr(6, f, cb->s));
}
fn pgm_lit0(): list<int,24> {
  return unwrap_or(push(tcons(5, tscal(0), 0, []), 0), tcons(5, tscal(0), 0, []));
}
fn x_listlit(src: str, f: int, fs: int, fe: int, t: Tok, end: int, depth: int): TR {
  if depth > 255 { return TR(t: tscal(1), p: t->s, d: derr(5, f, t->s)); } else { }
  let nx: Tok = pgm_tok(src, t->p, end);
  if nx->k == 4 { if nx->l == 1 { if tok_byte(src, nx->s) == 93 { return x_post_rest(src, f, fs, fe, pgm_lit0(), t->s, nx->p, end, depth, 512); } else { } } else { } } else { }
  return x_list_first(src, f, fs, fe, t, end, depth, t->s);
}
fn x_list_first(src: str, f: int, fs: int, fe: int, t: Tok, end: int, depth: int, bs: int): TR {
  let e: TR = x_or(src, f, fs, fe, t->p, end, depth + 1, 512);
  if has_err(e->d) { return e; } else { }
  if tbase(e->t) == 0 { return TR(t: tscal(1), p: t->s, d: derr(11, f, t->s)); } else { }
  return x_list_rest(src, f, fs, fe, e->t, 1, e->p, end, depth, bs);
}
fn x_list_rest(src: str, f: int, fs: int, fe: int, elem: list<int,24>, n: int, pos: int, end: int, depth: int, bs: int): TR {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return TR(t: tscal(1), p: pos, d: derr(6, f, pos)); } else { }
  if pgm_is_comma(src, t) { return x_list_next(src, f, fs, fe, elem, n, t->p, end, depth, bs); } else { }
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 93 { return x_post_rest(src, f, fs, fe, tlist_code(elem, n), bs, t->p, end, depth, 512); } else { } } else { } } else { }
  return TR(t: tscal(1), p: pos, d: derr(6, f, t->s));
}
fn x_list_next(src: str, f: int, fs: int, fe: int, elem: list<int,24>, n: int, pos: int, end: int, depth: int, bs: int): TR {
  let lt: Tok = pgm_tok(src, pos, end);
  if lt->k == 4 { if lt->l == 1 { if tok_byte(src, lt->s) == 93 { return TR(t: tscal(1), p: pos, d: derr(6, f, lt->s)); } else { } } else { } } else { }
  let e: TR = x_or(src, f, fs, fe, pos, end, depth + 1, 512);
  if has_err(e->d) { return e; } else { }
  if tbase(e->t) == 0 { return TR(t: tscal(1), p: pos, d: derr(11, f, lt->s)); } else { }
  if teq(e->t, elem) { } else { return TR(t: tscal(1), p: pos, d: derr(11, f, lt->s)); }
  return x_list_rest(src, f, fs, fe, elem, n + 1, e->p, end, depth, bs);
}
fn x_maplit(src: str, f: int, fs: int, fe: int, t: Tok, end: int, depth: int): TR {
  if depth > 255 { return TR(t: tscal(1), p: t->s, d: derr(5, f, t->s)); } else { }
  let nx: Tok = pgm_tok(src, t->p, end);
  if nx->k == 0 { return TR(t: tscal(1), p: t->s, d: derr(11, f, t->s)); } else { }
  if nx->k == 4 { if nx->l == 1 { if tok_byte(src, nx->s) == 125 { return TR(t: tscal(1), p: t->s, d: derr(11, f, t->s)); } else { } } else { } } else { }
  return pgm_mapentry(src, f, fs, fe, t->p, end, depth + 1, t->s);
}
fn pgm_mapentry(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, bo: int): TR {
  let kc: int = pgm_mapkey(src, pos, end, 0, 0);
  if kc < 0 { return TR(t: tscal(1), p: bo, d: derr(6, f, 0 - kc - 100)); } else { }
  let k: TR = x_or(src, f, fs, fe, pos, end, depth, 512);
  if has_err(k->d) { return k; } else { }
  let ke: Tok = pgm_tok(src, k->p, end);
  if ke->s == kc { } else { return TR(t: tscal(1), p: bo, d: derr(6, f, ke->s)); }
  let v: TR = x_or(src, f, fs, fe, kc + 1, end, depth, 512);
  if has_err(v->d) { return v; } else { }
  let nx: Tok = pgm_tok(src, v->p, end);
  if pgm_is_comma(src, nx) { return pgm_mapentry(src, f, fs, fe, nx->p, end, depth, bo); } else { }
  if nx->k == 4 { if nx->l == 1 { if tok_byte(src, nx->s) == 125 { return TR(t: tscal(1), p: bo, d: derr(15, f, bo)); } else { } } else { } } else { }
  return TR(t: tscal(1), p: bo, d: derr(6, f, nx->s));
}
fn pgm_mapkey(src: str, pos: int, end: int, pd: int, bd: int): int {
  if pos >= end { return 0 - (end + 100); } else { }
  let t: Tok = next_tok(src, pos);
  if t->s >= end { return 0 - (end + 100); } else { }
  if t->k == 0 - 2 { return pgm_mapkey(src, t->p, end, pd, bd); } else { }
  if t->k == 0 { return 0 - (end + 100); } else { }
  if t->k == 4 { return pgm_mapkey_op(src, t->p, end, pd, bd, t); } else { }
  return pgm_mapkey(src, t->p, end, pd, bd);
}
fn pgm_mapkey_op(src: str, pos: int, end: int, pd: int, bd: int, t: Tok): int {
  if t->l == 2 { return pgm_mapkey(src, t->p, end, pd, bd); } else { }
  if t->l == 1 { return pgm_mapkey_op1(src, t->p, end, pd, bd, tok_byte(src, t->s), t->s); } else { }
  return pgm_mapkey(src, t->p, end, pd, bd);
}
fn pgm_mapkey_op1(src: str, pos: int, end: int, pd: int, bd: int, b: int, ts: int): int {
  if b == 40 { return pgm_mapkey(src, pos, end, pd + 1, bd); } else { }
  if b == 91 { return pgm_mapkey(src, pos, end, pd + 1, bd); } else { }
  if b == 123 { return pgm_mapkey(src, pos, end, pd, bd + 1); } else { }
  if b == 41 { if pd == 0 { return 0 - (ts + 100); } else { } return pgm_mapkey(src, pos, end, pd - 1, bd); } else { }
  if b == 93 { if pd == 0 { return 0 - (ts + 100); } else { } return pgm_mapkey(src, pos, end, pd - 1, bd); } else { }
  if b == 125 { if bd == 0 { if pd == 0 { return 0 - (ts + 100); } else { } } else { } return pgm_mapkey(src, pos, end, pd, pgm_dn(bd)); } else { }
  if b == 59 { if pd == 0 { if bd == 0 { return 0 - (ts + 100); } else { } } else { } return pgm_mapkey(src, pos, end, pd, bd); } else { }
  if b == 44 { if pd == 0 { if bd == 0 { return 0 - (ts + 100); } else { } } else { } return pgm_mapkey(src, pos, end, pd, bd); } else { }
  if b == 58 { if pd == 0 { if bd == 0 { return ts; } else { } } else { } return pgm_mapkey(src, pos, end, pd, bd); } else { }
  return pgm_mapkey(src, pos, end, pd, bd);
}
fn pgm_dn(bd: int): int {
  if bd == 0 { return 0; } else { }
  return bd - 1;
}
fn pgm_expr_end(src: str, pos: int, end: int, pd: int, bd: int): int {
  return pgm_expr_scan(src, pos, end, pd, bd, 0);
}
fn pgm_expr_scan(src: str, pos: int, end: int, pd: int, bd: int, want: int): int {
  if pos >= end { return 0 - 1; } else { }
  let t: Tok = next_tok(src, pos);
  if t->s >= end { return 0 - 1; } else { }
  if t->k == 0 - 2 { return pgm_expr_scan(src, t->p, end, pd, bd, want); } else { }
  if t->k == 0 { return 0 - 1; } else { }
  if t->k == 4 { return pgm_expr_op(src, t->p, end, pd, bd, want, t); } else { }
  if t->k == 1 { return pgm_expr_nm(src, t->p, end, pd, bd, want, t); } else { }
  if want == 1 { return t->s; } else { }
  return pgm_expr_scan(src, t->p, end, pd, bd, 1);
}
fn pgm_expr_nm(src: str, pos: int, end: int, pd: int, bd: int, want: int, t: Tok): int {
  if t->l == 4 { if beq(src, t->s, "true", 0, 4) { return pgm_expr_operand(src, t->p, end, pd, bd, want, t->s); } else { } } else { }
  if t->l == 5 { if beq(src, t->s, "false", 0, 5) { return pgm_expr_operand(src, t->p, end, pd, bd, want, t->s); } else { } } else { }
  if pgm_is_typeword(src, t) { return pgm_expr_tword(src, t->p, end, pd, bd, want, t); } else { }
  if pgm_is_kw(src, t->s, t->l) { return pgm_expr_kw(src, t, want); } else { }
  return pgm_expr_operand(src, t->p, end, pd, bd, want, t->s);
}
fn pgm_expr_tword(src: str, pos: int, end: int, pd: int, bd: int, want: int, t: Tok): int {
  let nx: Tok = next_tok(src, pos);
  if nx->k == 4 { if nx->l == 2 { if beq(src, nx->s, "::", 0, 2) { return pgm_expr_qual(src, nx->p, end, pd, bd); } else { } } else { } } else { }
  return pgm_expr_kw(src, t, want);
}
fn pgm_expr_operand(src: str, pos: int, end: int, pd: int, bd: int, want: int, ts: int): int {
  if want == 1 { return ts; } else { }
  return pgm_expr_scan(src, pos, end, pd, bd, 1);
}
fn pgm_expr_kw(src: str, t: Tok, want: int): int {
  if want == 1 { return t->s; } else { }
  return 0 - (t->s + 100);
}
fn pgm_expr_op(src: str, pos: int, end: int, pd: int, bd: int, want: int, t: Tok): int {
  if t->l == 2 { return pgm_expr_op2(src, t->p, end, pd, bd, t); } else { }
  if t->l == 1 { return pgm_expr_op1(src, t->p, end, pd, bd, want, tok_byte(src, t->s)); } else { }
  return pgm_expr_scan(src, t->p, end, pd, bd, want);
}
fn pgm_expr_op2(src: str, pos: int, end: int, pd: int, bd: int, t: Tok): int {
  if beq(src, t->s, "->", 0, 2) { return pgm_expr_qual(src, t->p, end, pd, bd); } else { }
  if beq(src, t->s, "::", 0, 2) { return pgm_expr_qual(src, t->p, end, pd, bd); } else { }
  return pgm_expr_scan(src, t->p, end, pd, bd, 0);
}
fn pgm_expr_qual(src: str, pos: int, end: int, pd: int, bd: int): int {
  let t: Tok = next_tok(src, pos);
  if t->s >= end { return 0 - 1; } else { }
  return pgm_expr_scan(src, t->p, end, pd, bd, 1);
}
fn pgm_expr_op1(src: str, pos: int, end: int, pd: int, bd: int, want: int, b: int): int {
  if b == 40 { return pgm_expr_scan(src, pos, end, pd + 1, bd, 0); } else { }
  if b == 91 { return pgm_expr_scan(src, pos, end, pd + 1, bd, 0); } else { }
  if b == 123 { return pgm_expr_obrace(src, pos, end, pd, bd, want); } else { }
  if b == 41 { if pd == 0 { return t_bpos(src, pos); } else { } return pgm_expr_scan(src, pos, end, pd - 1, bd, 1); } else { }
  if b == 93 { if pd == 0 { return t_bpos(src, pos); } else { } return pgm_expr_scan(src, pos, end, pd - 1, bd, 1); } else { }
  if b == 125 { if bd == 0 { if pd == 0 { return t_bpos(src, pos); } else { } } else { } return pgm_expr_scan(src, pos, end, pd, pgm_dn(bd), 1); } else { }
  if b == 59 { if pd == 0 { if bd == 0 { return t_bpos(src, pos); } else { } } else { } return pgm_expr_scan(src, pos, end, pd, bd, 0); } else { }
  if b == 44 { if pd == 0 { if bd == 0 { return t_bpos(src, pos); } else { } } else { } return pgm_expr_scan(src, pos, end, pd, bd, 0); } else { }
  return pgm_expr_scan(src, pos, end, pd, bd, 0);
}
fn pgm_expr_obrace(src: str, pos: int, end: int, pd: int, bd: int, want: int): int {
  if want == 1 { return t_bpos(src, pos); } else { }
  return pgm_expr_scan(src, pos, end, pd, bd + 1, 0);
}
fn t_bpos(src: str, pos: int): int {
  return pos - 1;
}
fn pgm_is_coreban(src: str, s: int, l: int): bool {
  if l == 2 {
    if beq(src, s, "ok", 0, 2) { return true; } else { }
  } else { }
  if l == 3 {
    if beq(src, s, "err", 0, 3) { return true; } else { }
    if beq(src, s, "get", 0, 3) { return true; } else { }
  } else { }
  if l == 6 {
    if beq(src, s, "insert", 0, 6) { return true; } else { }
  } else { }
  if l == 9 {
    if beq(src, s, "unwrap_ok", 0, 9) { return true; } else { }
  } else { }
  if l == 10 {
    if beq(src, s, "unwrap_err", 0, 10) { return true; } else { }
  } else { }
  return false;
}
fn pgm_call(src: str, f: int, fs: int, fe: int, ns: int, nl: int, pos: int, end: int, depth: int, fuel: int): TR {
  let r: TR = pgm_call_inner(src, f, fs, fe, ns, nl, pos, end, depth, fuel);
  if has_err(r->d) { return r; } else { }
  return x_post_rest(src, f, fs, fe, r->t, ns, r->p, end, depth, fuel);
}
fn pgm_call_inner(src: str, f: int, fs: int, fe: int, ns: int, nl: int, pos: int, end: int, depth: int, fuel: int): TR {
  if depth > 255 { return TR(t: tscal(1), p: ns, d: derr(5, f, ns)); } else { }
  if pgm_is_coreban(src, ns, nl) { return TR(t: tscal(1), p: ns, d: derr(15, f, ns)); } else { }
  if builtin_arity(src, ns, nl) == 0 - 1 { } else { return pgm_bcall(src, f, fs, fe, ns, nl, pos, end, depth + 1); }
  if pgm_namedshape(src, pos, end) == 1 { return pgm_call_named(src, f, fs, fe, ns, nl, pos, end, depth, fuel); } else { }
  let rf: FR = res_fn(src, f, ns, nl, ns);
  if has_err(rf->d) { return pgm_call_nofn(src, f, fs, fe, ns, nl, pos, end, depth, fuel); } else { }
  if rf->i == 0 - 1 { return pgm_call_nofn(src, f, fs, fe, ns, nl, pos, end, depth, fuel); } else { }
  return pgm_ucall(src, f, fs, fe, ns, nl, rf->i, pos, end, depth + 1);
}
fn pgm_call_named(src: str, f: int, fs: int, fe: int, ns: int, nl: int, pos: int, end: int, depth: int, fuel: int): TR {
  let rr: FR = res_rec(src, f, ns, nl, ns);
  if has_err(rr->d) { } else { if rr->i == 0 - 1 { } else { return pgm_reclit(src, f, fs, fe, ns, nl, rr->i, pos, end, depth + 1, fuel); } }
  let rf: FR = res_fn(src, f, ns, nl, ns);
  if has_err(rf->d) { return TR(t: tscal(1), p: ns, d: derr(13, f, ns)); } else { }
  if rf->i == 0 - 1 { return TR(t: tscal(1), p: ns, d: derr(13, f, ns)); } else { }
  return TR(t: tscal(1), p: ns, d: derr(11, f, ns));
}
fn pgm_call_nofn(src: str, f: int, fs: int, fe: int, ns: int, nl: int, pos: int, end: int, depth: int, fuel: int): TR {
  let rr: FR = res_rec(src, f, ns, nl, ns);
  if has_err(rr->d) { return TR(t: tscal(1), p: ns, d: derr(13, f, ns)); } else { }
  if rr->i == 0 - 1 { return TR(t: tscal(1), p: ns, d: derr(13, f, ns)); } else { }
  return pgm_call_rec(src, f, fs, fe, ns, nl, rr->i, pos, end, depth, fuel);
}
fn pgm_call_rec(src: str, f: int, fs: int, fe: int, ns: int, nl: int, rid: int, pos: int, end: int, depth: int, fuel: int): TR {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 1 { return pgm_reclit(src, f, fs, fe, ns, nl, rid, pos, end, depth, fuel); } else { }
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 41 { return pgm_recunit(src, f, ns, nl, rid, t); } else { } } else { } } else { }
  return TR(t: tscal(1), p: ns, d: derr(11, f, ns));
}
fn pgm_recunit(src: str, f: int, ns: int, nl: int, rid: int, t: Tok): TR {
  let q: TR = pgm_rec_missing(src, f, ns, rid, t->s);
  if has_err(q->d) { return q; } else { }
  let rr: list<int,24> = [4, rid];
  return TR(t: rr, p: t->p, d: dok());
}
fn pgm_rec_missing(src: str, f: int, ns: int, rid: int, at: int): TR {
  let n: int = pgm_recnfields(src, f, rid);
  if n == 0 { return TR(t: tscal(0), p: at, d: dok()); } else { }
  return TR(t: tscal(1), p: ns, d: derr(12, f, ns));
}
fn pgm_recnfields(src: str, f: int, rid: int): int {
  return pgm_recnf_at(src, f, rid, 0, 0);
}
fn pgm_recnf_at(src: str, f: int, rid: int, pos: int, seen: int): int {
  let it: TI = tl_next(src, f, pos, len(src));
  if it->k == 0 { return 0; } else { }
  if it->k == 0 - 1 { return pgm_recnf_at(src, f, rid, it->p, seen); } else { }
  if it->k == 2 {
    if seen == rid { return pgm_recnf_hit(src, it); } else { }
    return pgm_recnf_at(src, f, rid, it->p, seen + 1);
  } else { }
  return pgm_recnf_at(src, f, rid, it->p, seen);
}
fn pgm_recnf_hit(src: str, it: TI): int {
  let fs: int = rec_fstart(src, it->s, it->s + it->l);
  if fs == 0 - 1 { return 0; } else { }
  return pgm_recnf_n(src, fs, it->s + it->l, 0);
}
fn pgm_recnf_n(src: str, pos: int, end: int, acc: int): int {
  let t: Tok = next_tok(src, pos);
  if t->s >= end { return acc; } else { }
  if t->k == 0 - 2 { return pgm_recnf_n(src, t->p, end, acc); } else { }
  if t->k == 0 { return acc; } else { }
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 125 { return acc; } else { } } else { } return pgm_recnf_n(src, t->p, end, acc); } else { }
  if t->k == 1 { return pgm_recnf_colon(src, t->p, end, acc); } else { }
  return pgm_recnf_n(src, t->p, end, acc);
}
fn pgm_recnf_colon(src: str, pos: int, end: int, acc: int): int {
  let t: Tok = next_tok(src, pos);
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 58 { return pgm_recnf_skip(src, t->p, end, acc + 1); } else { } } else { } } else { }
  return acc;
}
fn pgm_recnf_skip(src: str, pos: int, end: int, acc: int): int {
  let e: int = pgm_tyend(src, pos, end, 0);
  if e < 0 { return acc; } else { }
  return pgm_recnf_n(src, e, end, acc);
}
fn pgm_reclit(src: str, f: int, fs: int, fe: int, ns: int, nl: int, rid: int, pos: int, end: int, depth: int, fuel: int): TR {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return TR(t: tscal(1), p: ns, d: derr(6, f, end)); } else { }
  return pgm_reclit_field(src, f, fs, fe, ns, nl, rid, t->s, end, depth, fuel, t);
}
fn pgm_reclit_field(src: str, f: int, fs: int, fe: int, ns: int, nl: int, rid: int, lp: int, end: int, depth: int, fuel: int, t: Tok): TR {
  if t->k == 1 { } else { return TR(t: tscal(1), p: ns, d: derr(6, f, t->s)); }
  if pgm_is_kw(src, t->s, t->l) { return TR(t: tscal(1), p: ns, d: derr(6, f, t->s)); } else { }
  let cn: Tok = pgm_tok(src, t->p, end);
  if pgm_is_colon(src, cn) { } else { return TR(t: tscal(1), p: ns, d: derr(6, f, cn->s)); }
  let ft: TR = rec_ftype(src, f, rid, t->s, t->l);
  if has_err(ft->d) { return TR(t: tscal(1), p: ns, d: derr(9, f, t->s)); } else { }
  if pgm_ldup(src, lp, t->s, t->s, t->l) == 1 { return TR(t: tscal(1), p: ns, d: derr(10, f, t->s)); } else { }
  let vs: Tok = pgm_tok(src, cn->p, end);
  let e: TR = x_or(src, f, fs, fe, cn->p, end, depth, 512);
  if has_err(e->d) { return e; } else { }
  if tbase(e->t) == 0 { return TR(t: tscal(1), p: ns, d: derr(11, f, vs->s)); } else { }
  if teq(e->t, ft->t) { } else { return TR(t: tscal(1), p: ns, d: derr(11, f, vs->s)); }
  return pgm_reclit_next(src, f, fs, fe, ns, nl, rid, lp, e->p, end, depth, fuel);
}
fn pgm_reclit_next(src: str, f: int, fs: int, fe: int, ns: int, nl: int, rid: int, lp: int, pos: int, end: int, depth: int, fuel: int): TR {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return TR(t: tscal(1), p: ns, d: derr(6, f, end)); } else { }
  if pgm_is_comma(src, t) { return pgm_reclit_after(src, f, fs, fe, ns, nl, rid, lp, t->p, end, depth, fuel); } else { }
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 41 { return pgm_reclit_done(src, f, fs, fe, ns, nl, rid, lp, t, end, depth); } else { } } else { } } else { }
  return TR(t: tscal(1), p: ns, d: derr(6, f, t->s));
}
fn pgm_reclit_after(src: str, f: int, fs: int, fe: int, ns: int, nl: int, rid: int, lp: int, pos: int, end: int, depth: int, fuel: int): TR {
  let lt: Tok = pgm_tok(src, pos, end);
  if lt->k == 4 { if lt->l == 1 { if tok_byte(src, lt->s) == 41 { return TR(t: tscal(1), p: ns, d: derr(6, f, lt->s)); } else { } } else { } } else { }
  return pgm_reclit_field(src, f, fs, fe, ns, nl, rid, lp, end, depth, fuel, lt);
}
fn pgm_reclit_done(src: str, f: int, fs: int, fe: int, ns: int, nl: int, rid: int, lp: int, t: Tok, end: int, depth: int): TR {
  let q: D = pgm_rec_coverage(src, f, ns, rid, lp, t->s, end, depth);
  if has_err(q) { return TR(t: tscal(1), p: ns, d: q); } else { }
  let rr: list<int,24> = [4, rid];
  return TR(t: rr, p: t->p, d: dok());
}
fn pgm_rec_coverage(src: str, f: int, ns: int, rid: int, lp: int, close: int, end: int, depth: int): D {
  return pgm_cover_tl(src, f, ns, rid, lp, close, end, depth, 0, 0);
}
fn pgm_cover_tl(src: str, f: int, ns: int, rid: int, lp: int, close: int, end: int, depth: int, pos: int, seen: int): D {
  let it: TI = tl_next(src, f, pos, len(src));
  if it->k == 0 { return dok(); } else { }
  if it->k == 0 - 1 { return pgm_cover_tl(src, f, ns, rid, lp, close, end, depth, it->p, seen); } else { }
  if it->k == 2 {
    if seen == rid { return pgm_cover_fields(src, f, ns, it, lp, close, end, depth); } else { }
    return pgm_cover_tl(src, f, ns, rid, lp, close, end, depth, it->p, seen + 1);
  } else { }
  return pgm_cover_tl(src, f, ns, rid, lp, close, end, depth, it->p, seen);
}
fn pgm_cover_fields(src: str, f: int, ns: int, it: TI, lp: int, close: int, end: int, depth: int): D {
  let fs: int = rec_fstart(src, it->s, it->s + it->l);
  if fs == 0 - 1 { return dok(); } else { }
  return pgm_cover_at(src, f, ns, fs, it->s + it->l, lp, close, end, depth);
}
fn pgm_cover_at(src: str, f: int, ns: int, pos: int, recend: int, lp: int, close: int, end: int, depth: int): D {
  let t: Tok = next_tok(src, pos);
  if t->s >= recend { return dok(); } else { }
  if t->k == 0 - 2 { return pgm_cover_at(src, f, ns, t->p, recend, lp, close, end, depth); } else { }
  if t->k == 0 { return dok(); } else { }
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 125 { return dok(); } else { } } else { } return pgm_cover_at(src, f, ns, t->p, recend, lp, close, end, depth); } else { }
  if t->k == 1 { return pgm_cover_nm(src, f, ns, t, recend, lp, close, end, depth); } else { }
  return pgm_cover_at(src, f, ns, t->p, recend, lp, close, end, depth);
}
fn pgm_cover_nm(src: str, f: int, ns: int, t: Tok, recend: int, lp: int, close: int, end: int, depth: int): D {
  if pgm_lit_has(src, lp, close, t->s, t->l) == 1 { return pgm_cover_skip(src, f, ns, t->p, recend, lp, close, end, depth); } else { }
  return derr(12, f, ns);
}
fn pgm_cover_skip(src: str, f: int, ns: int, pos: int, recend: int, lp: int, close: int, end: int, depth: int): D {
  let an: VS = annot_span(src, pos);
  if an->tl == 0 { return pgm_cover_at(src, f, ns, pos, recend, lp, close, end, depth); } else { }
  return pgm_cover_at(src, f, ns, an->ts + an->tl, recend, lp, close, end, depth);
}
fn pgm_lit_has(src: str, lp: int, close: int, ns: int, nl: int): int {
  return pgm_lit_has_at(src, lp, close, ns, nl, 0);
}
fn pgm_lit_has_at(src: str, pos: int, close: int, ns: int, nl: int, pd: int): int {
  if pos >= close { return 0; } else { }
  let t: Tok = next_tok(src, pos);
  if t->s >= close { return 0; } else { }
  if t->k == 0 - 2 { return pgm_lit_has_at(src, t->p, close, ns, nl, pd); } else { }
  if t->k == 0 { return 0; } else { }
  if t->k == 4 { return pgm_lit_has_br(src, t->p, close, ns, nl, pd, t); } else { }
  if t->k == 1 { if pd == 0 { return pgm_lit_has_nm(src, t, close, ns, nl, pd); } else { } } else { }
  return pgm_lit_has_at(src, t->p, close, ns, nl, pd);
}
fn pgm_lit_has_br(src: str, pos: int, close: int, ns: int, nl: int, pd: int, t: Tok): int {
  if t->l == 1 { if tok_byte(src, t->s) == 40 { return pgm_lit_has_at(src, t->p, close, ns, nl, pd + 1); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 91 { return pgm_lit_has_at(src, t->p, close, ns, nl, pd + 1); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 41 { return pgm_lit_has_dn(src, t->p, close, ns, nl, pd); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 93 { return pgm_lit_has_dn(src, t->p, close, ns, nl, pd); } else { } } else { }
  return pgm_lit_has_at(src, t->p, close, ns, nl, pd);
}
fn pgm_lit_has_dn(src: str, pos: int, close: int, ns: int, nl: int, pd: int): int {
  if pd == 0 { return pgm_lit_has_at(src, pos, close, ns, nl, 0); } else { }
  return pgm_lit_has_at(src, pos, close, ns, nl, pd - 1);
}
fn pgm_lit_has_nm(src: str, t: Tok, close: int, ns: int, nl: int, pd: int): int {
  if t->l == nl { if beq(src, t->s, src, ns, nl) { return pgm_lit_has_colon(src, t->p, close, ns, nl, pd); } else { } } else { }
  return pgm_lit_has_at(src, t->p, close, ns, nl, pd);
}
fn pgm_lit_has_colon(src: str, pos: int, close: int, ns: int, nl: int, pd: int): int {
  let t: Tok = next_tok(src, pos);
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 58 { return 1; } else { } } else { } } else { }
  return pgm_lit_has_at(src, t->p, close, ns, nl, pd);
}
fn pgm_fn_range(src: str, idx: int): VS {
  return pgm_fn_range_at(src, idx, 0, 0);
}
fn pgm_fn_range_at(src: str, idx: int, pos: int, seen: int): VS {
  let it: TI = tl_next(src, 0, pos, len(src));
  if it->k == 0 { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
  if it->k == 0 - 1 { return pgm_fn_range_at(src, idx, it->p, seen); } else { }
  if it->k == 1 {
    if seen == idx { return VS(off: it->s, k: 0, ts: it->s, tl: it->l, slot: 0, d: dok()); } else { }
    return pgm_fn_range_at(src, idx, it->p, seen + 1);
  } else { }
  return pgm_fn_range_at(src, idx, it->p, seen);
}
fn pgm_hparam_count(src: str, cs: int, ce: int): int {
  let kw: Tok = next_tok(src, cs);
  let nm: Tok = next_tok(src, kw->p);
  let lp: Tok = next_tok(src, nm->p);
  let bo: int = pgm_body_open(src, lp->p, ce);
  if bo == 0 - 1 { return 0; } else { }
  return pgm_hcount(src, lp->p, bo, 0);
}
fn pgm_hcount(src: str, pos: int, bo: int, acc: int): int {
  let t: Tok = next_tok(src, pos);
  if t->s >= bo { return acc; } else { }
  if t->k == 0 - 2 { return pgm_hcount(src, t->p, bo, acc); } else { }
  if t->k == 0 { return acc; } else { }
  if pgm_is_close(src, t) { return acc; } else { }
  if t->k == 1 { return pgm_hcount_skip(src, t->p, bo, acc + 1); } else { }
  return pgm_hcount(src, t->p, bo, acc);
}
fn pgm_hcount_skip(src: str, pos: int, bo: int, acc: int): int {
  let an: VS = annot_span(src, pos);
  if an->tl == 0 { return acc; } else { }
  return pgm_hcount(src, an->ts + an->tl, bo, acc);
}
fn pgm_hparam_ty(src: str, f: int, cs: int, ce: int, idx: int): TR {
  let kw: Tok = next_tok(src, cs);
  let nm: Tok = next_tok(src, kw->p);
  let lp: Tok = next_tok(src, nm->p);
  let bo: int = pgm_body_open(src, lp->p, ce);
  if bo == 0 - 1 { return TR(t: tscal(1), p: cs, d: derr(12, f, cs)); } else { }
  return pgm_hty_at(src, f, lp->p, bo, idx, 0);
}
fn pgm_hty_at(src: str, f: int, pos: int, bo: int, idx: int, cur: int): TR {
  let t: Tok = next_tok(src, pos);
  if t->k == 1 { } else { return TR(t: tscal(1), p: pos, d: derr(12, f, pos)); }
  if cur == idx { return pgm_hty_ty(src, f, t->p, bo); } else { }
  let an: VS = annot_span(src, t->p);
  if an->tl == 0 { return TR(t: tscal(1), p: pos, d: derr(12, f, pos)); } else { }
  return pgm_hty_at(src, f, an->ts + an->tl, bo, idx, cur + 1);
}
fn pgm_hty_ty(src: str, f: int, pos: int, bo: int): TR {
  let cn: Tok = next_tok(src, pos);
  let ts: Tok = next_tok(src, cn->p);
  return intern_ty(src, f, ts->s, bo, 0);
}
fn pgm_ucall(src: str, f: int, fs: int, fe: int, ns: int, nl: int, ord: int, pos: int, end: int, depth: int): TR {
  let rg: VS = pgm_fn_range(src, ord);
  if rg->off == 0 - 1 { return TR(t: tscal(1), p: ns, d: derr(13, f, ns)); } else { }
  let npar: int = pgm_hparam_count(src, rg->off, rg->ts + rg->tl);
  let c1: SR = pgm_uargs1(src, f, fs, fe, ns, pos, end, depth, 0);
  if has_err(c1->d) { return TR(t: tscal(1), p: ns, d: c1->d); } else { }
  if c1->p == npar { } else { return TR(t: tscal(1), p: ns, d: derr(12, f, ns)); }
  let c3: SR = pgm_uargs3(src, f, fs, fe, ns, rg, npar, pos, end, depth, 0);
  if has_err(c3->d) { return TR(t: tscal(1), p: ns, d: c3->d); } else { }
  let rt: TR = pgm_uret(src, f, ns, ord);
  if has_err(rt->d) { return rt; } else { }
  return TR(t: rt->t, p: c3->p, d: dok());
}
fn pgm_uargs1(src: str, f: int, fs: int, fe: int, ns: int, pos: int, end: int, depth: int, acc: int): SR {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return SR(p: acc, d: derr(6, f, end)); } else { }
  if pgm_is_close(src, t) { return SR(p: acc, d: dok()); } else { }
  let e: TR = x_or(src, f, fs, fe, pos, end, depth, 512);
  if has_err(e->d) { return SR(p: acc, d: e->d); } else { }
  if tbase(e->t) == 0 { return SR(p: acc, d: derr(11, f, t->s)); } else { }
  let nx: Tok = pgm_tok(src, e->p, end);
  if pgm_is_comma(src, nx) { return pgm_uargs1_next(src, f, fs, fe, ns, nx->p, end, depth, acc + 1); } else { }
  if pgm_is_close(src, nx) { return SR(p: acc + 1, d: dok()); } else { }
  return SR(p: acc, d: derr(6, f, nx->s));
}
fn pgm_uargs1_next(src: str, f: int, fs: int, fe: int, ns: int, pos: int, end: int, depth: int, acc: int): SR {
  let lt: Tok = pgm_tok(src, pos, end);
  if pgm_is_close(src, lt) { return SR(p: acc, d: derr(6, f, lt->s)); } else { }
  return pgm_uargs1(src, f, fs, fe, ns, pos, end, depth, acc);
}
fn pgm_uargs3(src: str, f: int, fs: int, fe: int, ns: int, rg: VS, npar: int, pos: int, end: int, depth: int, idx: int): SR {
  let t: Tok = pgm_tok(src, pos, end);
  if pgm_is_close(src, t) { return SR(p: t->p, d: dok()); } else { }
  let pt: TR = pgm_hparam_ty(src, f, rg->off, rg->ts + rg->tl, idx);
  if has_err(pt->d) { return SR(p: ns, d: pt->d); } else { }
  let lt: Tok = pgm_tok(src, pos, end);
  if lt->k == 4 { if lt->l == 1 { if tok_byte(src, lt->s) == 91 { if tbase(pt->t) == 5 { return pgm_uargs3_list(src, f, fs, fe, ns, rg, npar, pos, end, depth, idx, pt->t); } else { } } else { } } else { } } else { }
  let e: TR = x_or(src, f, fs, fe, pos, end, depth, 512);
  if has_err(e->d) { return SR(p: ns, d: e->d); } else { }
  if teq(e->t, pt->t) { } else { return SR(p: ns, d: derr(11, f, t->s)); }
  let nx: Tok = pgm_tok(src, e->p, end);
  if pgm_is_comma(src, nx) { return pgm_uargs3(src, f, fs, fe, ns, rg, npar, nx->p, end, depth, idx + 1); } else { }
  if pgm_is_close(src, nx) { return SR(p: nx->p, d: dok()); } else { }
  return SR(p: ns, d: derr(6, f, nx->s));
}
fn pgm_uargs3_list(src: str, f: int, fs: int, fe: int, ns: int, rg: VS, npar: int, pos: int, end: int, depth: int, idx: int, want: list<int,24>): SR {
  let ep: SR = pgm_wantlist(src, f, fs, fe, pos, end, depth, want);
  if has_err(ep->d) { return ep; } else { }
  let nx: Tok = pgm_tok(src, ep->p, end);
  if pgm_is_comma(src, nx) { return pgm_uargs3(src, f, fs, fe, ns, rg, npar, nx->p, end, depth, idx + 1); } else { }
  if pgm_is_close(src, nx) { return SR(p: nx->p, d: dok()); } else { }
  return SR(p: ns, d: derr(6, f, nx->s));
}
fn pgm_wantlist(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, want: list<int,24>): SR {
  if depth > 255 { return SR(p: pos, d: derr(5, f, pos)); } else { }
  let we: list<int,24> = pgm_mid(want);
  let cap: int = pgm_last(want);
  let t: Tok = pgm_tok(src, pos, end);
  return pgm_wantlist_go(src, f, fs, fe, t->p, end, depth + 1, we, cap, t->s, 0);
}
fn pgm_wantlist_go(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, we: list<int,24>, cap: int, lit: int, n: int): SR {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 93 { return pgm_wantlist_end(src, f, we, cap, lit, n, t); } else { } } else { } } else { }
  if t->k == 0 { return SR(p: lit, d: derr(6, f, end)); } else { }
  return pgm_wantlist_elem(src, f, fs, fe, pos, end, depth, we, cap, lit, n, t);
}
fn pgm_wantlist_end(src: str, f: int, we: list<int,24>, cap: int, lit: int, n: int, t: Tok): SR {
  if n <= cap { return SR(p: t->p, d: dok()); } else { }
  return SR(p: lit, d: derr(14, f, lit));
}
fn pgm_wantlist_elem(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, we: list<int,24>, cap: int, lit: int, n: int, t: Tok): SR {
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 91 { return pgm_wantlist_nest(src, f, fs, fe, pos, end, depth, we, cap, lit, n); } else { } } else { } } else { }
  let e: TR = x_or(src, f, fs, fe, pos, end, depth, 512);
  if has_err(e->d) { return SR(p: lit, d: e->d); } else { }
  if tbase(e->t) == 0 { return SR(p: lit, d: derr(11, f, pos)); } else { }
  if teq(e->t, we) { } else { return SR(p: lit, d: derr(11, f, pos)); }
  return pgm_wantlist_next(src, f, fs, fe, e->p, end, depth, we, cap, lit, n + 1);
}
fn pgm_wantlist_nest(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, we: list<int,24>, cap: int, lit: int, n: int): SR {
  if tbase(we) == 5 { return pgm_wantlist_nest2(src, f, fs, fe, pos, end, depth, we, cap, lit, n); } else { }
  let e: TR = x_or(src, f, fs, fe, pos, end, depth, 512);
  if has_err(e->d) { return SR(p: lit, d: e->d); } else { }
  return SR(p: lit, d: derr(11, f, pos));
}
fn pgm_wantlist_nest2(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, we: list<int,24>, cap: int, lit: int, n: int): SR {
  let inner: SR = pgm_wantlist(src, f, fs, fe, pos, end, depth, we);
  if has_err(inner->d) { return inner; } else { }
  return pgm_wantlist_next(src, f, fs, fe, inner->p, end, depth, we, cap, lit, n + 1);
}
fn pgm_wantlist_next(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, we: list<int,24>, cap: int, lit: int, n: int): SR {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return SR(p: lit, d: derr(6, f, end)); } else { }
  if pgm_is_comma(src, t) { return pgm_wantlist_after(src, f, fs, fe, t->p, end, depth, we, cap, lit, n); } else { }
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 93 { return pgm_wantlist_end(src, f, we, cap, lit, n, t); } else { } } else { } } else { }
  return SR(p: lit, d: derr(6, f, t->s));
}
fn pgm_wantlist_after(src: str, f: int, fs: int, fe: int, pos: int, end: int, depth: int, we: list<int,24>, cap: int, lit: int, n: int): SR {
  let lt: Tok = pgm_tok(src, pos, end);
  if lt->k == 4 { if lt->l == 1 { if tok_byte(src, lt->s) == 93 { return SR(p: lit, d: derr(6, f, lt->s)); } else { } } else { } } else { }
  return pgm_wantlist_go(src, f, fs, fe, pos, end, depth, we, cap, lit, n);
}
fn pgm_wantlist_ok(src: str, f: int, fs: int, fe: int, nm: Tok, want: list<int,24>, pos: int, end: int, depth: int, letpos: int): SR {
  let ep: SR = pgm_wantlist(src, f, fs, fe, pos, end, depth, want);
  if has_err(ep->d) { return ep; } else { }
  let sc: Tok = pgm_tok(src, ep->p, end);
  if pgm_is_semi(src, sc) { } else { return SR(p: pos, d: derr(6, f, sc->s)); }
  return pgm_let_done(src, f, fs, fe, nm, sc->p, letpos);
}
fn pgm_uret(src: str, f: int, ns: int, ord: int): TR {
  let rs: VS = fn_ret_span(src, ord);
  if rs->tl == 0 { if rs->off == 1 { return TR(t: tscal(0), p: ns, d: dok()); } else { } return TR(t: tscal(1), p: ns, d: derr(11, f, ns)); } else { }
  return intern_ty(src, f, rs->ts, rs->ts + rs->tl, 0);
}
fn pgm_bcall(src: str, f: int, fs: int, fe: int, ns: int, nl: int, pos: int, end: int, depth: int): TR {
  let want: int = builtin_arity(src, ns, nl);
  let n: int = pgm_argcount(src, pos, end);
  if n == 0 - 1 { return TR(t: tscal(1), p: ns, d: derr(6, f, end)); } else { }
  if n == want { } else { return TR(t: tscal(1), p: ns, d: derr(12, f, ns)); }
  let c0: list<int,24> = tscal(1);
  return pgm_bargs(src, f, fs, fe, ns, nl, pos, end, depth, 0, c0);
}
fn pgm_argcount(src: str, pos: int, end: int): int {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return 0 - 1; } else { }
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 41 { return 0; } else { } } else { } } else { }
  return pgm_argc_at(src, pos, end, 0, 1);
}
fn pgm_argc_at(src: str, pos: int, end: int, pd: int, acc: int): int {
  if pos >= end { return acc; } else { }
  let t: Tok = next_tok(src, pos);
  if t->k == 0 - 2 { return pgm_argc_at(src, t->p, end, pd, acc); } else { }
  if t->k == 0 { return acc; } else { }
  if t->k == 4 { return pgm_argc_op(src, t->p, end, pd, acc, t); } else { }
  return pgm_argc_at(src, t->p, end, pd, acc);
}
fn pgm_argc_op(src: str, pos: int, end: int, pd: int, acc: int, t: Tok): int {
  if t->l == 1 { if tok_byte(src, t->s) == 40 { return pgm_argc_at(src, t->p, end, pd + 1, acc); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 91 { return pgm_argc_at(src, t->p, end, pd + 1, acc); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 41 { return pgm_argc_close(src, t->p, end, pd, acc); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 93 { return pgm_argc_close(src, t->p, end, pd, acc); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 44 { if pd == 0 { return pgm_argc_at(src, t->p, end, pd, acc + 1); } else { } } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 59 { return acc; } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 123 { return acc; } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 125 { return acc; } else { } } else { }
  return pgm_argc_at(src, t->p, end, pd, acc);
}
fn pgm_argc_close(src: str, pos: int, end: int, pd: int, acc: int): int {
  if pd == 0 { return acc; } else { }
  return pgm_argc_at(src, pos, end, pd - 1, acc);
}
fn pgm_bret(src: str, f: int, ns: int, nl: int, c0: list<int,24>, nx: Tok): TR {
  let r: TR = check_builtin_ret(src, f, ns, nl, c0, ns);
  if has_err(r->d) { return r; } else { }
  return TR(t: r->t, p: nx->p, d: dok());
}
fn pgm_bargs(src: str, f: int, fs: int, fe: int, ns: int, nl: int, pos: int, end: int, depth: int, idx: int, c0: list<int,24>): TR {
  let t: Tok = pgm_tok(src, pos, end);
  if pgm_is_close(src, t) { return pgm_bret(src, f, ns, nl, c0, t); } else { }
  let at: Tok = pgm_tok(src, pos, end);
  let e: TR = x_or(src, f, fs, fe, pos, end, depth, 512);
  if has_err(e->d) { return e; } else { }
  if nl == 5 { if beq(src, ns, "print", 0, 5) { if tbase(e->t) == 0 { return TR(t: tscal(1), p: ns, d: derr(11, f, at->s)); } else { } } else { } } else { }
  let db: D = check_barg(src, f, ns, nl, idx, e->t, c0, at->s);
  if has_err(db) { return TR(t: tscal(1), p: ns, d: db); } else { }
  let nx: Tok = pgm_tok(src, e->p, end);
  if pgm_is_comma(src, nx) { return pgm_bargs(src, f, fs, fe, ns, nl, nx->p, end, depth, idx + 1, ctx_pick(c0, e->t, idx)); } else { }
  return pgm_bargs_end(src, f, fs, fe, ns, nl, e->p, end, depth, idx, c0, nx);
}
fn ctx_pick(c0: list<int,24>, et: list<int,24>, idx: int): list<int,24> {
  if idx == 0 { return et; } else { }
  return c0;
}
fn pgm_bargs_end(src: str, f: int, fs: int, fe: int, ns: int, nl: int, pos: int, end: int, depth: int, idx: int, c0: list<int,24>, nx: Tok): TR {
  if pgm_is_close(src, nx) { return pgm_bret(src, f, ns, nl, c0, nx); } else { }
  return TR(t: tscal(1), p: ns, d: derr(6, f, nx->s));
}
fn pgm_match_try(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, end: int, depth: int, blk: int, brk: int, t: Tok): SR {
  let st: Tok = pgm_tok(src, t->p, end);
  if pgm_is_obrace(src, st) { return pgm_match_mapcond(src, f, fs, fe, ret, pos, end, depth, blk, brk, t); } else { }
  let me: int = pgm_expr_end(src, t->p, end, 0, 0);
  if me == 0 - 1 { return pgm_exprstmt(src, f, fs, fe, pos, end, depth); } else { }
  if me < 0 - 1 { return SR(p: pos, d: derr(7, f, 0 - me - 100)); } else { }
  let mb: Tok = pgm_tok(src, me, end);
  if pgm_is_obrace(src, mb) { } else { return pgm_exprstmt(src, f, fs, fe, pos, end, depth); }
  let s: TR = x_or(src, f, fs, fe, t->p, end, depth, 512);
  if has_err(s->d) { return SR(p: pos, d: s->d); } else { }
  return pgm_match(src, f, fs, fe, ret, pos, end, depth, blk, brk, t, s, mb, st->s);
}
fn pgm_match(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, end: int, depth: int, blk: int, brk: int, t: Tok, s: TR, bo: Tok, ss: int): SR {
  if tbase(s->t) == 6 { return pgm_match_arms(src, f, fs, fe, ret, pos, end, depth, blk, brk, t, s->t, bo); } else { }
  if tbase(s->t) == 2 { return pgm_match_arms(src, f, fs, fe, ret, pos, end, depth, blk, brk, t, s->t, bo); } else { }
  if tbase(s->t) == 1 { return pgm_match_arms(src, f, fs, fe, ret, pos, end, depth, blk, brk, t, s->t, bo); } else { }
  if tbase(s->t) == 3 { return pgm_match_arms(src, f, fs, fe, ret, pos, end, depth, blk, brk, t, s->t, bo); } else { }
  return SR(p: pos, d: derr(11, f, ss));
}
record AR { p: int, d: D, a0: int, a1: int, a4: int, a5: int, a6: int }
fn pgm_match_arms(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, end: int, depth: int, blk: int, brk: int, t: Tok, st: list<int,24>, bo: Tok): SR {
  let be: int = pgm_brace_end(src, bo->s, end);
  if be == 0 - 1 { return SR(p: pos, d: derr(8, f, end)); } else { }
  let r: AR = pgm_arm(src, f, fs, fe, ret, bo->p, be, end, depth, blk, brk, st, 0, 0, 0, 0, 0, bo->p);
  if has_err(r->d) { return SR(p: r->p, d: r->d); } else { }
  let nx: Tok = pgm_tok(src, r->p, end);
  if pgm_is_cbrace(src, nx) { return pgm_match_exh(src, f, t, st, r, nx); } else { }
  return SR(p: pos, d: derr(6, f, nx->s));
}
fn pgm_match_exh(src: str, f: int, t: Tok, st: list<int,24>, r: AR, nx: Tok): SR {
  if pgm_exh(tbase(st), r) == 1 { return SR(p: nx->p, d: dok()); } else { }
  return SR(p: t->s, d: derr(11, f, t->s));
}
fn pgm_exh(base: int, r: AR): int {
  if base == 6 { if r->a0 == 1 { if r->a1 == 1 { return 1; } else { } } else { } if r->a6 == 1 { return 1; } else { } return 0; } else { }
  if base == 2 { if r->a4 == 1 { if r->a5 == 1 { return 1; } else { } } else { } if r->a6 == 1 { return 1; } else { } return 0; } else { }
  if r->a6 == 1 { return 1; } else { }
  return 0;
}
fn pgm_arm(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, be: int, end: int, depth: int, blk: int, brk: int, st: list<int,24>, a0: int, a1: int, a4: int, a5: int, a6: int, ao: int): AR {
  let p: int = pgm_skip_ws(src, pos, be);
  if p >= be { return AR(p: p, d: derr(7, f, p), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); } else { }
  let t: Tok = next_tok(src, p);
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 125 { return AR(p: p, d: derr(7, f, p), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); } else { } } else { } } else { }
  if a6 == 1 { return AR(p: p, d: derr(11, f, p), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); } else { }
  return pgm_armpat(src, f, fs, fe, ret, p, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, t, ao);
}
fn pgm_armpat(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, be: int, end: int, depth: int, blk: int, brk: int, st: list<int,24>, a0: int, a1: int, a4: int, a5: int, a6: int, t: Tok, ao: int): AR {
  if t->k == 2 { return pgm_armlit(src, f, fs, fe, ret, pos, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, t, 1, ao); } else { }
  if t->k == 3 { return pgm_armlit(src, f, fs, fe, ret, pos, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, t, 3, ao); } else { }
  if t->k == 1 { return pgm_armpat_nm(src, f, fs, fe, ret, pos, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, t, ao); } else { }
  return AR(p: pos, d: derr(7, f, t->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6);
}
fn pgm_armpat_nm(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, be: int, end: int, depth: int, blk: int, brk: int, st: list<int,24>, a0: int, a1: int, a4: int, a5: int, a6: int, t: Tok, ao: int): AR {
  if t->l == 1 { if tok_byte(src, t->s) == 95 { return pgm_armbody(src, f, fs, fe, ret, t->p, be, end, depth, blk, brk, st, a0, a1, a4, a5, 1, ao); } else { } } else { }
  if t->l == 4 { if beq(src, t->s, "true", 0, 4) { return pgm_armbool(src, f, fs, fe, ret, pos, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, t, 4, ao); } else { } } else { }
  if t->l == 5 { if beq(src, t->s, "false", 0, 5) { return pgm_armbool(src, f, fs, fe, ret, pos, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, t, 5, ao); } else { } } else { }
  if t->l == 2 { if beq(src, t->s, "ok", 0, 2) { return pgm_armctor(src, f, fs, fe, ret, pos, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, t, 2, ao); } else { } } else { }
  if t->l == 3 { if beq(src, t->s, "err", 0, 3) { return pgm_armctor(src, f, fs, fe, ret, pos, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, t, 3, ao); } else { } } else { }
  if pgm_is_kw(src, t->s, t->l) { return AR(p: pos, d: derr(7, f, t->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); } else { }
  return pgm_armbare(src, f, fs, fe, ret, pos, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, t, ao);
}
fn pgm_armlit(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, be: int, end: int, depth: int, blk: int, brk: int, st: list<int,24>, a0: int, a1: int, a4: int, a5: int, a6: int, t: Tok, kind: int, ao: int): AR {
  if tbase(st) == 1 { if kind == 1 { return pgm_armlit_ok(src, f, fs, fe, ret, pos, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, t, ao); } else { } } else { }
  if tbase(st) == 3 { if kind == 3 { return pgm_armlit_ok(src, f, fs, fe, ret, pos, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, t, ao); } else { } } else { }
  return AR(p: pos, d: derr(11, f, t->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6);
}
fn pgm_armlit_ok(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, be: int, end: int, depth: int, blk: int, brk: int, st: list<int,24>, a0: int, a1: int, a4: int, a5: int, a6: int, t: Tok, ao: int): AR {
  if pgm_armlit_dup(src, ao, t->s, t) == 1 { return AR(p: pos, d: derr(10, f, t->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); } else { }
  return pgm_armbody(src, f, fs, fe, ret, t->p, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, ao);
}
fn pgm_armbool(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, be: int, end: int, depth: int, blk: int, brk: int, st: list<int,24>, a0: int, a1: int, a4: int, a5: int, a6: int, t: Tok, which: int, ao: int): AR {
  if tbase(st) == 2 { } else { return AR(p: pos, d: derr(11, f, t->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); }
  if pgm_armlit_dup(src, ao, t->s, t) == 1 { return AR(p: pos, d: derr(10, f, t->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); } else { }
  if which == 4 { return pgm_armbody(src, f, fs, fe, ret, t->p, be, end, depth, blk, brk, st, a0, a1, 1, a5, a6, ao); } else { }
  return pgm_armbody(src, f, fs, fe, ret, t->p, be, end, depth, blk, brk, st, a0, a1, a4, 1, a6, ao);
}
fn pgm_armctor(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, be: int, end: int, depth: int, blk: int, brk: int, st: list<int,24>, a0: int, a1: int, a4: int, a5: int, a6: int, t: Tok, kind: int, ao: int): AR {
  if tbase(st) == 6 { } else { return AR(p: pos, d: derr(11, f, t->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); }
  let nx: Tok = next_tok(src, tl_skip_ws(src, t->p, be));
  if nx->k == 4 { if nx->l == 1 { if tok_byte(src, nx->s) == 40 { return pgm_ctorpay(src, f, fs, fe, ret, pos, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, t, kind, nx, ao); } else { } } else { } } else { }
  return pgm_armbare(src, f, fs, fe, ret, pos, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, t, ao);
}
fn pgm_ctorpay(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, be: int, end: int, depth: int, blk: int, brk: int, st: list<int,24>, a0: int, a1: int, a4: int, a5: int, a6: int, t: Tok, kind: int, nx: Tok, ao: int): AR {
  let nm: Tok = next_tok(src, nx->p);
  if nm->k == 1 { if nm->l == 1 { if tok_byte(src, nm->s) == 95 { return pgm_ctorpay_wild(src, f, fs, fe, ret, nm->p, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, t, kind, ao); } else { } } else { } } else { }
  if nm->k == 2 { return pgm_ctorpay_lit(src, f, fs, fe, ret, nm->p, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, t, kind, nm, 1, ao); } else { }
  if nm->k == 3 { return pgm_ctorpay_lit(src, f, fs, fe, ret, nm->p, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, t, kind, nm, 3, ao); } else { }
  if nm->k == 1 { return pgm_ctorpay_bind(src, f, fs, fe, ret, nm->p, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, t, kind, nm, ao); } else { }
  return AR(p: pos, d: derr(7, f, nm->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6);
}
fn pgm_ctorpay_wild(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, be: int, end: int, depth: int, blk: int, brk: int, st: list<int,24>, a0: int, a1: int, a4: int, a5: int, a6: int, t: Tok, kind: int, ao: int): AR {
  if pgm_dupctor(a0, a1, kind) == 1 { return AR(p: t->s, d: derr(10, f, t->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); } else { }
  let c: Tok = next_tok(src, pos);
  if c->k == 4 { if c->l == 1 { if tok_byte(src, c->s) == 41 { return pgm_armbody(src, f, fs, fe, ret, c->p, be, end, depth, blk, brk, st, pgm_a0(a0, kind), pgm_a1(a1, kind), a4, a5, a6, ao); } else { } } else { } } else { }
  return AR(p: t->s, d: derr(6, f, c->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6);
}
fn pgm_dupctor(a0: int, a1: int, kind: int): int {
  if kind == 2 { if a0 == 1 { return 1; } else { } } else { }
  if kind == 3 { if a1 == 1 { return 1; } else { } } else { }
  return 0;
}
fn pgm_a0(a0: int, kind: int): int {
  if kind == 2 { return 1; } else { }
  return a0;
}
fn pgm_a1(a1: int, kind: int): int {
  if kind == 3 { return 1; } else { }
  return a1;
}
fn pgm_ctorpay_lit(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, be: int, end: int, depth: int, blk: int, brk: int, st: list<int,24>, a0: int, a1: int, a4: int, a5: int, a6: int, t: Tok, kind: int, nm: Tok, lk: int, ao: int): AR {
  if pgm_dupctor(a0, a1, kind) == 1 { return AR(p: t->s, d: derr(10, f, t->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); } else { }
  let pt: list<int,24> = tsub(st, 1, []);
  if lk == 1 { if tbase(pt) == 1 { } else { return AR(p: t->s, d: derr(11, f, nm->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); } } else { }
  if lk == 3 { if tbase(pt) == 3 { } else { return AR(p: t->s, d: derr(11, f, nm->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); } } else { }
  let c: Tok = next_tok(src, pos);
  if c->k == 4 { if c->l == 1 { if tok_byte(src, c->s) == 41 { return pgm_armbody(src, f, fs, fe, ret, c->p, be, end, depth, blk, brk, st, pgm_a0(a0, kind), pgm_a1(a1, kind), a4, a5, a6, ao); } else { } } else { } } else { }
  return AR(p: t->s, d: derr(6, f, c->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6);
}
fn pgm_ctorpay_bind(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, be: int, end: int, depth: int, blk: int, brk: int, st: list<int,24>, a0: int, a1: int, a4: int, a5: int, a6: int, t: Tok, kind: int, nm: Tok, ao: int): AR {
  if pgm_dupctor(a0, a1, kind) == 1 { return AR(p: t->s, d: derr(10, f, t->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); } else { }
  if pgm_is_kw(src, nm->s, nm->l) { return AR(p: t->s, d: derr(7, f, nm->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); } else { }
  let bd: D = pgm_bind_dup(src, f, fs, fe, t->s, nm);
  if has_err(bd) { return AR(p: t->s, d: bd, a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); } else { }
  let c: Tok = next_tok(src, pos);
  if c->k == 4 { if c->l == 1 { if tok_byte(src, c->s) == 41 { return pgm_armbody(src, f, fs, fe, ret, c->p, be, end, depth, blk, brk, st, pgm_a0(a0, kind), pgm_a1(a1, kind), a4, a5, a6, ao); } else { } } else { } } else { }
  return AR(p: t->s, d: derr(6, f, c->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6);
}
fn pgm_armlit_dup(src: str, ao: int, stop: int, t: Tok): int {
  return pgm_ardup_at(src, ao, stop, t->s, t->l);
}
fn pgm_ardup_at(src: str, pos: int, stop: int, ns: int, nl: int): int {
  if pos >= stop { return 0; } else { }
  let t: Tok = next_tok(src, pos);
  if t->s >= stop { return 0; } else { }
  if t->k == 0 - 2 { return pgm_ardup_at(src, t->p, stop, ns, nl); } else { }
  if t->k == 0 { return 0; } else { }
  if t->k == 4 { return pgm_ardup_br(src, t->p, stop, ns, nl, t); } else { }
  if t->k == 2 { if t->l == nl { if beq(src, t->s, src, ns, nl) { return 1; } else { } } else { } } else { }
  if t->k == 3 { if t->l == nl { if beq(src, t->s, src, ns, nl) { return 1; } else { } } else { } } else { }
  if t->k == 1 { return pgm_ardup_nm(src, t->p, stop, ns, nl, t); } else { }
  return pgm_ardup_at(src, t->p, stop, ns, nl);
}
fn pgm_ardup_br(src: str, pos: int, stop: int, ns: int, nl: int, t: Tok): int {
  if t->l == 1 { if tok_byte(src, t->s) == 123 { return pgm_ardup_at(src, t->p, stop, ns, nl); } else { } } else { }
  return pgm_ardup_at(src, t->p, stop, ns, nl);
}
fn pgm_ardup_nm(src: str, pos: int, stop: int, ns: int, nl: int, t: Tok): int {
  if t->l == 4 { if beq(src, t->s, "true", 0, 4) { if nl == 4 { if beq(src, ns, "true", 0, 4) { return 1; } else { } } else { } } else { } } else { }
  if t->l == 5 { if beq(src, t->s, "false", 0, 5) { if nl == 5 { if beq(src, ns, "false", 0, 5) { return 1; } else { } } else { } } else { } } else { }
  return pgm_ardup_body(src, pos, stop, ns, nl);
}
fn pgm_ardup_body(src: str, pos: int, stop: int, ns: int, nl: int): int {
  let bo: int = pgm_armbody_bo_pos(src, pos, stop);
  if bo == 0 - 1 { return pgm_ardup_at(src, pos, stop, ns, nl); } else { }
  let be: int = tl_balanced(src, bo, stop, 0);
  return pgm_ardup_at(src, be, stop, ns, nl);
}
fn pgm_armbody_bo_pos(src: str, pos: int, stop: int): int {
  let e: Tok = next_tok(src, pos);
  if e->s >= stop { return 0 - 1; } else { }
  if e->k == 0 - 2 { return pgm_armbody_bo_pos(src, e->p, stop); } else { }
  if e->k == 0 { return 0 - 1; } else { }
  if e->k == 4 { if e->l == 1 { if tok_byte(src, e->s) == 61 { return pgm_armbody_gt_pos(src, e->p, stop, e); } else { } } else { } } else { }
  return pgm_armbody_bo_pos(src, e->p, stop);
}
fn pgm_armbody_gt_pos(src: str, pos: int, stop: int, e: Tok): int {
  let g: Tok = next_tok(src, pos);
  if g->k == 4 { if g->l == 1 { if tok_byte(src, g->s) == 62 { if e->s + 1 == g->s { return pgm_armbody_ob_pos(src, g->p, stop); } else { } } else { } } else { } } else { }
  return 0 - 1;
}
fn pgm_armbody_ob_pos(src: str, pos: int, stop: int): int {
  let b: Tok = next_tok(src, pos);
  if b->k == 4 { if b->l == 1 { if tok_byte(src, b->s) == 123 { return b->s; } else { } } else { } } else { }
  return 0 - 1;
}
fn pgm_bind_dup(src: str, f: int, fs: int, fe: int, pat: int, nm: Tok): D {
  let v: VS = res_var(src, f, fs, fe, nm->s, nm->s, nm->l);
  if v->off == 0 - 1 { } else { return derr(10, f, pat); }
  if v->d->c == 10 { return derr(10, f, pat); } else { }
  let fr: FR = res_fn(src, f, nm->s, nm->l, nm->s);
  if fr->i == 0 - 1 { } else { return derr(10, f, pat); }
  if pgm_is_fnreserved(src, nm->s, nm->l) { return derr(10, f, pat); } else { }
  return dok();
}
fn pgm_armbare(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, be: int, end: int, depth: int, blk: int, brk: int, st: list<int,24>, a0: int, a1: int, a4: int, a5: int, a6: int, t: Tok, ao: int): AR {
  let bd: D = pgm_bind_dup(src, f, fs, fe, t->s, t);
  if has_err(bd) { return AR(p: t->s, d: bd, a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); } else { }
  return pgm_armbody(src, f, fs, fe, ret, t->p, be, end, depth, blk, brk, st, a0, a1, a4, a5, 1, ao);
}
fn pgm_armbody(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, be: int, end: int, depth: int, blk: int, brk: int, st: list<int,24>, a0: int, a1: int, a4: int, a5: int, a6: int, ao: int): AR {
  let e: Tok = next_tok(src, tl_skip_ws(src, pos, be));
  if e->k == 4 { if e->l == 1 { if tok_byte(src, e->s) == 61 { return pgm_armbody_gt(src, f, fs, fe, ret, e->p, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, ao); } else { } } else { } } else { }
  return AR(p: pos, d: derr(6, f, e->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6);
}
fn pgm_armbody_gt(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, be: int, end: int, depth: int, blk: int, brk: int, st: list<int,24>, a0: int, a1: int, a4: int, a5: int, a6: int, ao: int): AR {
  let g: Tok = next_tok(src, pos);
  if g->k == 4 { if g->l == 1 { if tok_byte(src, g->s) == 62 { return pgm_armbody_bo(src, f, fs, fe, ret, pos, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, g, ao); } else { } } else { } } else { }
  return AR(p: pos, d: derr(6, f, g->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6);
}
fn pgm_armbody_bo(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, be: int, end: int, depth: int, blk: int, brk: int, st: list<int,24>, a0: int, a1: int, a4: int, a5: int, a6: int, g: Tok, ao: int): AR {
  if g->s == pos { } else { return AR(p: pos, d: derr(6, f, g->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); }
  let bo: Tok = pgm_tok(src, g->p, end);
  if pgm_is_obrace(src, bo) { } else { return AR(p: pos, d: derr(6, f, bo->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); }
  let bn: int = pgm_brace_end(src, bo->s, end);
  if bn == 0 - 1 { return AR(p: pos, d: derr(8, f, end), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); } else { }
  let d: D = pgm_block(src, f, fs, fe, ret, bo->p, bn, depth + 1, blk + 1, brk);
  if has_err(d) { return AR(p: pos, d: d, a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); } else { }
  return pgm_arm_next(src, f, fs, fe, ret, bn, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, ao);
}
fn pgm_arm_next(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, be: int, end: int, depth: int, blk: int, brk: int, st: list<int,24>, a0: int, a1: int, a4: int, a5: int, a6: int, ao: int): AR {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return AR(p: pos, d: derr(8, f, end), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); } else { }
  if pgm_is_comma(src, t) { return pgm_arm_after(src, f, fs, fe, ret, t->p, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, ao); } else { }
  if pgm_is_cbrace(src, t) { return AR(p: t->s, d: dok(), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); } else { }
  return AR(p: pos, d: derr(6, f, t->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6);
}
fn pgm_arm_after(src: str, f: int, fs: int, fe: int, ret: list<int,24>, pos: int, be: int, end: int, depth: int, blk: int, brk: int, st: list<int,24>, a0: int, a1: int, a4: int, a5: int, a6: int, ao: int): AR {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 125 { return AR(p: pos, d: derr(6, f, t->s), a0: a0, a1: a1, a4: a4, a5: a5, a6: a6); } else { } } else { } } else { }
  return pgm_arm(src, f, fs, fe, ret, pos, be, end, depth, blk, brk, st, a0, a1, a4, a5, a6, ao);
}
fn pgm_ldup(src: str, lp: int, stop: int, ns: int, nl: int): int {
  return pgm_ldup_at(src, lp, stop, ns, nl, 0);
}
fn pgm_ldup_at(src: str, pos: int, stop: int, ns: int, nl: int, pd: int): int {
  if pos >= stop { return 0; } else { }
  let t: Tok = next_tok(src, pos);
  if t->s >= stop { return 0; } else { }
  if t->k == 0 - 2 { return pgm_ldup_at(src, t->p, stop, ns, nl, pd); } else { }
  if t->k == 0 { return 0; } else { }
  if t->k == 4 { return pgm_ldup_br(src, t->p, stop, ns, nl, pd, t); } else { }
  if t->k == 1 { if pd == 0 { return pgm_ldup_nm(src, t, stop, ns, nl, pd); } else { } } else { }
  return pgm_ldup_at(src, t->p, stop, ns, nl, pd);
}
fn pgm_ldup_br(src: str, pos: int, stop: int, ns: int, nl: int, pd: int, t: Tok): int {
  if t->l == 1 { if tok_byte(src, t->s) == 40 { return pgm_ldup_at(src, t->p, stop, ns, nl, pd + 1); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 91 { return pgm_ldup_at(src, t->p, stop, ns, nl, pd + 1); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 41 { return pgm_ldup_dn(src, t->p, stop, ns, nl, pd); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 93 { return pgm_ldup_dn(src, t->p, stop, ns, nl, pd); } else { } } else { }
  return pgm_ldup_at(src, t->p, stop, ns, nl, pd);
}
fn pgm_ldup_dn(src: str, pos: int, stop: int, ns: int, nl: int, pd: int): int {
  if pd == 0 { return pgm_ldup_at(src, pos, stop, ns, nl, 0); } else { }
  return pgm_ldup_at(src, pos, stop, ns, nl, pd - 1);
}
fn pgm_ldup_nm(src: str, t: Tok, stop: int, ns: int, nl: int, pd: int): int {
  if t->l == nl { if beq(src, t->s, src, ns, nl) { return pgm_ldup_colon(src, t, stop, ns, nl, pd); } else { } } else { }
  return pgm_ldup_at(src, t->p, stop, ns, nl, pd);
}
fn pgm_ldup_colon(src: str, t: Tok, stop: int, ns: int, nl: int, pd: int): int {
  let cn: Tok = next_tok(src, t->p);
  if pgm_is_colon(src, cn) { return 1; } else { }
  return pgm_ldup_at(src, t->p, stop, ns, nl, pd);
}
fn pgm_tok(src: str, pos: int, ce: int): Tok {
  if pos >= ce { return Tok(k: 0, s: ce, l: 0, p: ce); } else { }
  return next_tok(src, pos);
}
fn pgm_body_open(src: str, pos: int, end: int): int {
  let t: Tok = next_tok(src, pos);
  if t->k == 0 - 2 { return pgm_body_open(src, t->p, end); } else { }
  if t->s >= end { return 0 - 1; } else { }
  if t->k == 0 { return 0 - 1; } else { }
  if pgm_is_obrace(src, t) { return t->s; } else { }
  if t->p <= pos { return 0 - 1; } else { }
  return pgm_body_open(src, t->p, end);
}
fn pgm_body_closed(src: str, open: int, end: int): int {
  return pgm_brace(src, open + 1, end, 1);
}
fn pgm_brace(src: str, pos: int, end: int, depth: int): int {
  if pos >= end { return 0; } else { }
  let b: int = tok_byte(src, pos);
  if b == 123 { return pgm_brace(src, pos + 1, end, depth + 1); } else { }
  if b == 125 { if depth == 1 { return 1; } else { } return pgm_brace(src, pos + 1, end, depth - 1); } else { }
  if b == 34 { return pgm_brace(src, tl_skip_str(src, pos + 1, end), end, depth); } else { }
  if b == 47 { return pgm_brace_slash(src, pos, end, depth); } else { }
  return pgm_brace(src, pos + 1, end, depth);
}
fn pgm_brace_slash(src: str, pos: int, end: int, depth: int): int {
  if tok_byte(src, pos + 1) == 47 { return pgm_brace(src, tl_skip_line(src, pos + 2, end), end, depth); } else { }
  return pgm_brace(src, pos + 1, end, depth);
}
fn pgm_shape(src: str, f: int, pos: int, end: int): D {
  let it: TI = tl_next(src, f, pos, end);
  if it->k == 0 { return dok(); } else { }
  if it->k == 0 - 1 { return derr(7, f, it->s); } else { }
  if it->k == 1 { return pgm_sfn_item(src, f, it, end); } else { }
  if it->k == 2 { return pgm_srec_item(src, f, it, end); } else { }
  if it->k == 3 { return pgm_suse_item(src, f, it, end); } else { }
  return pgm_shape(src, f, it->p, end);
}
fn pgm_sfn_item(src: str, f: int, it: TI, end: int): D {
  let d: D = pgm_sfn(src, f, it->s, it->s + it->l);
  if has_err(d) { return d; } else { }
  return pgm_shape(src, f, it->p, end);
}
fn pgm_srec_item(src: str, f: int, it: TI, end: int): D {
  let d: D = pgm_srec(src, f, it->s, it->s + it->l);
  if has_err(d) { return d; } else { }
  return pgm_shape(src, f, it->p, end);
}
fn pgm_suse_item(src: str, f: int, it: TI, end: int): D {
  let ce: int = it->s + it->l;
  if ce < len(src) { return pgm_shape(src, f, it->p, end); } else { }
  if tok_byte(src, ce - 1) == 59 { return pgm_shape(src, f, it->p, end); } else { }
  return derr(8, f, ce);
}
fn pgm_sfn(src: str, f: int, cs: int, ce: int): D {
  let kw: Tok = next_tok(src, cs);
  let nm: Tok = next_tok(src, kw->p);
  if nm->k == 1 { } else { return derr(6, f, nm->s); }
  if pgm_is_kw(src, nm->s, nm->l) { return derr(6, f, nm->s); } else { }
  let lp: Tok = next_tok(src, nm->p);
  if pgm_is_lparen(src, lp) { } else { return derr(6, f, lp->s); }
  let bo: int = pgm_body_open(src, lp->p, ce);
  if bo == 0 - 1 { return derr(8, f, ce); } else { }
  let dp: D = pgm_sparams(src, f, lp->p, bo);
  if has_err(dp) { return dp; } else { }
  let dr: D = pgm_sret(src, f, lp->p, bo);
  if has_err(dr) { return dr; } else { }
  if pgm_body_closed(src, bo, ce) == 0 { return derr(8, f, ce); } else { }
  return dok();
}
fn pgm_sparams(src: str, f: int, pos: int, bo: int): D {
  let t: Tok = next_tok(src, pos);
  if t->s >= bo { return derr(6, f, bo); } else { }
  if pgm_is_close(src, t) { return dok(); } else { }
  if t->k == 1 { return pgm_sparam(src, f, t, bo); } else { }
  return derr(6, f, t->s);
}
fn pgm_sparam_next(src: str, f: int, pos: int, bo: int): D {
  let t: Tok = next_tok(src, pos);
  if t->s >= bo { return derr(6, f, bo); } else { }
  if t->k == 1 { return pgm_sparam(src, f, t, bo); } else { }
  return derr(6, f, t->s);
}
fn pgm_sparam(src: str, f: int, t: Tok, bo: int): D {
  if pgm_is_kw(src, t->s, t->l) { return derr(6, f, t->s); } else { }
  let cn: Tok = next_tok(src, t->p);
  if pgm_is_colon(src, cn) { } else { return derr(6, f, cn->s); }
  let ts: Tok = next_tok(src, cn->p);
  if ts->k == 1 { } else { return derr(6, f, ts->s); }
  let e: int = pgm_tyend(src, ts->s, bo, 0);
  if e == 0 - 2 { return derr(14, f, ts->s); } else { }
  if e < 0 { return derr(6, f, 0 - e - 100); } else { }
  let nx: Tok = next_tok(src, e);
  if nx->s >= bo { return derr(6, f, bo); } else { }
  if pgm_is_comma(src, nx) { return pgm_sparam_next(src, f, nx->p, bo); } else { }
  if pgm_is_close(src, nx) { return dok(); } else { }
  return derr(6, f, nx->s);
}
fn pgm_close_paren(src: str, pos: int, bo: int): int {
  return pgm_cparen_at(src, pos, bo, 0);
}
fn pgm_cparen_at(src: str, pos: int, bo: int, depth: int): int {
  if pos >= bo { return 0 - 1; } else { }
  let t: Tok = next_tok(src, pos);
  if t->s >= bo { return 0 - 1; } else { }
  if t->k == 0 - 2 { return pgm_cparen_at(src, t->p, bo, depth); } else { }
  if t->k == 4 { return pgm_cparen_op(src, t->p, bo, depth, t); } else { }
  return pgm_cparen_at(src, t->p, bo, depth);
}
fn pgm_cparen_op(src: str, pos: int, bo: int, depth: int, t: Tok): int {
  if t->l == 1 { if tok_byte(src, t->s) == 40 { return pgm_cparen_at(src, t->p, bo, depth + 1); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 41 { if depth == 0 { return t->s; } else { } return pgm_cparen_at(src, t->p, bo, depth - 1); } else { } } else { }
  return pgm_cparen_at(src, t->p, bo, depth);
}
fn pgm_sret(src: str, f: int, pos: int, bo: int): D {
  let cp: int = pgm_close_paren(src, pos, bo);
  if cp == 0 - 1 { return derr(6, f, bo); } else { }
  let t: Tok = next_tok(src, cp + 1);
  if pgm_is_obrace(src, t) { return dok(); } else { }
  if pgm_is_colon(src, t) { return pgm_sret_ty(src, f, t->p, bo); } else { }
  return derr(6, f, t->s);
}
fn pgm_sret_ty(src: str, f: int, pos: int, bo: int): D {
  let ts: Tok = next_tok(src, pos);
  if ts->k == 1 { } else { return derr(6, f, ts->s); }
  let e: int = pgm_tyend(src, ts->s, bo, 0);
  if e == 0 - 2 { return derr(14, f, ts->s); } else { }
  if e < 0 { return derr(6, f, 0 - e - 100); } else { }
  let nx: Tok = next_tok(src, e);
  if pgm_is_obrace(src, nx) { return dok(); } else { }
  return derr(6, f, nx->s);
}
fn pgm_srec(src: str, f: int, cs: int, ce: int): D {
  let kw: Tok = next_tok(src, cs);
  let nm: Tok = next_tok(src, kw->p);
  if nm->k == 1 { } else { return derr(6, f, nm->s); }
  if pgm_is_kw(src, nm->s, nm->l) { return derr(6, f, nm->s); } else { }
  let br: Tok = next_tok(src, nm->p);
  if pgm_is_obrace(src, br) { } else { return derr(6, f, br->s); }
  return pgm_sfields(src, f, br->p, ce);
}
fn pgm_sfields(src: str, f: int, pos: int, ce: int): D {
  let t: Tok = pgm_tok(src, pos, ce);
  if t->k == 0 { return derr(8, f, ce); } else { }
  if pgm_is_cbrace(src, t) { return dok(); } else { }
  if t->k == 1 { return pgm_sfield(src, f, t, ce); } else { }
  return derr(6, f, t->s);
}
fn pgm_sfield_next(src: str, f: int, pos: int, ce: int): D {
  let t: Tok = pgm_tok(src, pos, ce);
  if t->k == 0 { return derr(8, f, ce); } else { }
  if t->k == 1 { return pgm_sfield(src, f, t, ce); } else { }
  return derr(6, f, t->s);
}
fn pgm_sfield(src: str, f: int, t: Tok, ce: int): D {
  if pgm_is_kw(src, t->s, t->l) { return derr(6, f, t->s); } else { }
  let cn: Tok = pgm_tok(src, t->p, ce);
  if cn->k == 0 { return derr(8, f, ce); } else { }
  if pgm_is_colon(src, cn) { } else { return derr(6, f, cn->s); }
  let ts: Tok = pgm_tok(src, cn->p, ce);
  if ts->k == 0 { return derr(8, f, ce); } else { }
  if ts->k == 1 { } else { return derr(6, f, ts->s); }
  let e: int = pgm_tyend(src, ts->s, ce, 0);
  if e == 0 - 2 { return derr(14, f, ts->s); } else { }
  if e < 0 { return derr(6, f, 0 - e - 100); } else { }
  let nx: Tok = pgm_tok(src, e, ce);
  if nx->k == 0 { return derr(8, f, ce); } else { }
  if pgm_is_comma(src, nx) { return pgm_sfield_next(src, f, nx->p, ce); } else { }
  if pgm_is_cbrace(src, nx) { return dok(); } else { }
  return derr(6, f, nx->s);
}
fn pgm_tytok(src: str, pos: int, end: int): Tok {
  if pos >= end { return Tok(k: 0, s: end, l: 0, p: end); } else { }
  let t: Tok = next_tok(src, pos);
  if t->s >= end { return Tok(k: 0, s: end, l: 0, p: end); } else { }
  return t;
}
fn pgm_tyend(src: str, pos: int, end: int, depth: int): int {
  if depth > 8 { return 0 - 2; } else { }
  let t: Tok = pgm_tytok(src, pos, end);
  if t->k == 0 { return 0 - (t->s + 100); } else { }
  if t->k == 0 - 2 { return 0 - (t->s + 100); } else { }
  if t->k == 1 { return pgm_tyend_nm(src, t, end, depth); } else { }
  return 0 - (t->s + 100);
}
fn pgm_tyend_nm(src: str, t: Tok, end: int, depth: int): int {
  if t->l == 3 { if beq(src, t->s, "int", 0, 3) { return t->p; } else { } } else { }
  if t->l == 4 { if beq(src, t->s, "bool", 0, 4) { return t->p; } else { } } else { }
  if t->l == 3 { if beq(src, t->s, "str", 0, 3) { return t->p; } else { } } else { }
  if t->l == 4 { if beq(src, t->s, "list", 0, 4) { return pgm_tyend_list(src, t->p, end, depth); } else { } } else { }
  if t->l == 6 { if beq(src, t->s, "status", 0, 6) { return pgm_tyend_status(src, t->p, end, depth); } else { } } else { }
  if t->l == 6 { if beq(src, t->s, "result", 0, 6) { return pgm_tyend_result(src, t->p, end, depth); } else { } } else { }
  if t->l == 3 { if beq(src, t->s, "map", 0, 3) { return pgm_tyend_map(src, t->p, end, depth); } else { } } else { }
  return pgm_tyend_qual(src, t->p, end);
}
fn pgm_tyend_qual(src: str, pos: int, end: int): int {
  let t: Tok = pgm_tytok(src, pos, end);
  if t->k == 4 { if t->l == 2 { if tok_byte(src, t->s) == 58 { if tok_byte(src, t->s + 1) == 58 { return pgm_tyend_qnm(src, t->p, end); } else { } } else { } } else { } } else { }
  return pos;
}
fn pgm_tyend_qnm(src: str, pos: int, end: int): int {
  let t: Tok = pgm_tytok(src, pos, end);
  if t->k == 1 { return pgm_tyend_qual(src, t->p, end); } else { }
  return 0 - (t->s + 100);
}
fn pgm_tyend_gt(src: str, pos: int, end: int): int {
  let g: Tok = pgm_tytok(src, pos, end);
  if g->k == 4 { if tok_byte(src, g->s) == 62 { if g->l == 1 { return g->p; } else { } if g->l == 2 { return g->s + 1; } else { } } else { } } else { }
  return 0 - (g->s + 100);
}
fn pgm_tyend_comma(src: str, pos: int, end: int): int {
  let c: Tok = pgm_tytok(src, pos, end);
  if pgm_is_comma(src, c) { return c->p; } else { }
  return 0 - (c->s + 100);
}
fn pgm_tyend_int(src: str, pos: int, end: int): int {
  let n: Tok = pgm_tytok(src, pos, end);
  if n->k == 2 { return n->p; } else { }
  return 0 - (n->s + 100);
}
fn pgm_tyend_list(src: str, pos: int, end: int, depth: int): int {
  let o: Tok = pgm_tytok(src, pos, end);
  if pgm_is_lt(src, o) { } else { return 0 - (o->s + 100); }
  let e: int = pgm_tyend(src, o->p, end, depth + 1);
  if e < 0 { return e; } else { }
  let c: int = pgm_tyend_comma(src, e, end);
  if c < 0 { return c; } else { }
  let n: int = pgm_tyend_int(src, c, end);
  if n < 0 { return n; } else { }
  return pgm_tyend_gt(src, n, end);
}
fn pgm_tyend_status(src: str, pos: int, end: int, depth: int): int {
  let o: Tok = pgm_tytok(src, pos, end);
  if pgm_is_lt(src, o) { } else { return 0 - (o->s + 100); }
  let e: int = pgm_tyend(src, o->p, end, depth + 1);
  if e < 0 { return e; } else { }
  return pgm_tyend_gt(src, e, end);
}
fn pgm_tyend_map(src: str, pos: int, end: int, depth: int): int {
  let o: Tok = pgm_tytok(src, pos, end);
  if pgm_is_lt(src, o) { } else { return 0 - (o->s + 100); }
  let e1: int = pgm_tyend(src, o->p, end, depth + 1);
  if e1 < 0 { return e1; } else { }
  let c1: int = pgm_tyend_comma(src, e1, end);
  if c1 < 0 { return c1; } else { }
  let e2: int = pgm_tyend(src, c1, end, depth + 1);
  if e2 < 0 { return e2; } else { }
  let c2: int = pgm_tyend_comma(src, e2, end);
  if c2 < 0 { return c2; } else { }
  let n: int = pgm_tyend_int(src, c2, end);
  if n < 0 { return n; } else { }
  return pgm_tyend_gt(src, n, end);
}
fn pgm_tyend_result(src: str, pos: int, end: int, depth: int): int {
  let o: Tok = pgm_tytok(src, pos, end);
  if pgm_is_lt(src, o) { } else { return 0 - (o->s + 100); }
  let e1: int = pgm_tyend(src, o->p, end, depth + 1);
  if e1 < 0 { return e1; } else { }
  let c1: int = pgm_tyend_comma(src, e1, end);
  if c1 < 0 { return c1; } else { }
  let e2: int = pgm_tyend(src, c1, end, depth + 1);
  if e2 < 0 { return e2; } else { }
  return pgm_tyend_gt(src, e2, end);
}
fn pgm_collect(src: str, f: int, pos: int, end: int, best: int): int {
  let it: TI = tl_next(src, f, pos, end);
  if it->k == 0 { return best; } else { }
  if it->k == 1 { return pgm_collect_fn(src, f, it, end, best); } else { }
  if it->k == 2 { return pgm_collect_rec(src, f, it, end, best); } else { }
  return pgm_collect(src, f, it->p, end, best);
}
fn pgm_collect_fn(src: str, f: int, it: TI, end: int, best: int): int {
  let nm: Tok = next_tok(src, next_tok(src, it->s)->p);
  let b0: int = pgm_cfn_res(src, nm, it, best);
  let b1: int = pgm_cfn_dup2(src, f, b0, it, nm);
  return pgm_collect(src, f, it->p, end, b1);
}
fn pgm_cfn_dup2(src: str, f: int, b0: int, it: TI, nm: Tok): int {
  if nm->k == 1 { if pgm_earlier_dup(src, f, it->s, nm->s, nm->l) == 1 { return pgm_min(b0, it->s); } else { } } else { }
  return b0;
}
fn pgm_cfn_res(src: str, nm: Tok, it: TI, best: int): int {
  if nm->k == 1 { if pgm_is_fnreserved(src, nm->s, nm->l) { return pgm_min(best, it->s); } else { } } else { }
  return best;
}
fn pgm_collect_rec(src: str, f: int, it: TI, end: int, best: int): int {
  let nm: Tok = next_tok(src, next_tok(src, it->s)->p);
  let b0: int = pgm_crec_res(src, nm, it, best);
  let b1: int = pgm_cfn_dup2(src, f, b0, it, nm);
  return pgm_collect(src, f, it->p, end, b1);
}
fn pgm_crec_res(src: str, nm: Tok, it: TI, best: int): int {
  if nm->k == 1 { if pgm_is_typreserved(src, nm->s, nm->l) { return pgm_min(best, it->s); } else { } } else { }
  return best;
}
fn pgm_heads(src: str, f: int, pos: int, end: int): D {
  let it: TI = tl_next(src, f, pos, end);
  if it->k == 0 { return dok(); } else { }
  if it->k == 1 { return pgm_fn_item(src, f, it, end); } else { }
  if it->k == 2 { return pgm_rec_item(src, f, it, end); } else { }
  return pgm_heads(src, f, it->p, end);
}
fn pgm_fn_item(src: str, f: int, it: TI, end: int): D {
  let d: D = pgm_fn(src, f, it->s, it->s + it->l);
  if has_err(d) { return d; } else { }
  return pgm_heads(src, f, it->p, end);
}
fn pgm_rec_item(src: str, f: int, it: TI, end: int): D {
  let d: D = pgm_rec(src, f, it->s, it->s + it->l);
  if has_err(d) { return d; } else { }
  return pgm_heads(src, f, it->p, end);
}
fn pgm_tyerr(src: str, f: int, ty: TR, ts: int): D {
  if has_err(ty->d) { } else { return dok(); }
  if ty->d->c == 9 { return derr(9, f, ts); } else { }
  if ty->d->c == 11 { return derr(11, f, ts); } else { }
  if ty->d->c == 14 { return derr(14, f, ts); } else { }
  if ty->d->c == 15 { return derr(15, f, ts); } else { }
  return ty->d;
}
fn pgm_fn(src: str, f: int, cs: int, ce: int): D {
  let kw: Tok = next_tok(src, cs);
  let nm: Tok = next_tok(src, kw->p);
  let lp: Tok = next_tok(src, nm->p);
  let bo: int = pgm_body_open(src, lp->p, ce);
  if bo == 0 - 1 { return derr(8, f, ce); } else { }
  let dp: D = pgm_params(src, f, lp->p, bo);
  if has_err(dp) { return dp; } else { }
  return pgm_ret(src, f, lp->p, bo);
}
fn pgm_params(src: str, f: int, pos: int, bo: int): D {
  let t: Tok = next_tok(src, pos);
  if t->s >= bo { return derr(6, f, bo); } else { }
  if pgm_is_close(src, t) { return dok(); } else { }
  if t->k == 1 { return pgm_param(src, f, pos, t, bo); } else { }
  return derr(6, f, t->s);
}
fn pgm_param_next(src: str, f: int, pstart: int, pos: int, bo: int): D {
  let t: Tok = next_tok(src, pos);
  if t->s >= bo { return derr(6, f, bo); } else { }
  if t->k == 1 { return pgm_param(src, f, pstart, t, bo); } else { }
  return derr(6, f, t->s);
}
fn pgm_param(src: str, f: int, pstart: int, t: Tok, bo: int): D {
  if pgm_is_kw(src, t->s, t->l) { return derr(6, f, t->s); } else { }
  if pgm_is_break(src, t) { return derr(10, f, t->s); } else { }
  if pgm_pdup(src, pstart, t->s, t->s, t->l) == 1 { return derr(10, f, t->s); } else { }
  let cn: Tok = next_tok(src, t->p);
  if pgm_is_colon(src, cn) { } else { return derr(6, f, cn->s); }
  let ts: Tok = next_tok(src, cn->p);
  if ts->k == 1 { } else { return derr(6, f, ts->s); }
  let ty: TR = intern_ty(src, f, ts->s, bo, 0);
  if has_err(ty->d) { return pgm_tyerr(src, f, ty, ts->s); } else { }
  let nx: Tok = next_tok(src, ty->p);
  if nx->s >= bo { return derr(6, f, bo); } else { }
  if pgm_is_comma(src, nx) { return pgm_param_next(src, f, pstart, nx->p, bo); } else { }
  if pgm_is_close(src, nx) { return dok(); } else { }
  return derr(6, f, nx->s);
}
fn pgm_is_break(src: str, t: Tok): bool {
  if t->l == 5 { if beq(src, t->s, "break", 0, 5) { return true; } else { } } else { }
  if t->l == 8 { if beq(src, t->s, "continue", 0, 8) { return true; } else { } } else { }
  return false;
}
fn pgm_is_typeword(src: str, t: Tok): bool {
  if t->l == 3 { if beq(src, t->s, "int", 0, 3) { return true; } else { } } else { }
  if t->l == 4 { if beq(src, t->s, "bool", 0, 4) { return true; } else { } } else { }
  if t->l == 3 { if beq(src, t->s, "str", 0, 3) { return true; } else { } } else { }
  return false;
}
fn pgm_typeword_qual(src: str, f: int, t: Tok, end: int): TR {
  let nx: Tok = pgm_tok(src, t->p, end);
  if nx->k == 4 { if nx->l == 2 { if beq(src, nx->s, "::", 0, 2) { return TR(t: tscal(1), p: t->s, d: derr(9, f, t->s)); } else { } } else { } } else { }
  return TR(t: tscal(1), p: t->s, d: derr(7, f, t->s));
}
fn pgm_skip_ws(src: str, pos: int, end: int): int {
  if pos >= end { return pos; } else { }
  let b: int = tok_byte(src, pos);
  if b == 32 { return pgm_skip_ws(src, pos + 1, end); } else { }
  if b == 9 { return pgm_skip_ws(src, pos + 1, end); } else { }
  if b == 10 { return pgm_skip_ws(src, pos + 1, end); } else { }
  if b == 13 { return pgm_skip_ws(src, pos + 1, end); } else { }
  if b == 47 { return pgm_skip_slash(src, pos, end); } else { }
  return pos;
}
fn pgm_skip_slash(src: str, pos: int, end: int): int {
  if tok_byte(src, pos + 1) == 47 { return pgm_skip_ws(src, tl_skip_line(src, pos + 2, end), end); } else { }
  return pos;
}
fn pgm_numstart(src: str, s: int): int {
  if s <= 0 { return s; } else { }
  let b: int = unwrap_or(byte_at(src, s - 1), 0);
  if b >= 48 { if b <= 57 { return pgm_numstart(src, s - 1); } else { } } else { }
  return s;
}
fn pgm_earlier_dup(src: str, f: int, selfstart: int, ns: int, nl: int): int {
  return pgm_earlier_at(src, f, 0, selfstart, ns, nl);
}
fn pgm_earlier_at(src: str, f: int, pos: int, selfstart: int, ns: int, nl: int): int {
  let it: TI = tl_next(src, f, pos, selfstart);
  if it->k == 0 { return 0; } else { }
  if it->k == 0 - 1 { return pgm_earlier_at(src, f, it->p, selfstart, ns, nl); } else { }
  if it->k == 1 { if pgm_earlier_nm(src, it, 2, ns, nl) == 1 { return 1; } else { } } else { }
  if it->k == 2 { if pgm_earlier_nm(src, it, 6, ns, nl) == 1 { return 1; } else { } } else { }
  return pgm_earlier_at(src, f, it->p, selfstart, ns, nl);
}
fn pgm_earlier_nm(src: str, it: TI, kw: int, ns: int, nl: int): int {
  let t: Tok = next_tok(src, it->s + kw);
  if t->k == 1 { if t->l == nl { if beq(src, t->s, src, ns, nl) { return 1; } else { } } else { } } else { }
  return 0;
}
fn pgm_namedshape(src: str, pos: int, end: int): int {
  let t1: Tok = pgm_tok(src, pos, end);
  if t1->k == 1 { } else { return 0; }
  let t2: Tok = pgm_tok(src, t1->p, end);
  if pgm_is_colon(src, t2) { return 1; } else { }
  return 0;
}
fn pgm_pdup(src: str, pstart: int, stop: int, ns: int, nl: int): int {
  return pgm_pdup_at(src, pstart, stop, ns, nl);
}
fn pgm_pdup_at(src: str, pos: int, stop: int, ns: int, nl: int): int {
  if pos >= stop { return 0; } else { }
  let t: Tok = next_tok(src, pos);
  if t->s >= stop { return 0; } else { }
  if t->k == 0 - 2 { return pgm_pdup_at(src, t->p, stop, ns, nl); } else { }
  if t->k == 1 { return pgm_pdup_nm(src, t, stop, ns, nl); } else { }
  return pgm_pdup_at(src, t->p, stop, ns, nl);
}
fn pgm_pdup_nm(src: str, t: Tok, stop: int, ns: int, nl: int): int {
  if t->l == nl { if beq(src, t->s, src, ns, nl) { if peek_colon(src, t->p) { return 1; } else { } } else { } } else { }
  return pgm_pdup_skip(src, t, stop, ns, nl);
}
fn pgm_pdup_skip(src: str, t: Tok, stop: int, ns: int, nl: int): int {
  let an: VS = annot_span(src, t->p);
  if an->tl == 0 { return pgm_pdup_at(src, t->p, stop, ns, nl); } else { }
  return pgm_pdup_at(src, an->ts + an->tl, stop, ns, nl);
}
fn pgm_ret(src: str, f: int, pos: int, bo: int): D {
  let cp: int = pgm_close_paren(src, pos, bo);
  if cp == 0 - 1 { return derr(6, f, bo); } else { }
  let t: Tok = next_tok(src, cp + 1);
  if pgm_is_obrace(src, t) { return dok(); } else { }
  if pgm_is_colon(src, t) { return pgm_ret_ty(src, f, t->p, bo); } else { }
  return derr(6, f, t->s);
}
fn pgm_ret_ty(src: str, f: int, pos: int, bo: int): D {
  let ts: Tok = next_tok(src, pos);
  if ts->k == 1 { } else { return derr(6, f, ts->s); }
  let ty: TR = intern_ty(src, f, ts->s, bo, 0);
  if has_err(ty->d) { return pgm_tyerr(src, f, ty, ts->s); } else { }
  let nx: Tok = next_tok(src, ty->p);
  if pgm_is_obrace(src, nx) { return dok(); } else { }
  return derr(6, f, nx->s);
}
fn pgm_rec(src: str, f: int, cs: int, ce: int): D {
  let kw: Tok = next_tok(src, cs);
  let nm: Tok = next_tok(src, kw->p);
  let br: Tok = next_tok(src, nm->p);
  if pgm_is_obrace(src, br) { } else { return derr(6, f, br->s); }
  return pgm_fields(src, f, br->p, ce);
}
fn pgm_fields(src: str, f: int, pos: int, ce: int): D {
  let t: Tok = pgm_tok(src, pos, ce);
  if t->k == 0 { return derr(8, f, ce); } else { }
  if pgm_is_cbrace(src, t) { return dok(); } else { }
  if t->k == 1 { return pgm_field(src, f, pos, t, ce); } else { }
  return derr(6, f, t->s);
}
fn pgm_field_next(src: str, f: int, fstart: int, pos: int, ce: int): D {
  let t: Tok = pgm_tok(src, pos, ce);
  if t->k == 0 { return derr(8, f, ce); } else { }
  if t->k == 1 { return pgm_field(src, f, fstart, t, ce); } else { }
  return derr(6, f, t->s);
}
fn pgm_field(src: str, f: int, fstart: int, t: Tok, ce: int): D {
  if pgm_is_kw(src, t->s, t->l) { return derr(6, f, t->s); } else { }
  if pgm_fdup(src, fstart, t->s, t->s, t->l) == 1 { return derr(10, f, t->s); } else { }
  let cn: Tok = pgm_tok(src, t->p, ce);
  if cn->k == 0 { return derr(8, f, ce); } else { }
  if pgm_is_colon(src, cn) { } else { return derr(6, f, cn->s); }
  let ts: Tok = pgm_tok(src, cn->p, ce);
  if ts->k == 0 { return derr(8, f, ce); } else { }
  if ts->k == 1 { } else { return derr(6, f, ts->s); }
  let ty: TR = intern_ty(src, f, ts->s, ce, 0);
  if has_err(ty->d) { return pgm_tyerr(src, f, ty, ts->s); } else { }
  if tbase(ty->t) == 6 { return derr(11, f, t->s); } else { }
  let nx: Tok = pgm_tok(src, ty->p, ce);
  if nx->k == 0 { return derr(8, f, ce); } else { }
  if pgm_is_comma(src, nx) { return pgm_field_next(src, f, fstart, nx->p, ce); } else { }
  if pgm_is_cbrace(src, nx) { return dok(); } else { }
  return derr(6, f, nx->s);
}
fn pgm_fdup(src: str, fstart: int, stop: int, ns: int, nl: int): int {
  return pgm_fdup_at(src, fstart, stop, ns, nl);
}
fn pgm_fdup_at(src: str, pos: int, stop: int, ns: int, nl: int): int {
  if pos >= stop { return 0; } else { }
  let t: Tok = next_tok(src, pos);
  if t->s >= stop { return 0; } else { }
  if t->k == 0 - 2 { return pgm_fdup_at(src, t->p, stop, ns, nl); } else { }
  if t->k == 1 { return pgm_fdup_nm(src, t, stop, ns, nl); } else { }
  return pgm_fdup_at(src, t->p, stop, ns, nl);
}
fn pgm_fdup_nm(src: str, t: Tok, stop: int, ns: int, nl: int): int {
  if t->l == nl { if beq(src, t->s, src, ns, nl) { if peek_colon(src, t->p) { return 1; } else { } } else { } } else { }
  return pgm_fdup_skip(src, t, stop, ns, nl);
}
fn pgm_fdup_skip(src: str, t: Tok, stop: int, ns: int, nl: int): int {
  let an: VS = annot_span(src, t->p);
  if an->tl == 0 { return pgm_fdup_at(src, t->p, stop, ns, nl); } else { }
  return pgm_fdup_at(src, an->ts + an->tl, stop, ns, nl);
}

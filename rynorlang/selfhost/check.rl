// selfhost checker: span rules over re-scanned source (zero tables).
// Names resolve by backward scans (def-before-use makes them complete);
// types are flat prefix-code lists (list<int,24>): scalar [b] (1 int,
// 2 bool, 3 str); record [4,rid]; list [5,e..,cap]; status [6,t..].
// Nesting in codes never exceeds the type walk cap (8, host-identical);
// expression nesting caps at 8 (baby-resource bound, same
// PAR_DEPTH_EXCEEDED code, DOCUMENTED threshold). Results thread as
// small records (never tables). Driver-held lists (sizes) stay at
// depth <= 3; deep frames carry scalars + small maps only.
record TR { t: list<int,24>, p: int, d: D }
record SR { p: int, d: D }
record FR { i: int, d: D }
fn derr(c: int, f: int, o: int): D { return D(c: c, f: f, o: o); }
fn dok(): D { return D(c: 0, f: 0, o: 0); }
fn has_err(d: D): bool {
  if d->c == 0 { return false; } else { }
  return true;
}
fn tscal(b: int): list<int,24> {
  let t: list<int,24> = [b];
  return t;
}
fn tcons(h: int, t: list<int,24>, i: int, acc: list<int,24>): list<int,24> {
  if i == 0 { return tcons(h, t, i + 1, unwrap_or(push(acc, h), acc)); } else { }
  if i - 1 >= len(t) { return acc; } else { }
  return tcons(h, t, i + 1, unwrap_or(push(acc, unwrap_or(t[i - 1], 0)), acc));
}
fn teq(a: list<int,24>, b: list<int,24>): bool {
  if len(a) == len(b) { } else { return false; }
  return teq_at(a, b, 0);
}
fn teq_at(a: list<int,24>, b: list<int,24>, i: int): bool {
  if i >= len(a) { return true; } else { }
  if unwrap_or(a[i], 0 - 1) == unwrap_or(b[i], 0 - 2) { return teq_at(a, b, i + 1); } else { }
  return false;
}
fn tbase(t: list<int,24>): int {
  return unwrap_or(t[0], 0);
}
fn tsub(t: list<int,24>, i: int, acc: list<int,24>): list<int,24> {
  if i >= len(t) { return acc; } else { }
  let b: int = unwrap_or(t[i], 0);
  if b == 0 { return acc; } else { }
  if b == 1 { return unwrap_or(push(acc, 1), acc); } else { }
  if b == 2 { return unwrap_or(push(acc, 2), acc); } else { }
  if b == 3 { return unwrap_or(push(acc, 3), acc); } else { }
  if b == 4 { return unwrap_or(push(unwrap_or(push(acc, 4), acc), unwrap_or(t[i + 1], 0)), acc); } else { }
  if b == 6 { return tsubcat(t, i + 1, unwrap_or(push(acc, 6), acc)); } else { }
  return tsublist(t, i, acc);
}
fn tsubcat(t: list<int,24>, i: int, acc: list<int,24>): list<int,24> {
  let sub: list<int,24> = tsub(t, i, []);
  return tcat(acc, sub, 0);
}
fn tcat(a: list<int,24>, b: list<int,24>, i: int): list<int,24> {
  if i >= len(b) { return a; } else { }
  return tcat(unwrap_or(push(a, unwrap_or(b[i], 0)), a), b, i + 1);
}
fn tsublist(t: list<int,24>, i: int, acc: list<int,24>): list<int,24> {
  let e: list<int,24> = tsub(t, i + 1, []);
  let n: list<int,24> = tcat(unwrap_or(push(acc, 5), acc), e, 0);
  return tcat(n, [unwrap_or(t[i + len(e) + 1], 0)], 0);
}
fn tsize(t: list<int,24>, src: str, f: int, depth: int): int {
  return tsize2(0, t, 0, src, f, depth);
}
fn tsize2(mode: int, t: list<int,24>, rid: int, src: str, f: int, depth: int): int {
  if depth > 8 { return 0; } else { }
  if mode == 1 { return rec_size_at(src, f, rid, depth); } else { }
  let b: int = tbase(t);
  if b == 0 { return 0; } else { }
  if b == 1 { return 8; } else { }
  if b == 2 { return 8; } else { }
  if b == 3 { return 16; } else { }
  if b == 4 { return tsize2(1, t, unwrap_or(t[1], 0), src, f, depth + 1); } else { }
  if b == 6 { return 16 + tsize2(0, tsub(t, 1, []), 0, src, f, depth + 1); } else { }
  let e: list<int,24> = tsub(t, 1, []);
  let cap: int = unwrap_or(t[1 + len(e)], 0);
  return 8 + cap * tsize2(0, e, 0, src, f, depth + 1);
}
fn tslots(t: list<int,24>, src: str, f: int): int {
  let b: int = tbase(t);
  if b == 1 { return 1; } else { }
  if b == 2 { return 1; } else { }
  if b == 3 { return 2; } else { }
  let n: int = tsize(t, src, f, 0);
  return (n + 7) / 8;
}
fn rec_size_at(src: str, f: int, rid: int, depth: int): int {
  return rec_find(src, f, rid, 0, 0, depth);
}
fn rec_fstart(src: str, pos: int, end: int): int {
  let t: Tok = next_tok(src, pos);
  if t->s >= end { return 0 - 1; } else { }
  if t->k == 0 - 2 { return rec_fstart(src, t->p, end); } else { }
  if t->k == 0 { return 0 - 1; } else { }
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 123 { return t->p; } else { } } else { } } else { }
  if t->p <= pos { return 0 - 1; } else { }
  return rec_fstart(src, t->p, end);
}
fn rec_find(src: str, f: int, rid: int, pos: int, seen: int, depth: int): int {
  let it: TI = tl_next(src, f, pos, len(src));
  if it->k == 0 { return 0; } else { }
  if it->k == 0 - 1 { return rec_find(src, f, rid, it->p, seen, depth); } else { }
  if it->k == 2 {
    if seen == rid { return rec_find_hit(src, f, it, depth); } else { }
    return rec_find(src, f, rid, it->p, seen + 1, depth);
  } else { }
  return rec_find(src, f, rid, it->p, seen, depth);
}
fn rec_find_hit(src: str, f: int, it: TI, depth: int): int {
  let fs: int = rec_fstart(src, it->s, it->s + it->l);
  if fs == 0 - 1 { return 0; } else { }
  return rec_fields_size(src, f, fs, it->s + it->l, depth);
}
fn rec_ftype(src: str, f: int, rid: int, ns: int, nl: int): TR {
  return rec_ffind(src, f, rid, ns, nl, 0, 0);
}
fn rec_ffind(src: str, f: int, rid: int, ns: int, nl: int, pos: int, seen: int): TR {
  let it: TI = tl_next(src, f, pos, len(src));
  if it->k == 0 { return TR(t: tscal(1), p: pos, d: derr(9, f, ns)); } else { }
  if it->k == 0 - 1 { return rec_ffind(src, f, rid, ns, nl, it->p, seen); } else { }
  if it->k == 2 {
    if seen == rid { return rec_ffhit(src, f, it, ns, nl); } else { }
    return rec_ffind(src, f, rid, ns, nl, it->p, seen + 1);
  } else { }
  return rec_ffind(src, f, rid, ns, nl, it->p, seen);
}
fn rec_ffhit(src: str, f: int, it: TI, ns: int, nl: int): TR {
  let fs: int = rec_fstart(src, it->s, it->s + it->l);
  if fs == 0 - 1 { return TR(t: tscal(1), p: it->s, d: derr(9, f, ns)); } else { }
  return rec_fftype(src, f, fs, it->s + it->l, ns, nl);
}
fn rec_fftype(src: str, f: int, pos: int, end: int, ns: int, nl: int): TR {
  let t: Tok = next_tok(src, pos);
  if t->s >= end { return TR(t: tscal(1), p: end, d: derr(9, f, ns)); } else { }
  if t->k == 0 - 2 { return rec_fftype(src, f, t->p, end, ns, nl); } else { }
  if t->k == 0 { return TR(t: tscal(1), p: end, d: derr(9, f, ns)); } else { }
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 125 { return TR(t: tscal(1), p: end, d: derr(9, f, ns)); } else { } } else { } return rec_fftype(src, f, t->p, end, ns, nl); } else { }
  if t->k == 1 { return rec_ffname(src, f, t->p, end, ns, nl, t); } else { }
  return rec_fftype(src, f, t->p, end, ns, nl);
}
fn rec_ffname(src: str, f: int, pos: int, end: int, ns: int, nl: int, t: Tok): TR {
  if t->l == nl { if beq(src, t->s, src, ns, nl) { return rec_ffval(src, f, t->p, end, ns, nl); } else { } } else { }
  return rec_ffskip(src, f, t->p, end, ns, nl);
}
fn rec_ffval(src: str, f: int, pos: int, end: int, ns: int, nl: int): TR {
  let t: Tok = next_tok(src, pos);
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 58 { return intern_ty(src, f, t->p, end, 0); } else { } } else { } } else { }
  return rec_fftype(src, f, t->p, end, ns, nl);
}
fn rec_ffskip(src: str, f: int, pos: int, end: int, ns: int, nl: int): TR {
  let an: VS = annot_span(src, pos);
  if an->tl == 0 { return rec_fftype(src, f, pos, end, ns, nl); } else { }
  return rec_fftype(src, f, an->ts + an->tl, end, ns, nl);
}
fn rec_fields_size(src: str, f: int, s: int, end: int, depth: int): int {
  return rec_field_at(src, f, s, end, depth, 0);
}
fn rec_field_at(src: str, f: int, pos: int, end: int, depth: int, acc: int): int {
  let t: Tok = next_tok(src, tl_skip_ws(src, pos, end));
  if t->k == 4 { if t->l == 1 { if unwrap_or(byte_at(src, t->s), 0) == 125 { return acc; } else { } } else { } return acc; } else { }
  if t->k == 1 { return rec_field_ty(src, f, t->p, end, depth, acc); } else { }
  return acc;
}
fn rec_field_ty(src: str, f: int, pos: int, end: int, depth: int, acc: int): int {
  let t: Tok = next_tok(src, tl_skip_ws(src, pos, end));
  if t->k == 4 { if t->l == 1 { if unwrap_or(byte_at(src, t->s), 0) == 58 { return rec_field_val(src, f, t->p, end, depth, acc); } else { } } else { } }
  return acc;
}
fn rec_field_val(src: str, f: int, pos: int, end: int, depth: int, acc: int): int {
  let ty: TR = intern_ty(src, f, tl_skip_ws(src, pos, end), end, 0);
  if has_err(ty->d) { return acc; } else { }
  let w: int = tsize2(0, ty->t, 0, src, f, depth + 1);
  return rec_field_next(src, f, ty->p, end, depth, acc + w);
}
fn rec_field_next(src: str, f: int, pos: int, end: int, depth: int, acc: int): int {
  let t: Tok = next_tok(src, tl_skip_ws(src, pos, end));
  if t->k == 4 { if t->l == 1 { if unwrap_or(byte_at(src, t->s), 0) == 44 { return rec_field_at(src, f, t->p, end, depth, acc); } else { } } else { } }
  return acc;
}
fn intern_ty(src: str, f: int, pos: int, end: int, depth: int): TR {
  if depth > 8 { return TR(t: tscal(1), p: pos, d: derr(14, f, pos)); } else { }
  let p: int = tl_skip_ws(src, pos, end);
  let t: Tok = next_tok(src, p);
  if t->k == 0 - 2 { return TR(t: tscal(1), p: pos, d: derr(t->l, f, t->s)); } else { }
  if t->k == 1 {
    if t->l == 3 { if beq(src, t->s, "int", 0, 3) { return TR(t: tscal(1), p: t->p, d: dok()); } else { } } else { }
    if t->l == 4 { if beq(src, t->s, "bool", 0, 4) { return TR(t: tscal(2), p: t->p, d: dok()); } else { } } else { }
    if t->l == 3 { if beq(src, t->s, "str", 0, 3) { return TR(t: tscal(3), p: t->p, d: dok()); } else { } } else { }
    if t->l == 4 { if beq(src, t->s, "list", 0, 4) { return intern_list(src, f, t->p, end, depth); } else { } } else { }
    if t->l == 6 {
      if beq(src, t->s, "status", 0, 6) { return intern_status(src, f, t->p, end, depth); } else { }
      if beq(src, t->s, "result", 0, 6) { return TR(t: tscal(1), p: t->p, d: derr(15, f, t->s)); } else { }
    } else { }
    if t->l == 3 { if beq(src, t->s, "map", 0, 3) { return TR(t: tscal(1), p: t->p, d: derr(15, f, t->s)); } else { } } else { }
    return intern_nominal(src, f, t, end, depth);
  } else { }
  return TR(t: tscal(1), p: pos, d: derr(11, f, pos));
}
fn intern_nominal(src: str, f: int, t: Tok, end: int, depth: int): TR {
  let nx: Tok = next_tok(src, tl_skip_ws(src, t->p, end));
  if nx->k == 4 { if nx->l == 2 { if unwrap_or(byte_at(src, nx->s), 0) == 58 { if unwrap_or(byte_at(src, nx->s + 1), 0) == 58 { return intern_qual(src, f, t, nx->p, end, depth); } else { } } else { } } else { } }
  let r: FR = res_rec(src, f, t->s, t->l, t->s);
  if has_err(r->d) { return TR(t: tscal(1), p: t->p, d: r->d); } else { }
  if r->i == 0 - 1 { return TR(t: tscal(1), p: t->p, d: derr(9, f, t->s)); } else { }
  let q: list<int,24> = [4, r->i];
  return TR(t: q, p: t->p, d: dok());
}
fn intern_qual(src: str, f: int, t: Tok, pos: int, end: int, depth: int): TR {
  return TR(t: tscal(1), p: pos, d: derr(9, f, t->s));
}
fn intern_list(src: str, f: int, pos: int, end: int, depth: int): TR {
  let o: Tok = next_tok(src, tl_skip_ws(src, pos, end));
  if o->k == 4 { if o->l == 1 { if unwrap_or(byte_at(src, o->s), 0) == 60 { } else { return TR(t: tscal(1), p: pos, d: derr(6, f, pos)); } } else { return TR(t: tscal(1), p: pos, d: derr(6, f, pos)); } } else { return TR(t: tscal(1), p: pos, d: derr(6, f, pos)); }
  let e0: Tok = next_tok(src, tl_skip_ws(src, o->p, end));
  if e0->k == 1 { } else { return TR(t: tscal(1), p: o->p, d: derr(6, f, e0->s)); }
  let e: TR = intern_ty(src, f, o->p, end, depth + 1);
  if has_err(e->d) { return e; } else { }
  if tbase(e->t) == 6 { return TR(t: tscal(1), p: o->p, d: derr(11, f, o->p)); } else { }
  let c: Tok = next_tok(src, tl_skip_ws(src, e->p, end));
  if c->k == 4 { if c->l == 1 { if unwrap_or(byte_at(src, c->s), 0) == 44 { } else { return TR(t: tscal(1), p: e->p, d: derr(6, f, e->p)); } } else { return TR(t: tscal(1), p: e->p, d: derr(6, f, e->p)); } } else { return TR(t: tscal(1), p: e->p, d: derr(6, f, e->p)); }
  let n: Tok = next_tok(src, c->p);
  if n->k == 2 { } else { return TR(t: tscal(1), p: c->p, d: derr(6, f, c->p)); }
  let cap: int = span_int(src, n->s, n->l);
  if cap < 1 { return TR(t: tscal(1), p: n->s, d: derr(14, f, n->s)); } else { }
  let g: Tok = next_tok(src, n->p);
  if g->k == 4 {
    if unwrap_or(byte_at(src, g->s), 0) == 62 {
      if g->l == 1 { return TR(t: tlist_code(e->t, cap), p: g->p, d: dok()); } else { }
      if g->l == 2 { if unwrap_or(byte_at(src, g->s + 1), 0) == 62 { return TR(t: tlist_code(e->t, cap), p: g->s + 1, d: dok()); } else { } } else { }
    } else { }
  } else { }
  return TR(t: tscal(1), p: g->s, d: derr(6, f, g->s));
}
fn tlist_code(e: list<int,24>, cap: int): list<int,24> {
  let a: list<int,24> = [5];
  let b: list<int,24> = tcat(a, e, 0);
  return unwrap_or(push(b, cap), b);
}
fn span_int(src: str, s: int, l: int): int {
  return span_int_at(src, s, s + l, 0);
}
fn span_int_at(src: str, pos: int, end: int, acc: int): int {
  if pos >= end { return acc; } else { }
  let d: int = unwrap_or(byte_at(src, pos), 0) - 48;
  if acc > 922337203685477580 { return 9223372036854775807; } else { }
  if acc == 922337203685477580 { if d > 7 { return 9223372036854775807; } else { } } else { }
  return span_int_at(src, pos + 1, end, acc * 10 + d);
}
fn check_bounded(t: list<int,24>, src: str, f: int, o: int): D {
  if tbase(t) == 0 { return dok(); } else { }
  if tbase(t) == 1 { return dok(); } else { }
  if tbase(t) == 2 { return dok(); } else { }
  if tbase(t) == 3 { return dok(); } else { }
  if tsize(t, src, f, 0) > 8192 { return derr(14, f, o); } else { }
  return dok();
}
fn intern_status(src: str, f: int, pos: int, end: int, depth: int): TR {
  let o: Tok = next_tok(src, tl_skip_ws(src, pos, end));
  if o->k == 4 { if o->l == 1 { if unwrap_or(byte_at(src, o->s), 0) == 60 { } else { return TR(t: tscal(1), p: pos, d: derr(6, f, pos)); } } else { return TR(t: tscal(1), p: pos, d: derr(6, f, pos)); } } else { return TR(t: tscal(1), p: pos, d: derr(6, f, pos)); }
  let e0: Tok = next_tok(src, tl_skip_ws(src, o->p, end));
  if e0->k == 1 { } else { return TR(t: tscal(1), p: o->p, d: derr(6, f, e0->s)); }
  let e: TR = intern_ty(src, f, o->p, end, depth + 1);
  if has_err(e->d) { return e; } else { }
  if tbase(e->t) == 6 { return TR(t: tscal(1), p: o->p, d: derr(11, f, o->p)); } else { }
  let g: Tok = next_tok(src, tl_skip_ws(src, e->p, end));
  if g->k == 4 {
    if unwrap_or(byte_at(src, g->s), 0) == 62 {
      if g->l == 1 { return TR(t: tcat(tscal(6), e->t, 0), p: g->p, d: dok()); } else { }
      if g->l == 2 { if unwrap_or(byte_at(src, g->s + 1), 0) == 62 { return TR(t: tcat(tscal(6), e->t, 0), p: g->s + 1, d: dok()); } else { } } else { }
    } else { }
  } else { }
  return TR(t: tscal(1), p: e->p, d: derr(6, f, e->p));
}
record TI { k: int, s: int, l: int, p: int, d: D }
fn is_wb(src: str, pos: int): bool {
  if pos < 0 { return true; } else { }
  if pos >= len(src) { return true; } else { }
  return is_alnum(unwrap_or(byte_at(src, pos), 0)) == false;
}
fn tl_skip_str(src: str, pos: int, end: int): int {
  if pos >= end { return pos; } else { }
  let b: int = unwrap_or(byte_at(src, pos), 0);
  if b == 34 { return pos + 1; } else { }
  if b == 92 { return tl_skip_str(src, pos + 2, end); } else { }
  return tl_skip_str(src, pos + 1, end);
}
fn tl_skip_ws(src: str, pos: int, end: int): int {
  if pos >= end { return pos; } else { }
  let b: int = unwrap_or(byte_at(src, pos), 0);
  if b == 32 { return tl_skip_ws(src, pos + 1, end); } else { }
  if b == 9 { return tl_skip_ws(src, pos + 1, end); } else { }
  if b == 10 { return tl_skip_ws(src, pos + 1, end); } else { }
  if b == 13 { return tl_skip_ws(src, pos + 1, end); } else { }
  if b == 47 {
    if unwrap_or(byte_at(src, pos + 1), 0) == 47 { return tl_skip_ws(src, tl_skip_line(src, pos + 2, end), end); } else { }
    return pos;
  } else { }
  if b == 34 { return tl_skip_str(src, pos + 1, end); } else { }
  return pos;
}
fn tl_skip_line(src: str, pos: int, end: int): int {
  if pos >= end { return pos; } else { }
  if unwrap_or(byte_at(src, pos), 0) == 10 { return pos + 1; } else { }
  return tl_skip_line(src, pos + 1, end);
}
fn tl_balanced(src: str, pos: int, end: int, depth: int): int {
  if pos >= end { return pos; } else { }
  let b: int = unwrap_or(byte_at(src, pos), 0);
  if b == 123 { return tl_balanced(src, pos + 1, end, depth + 1); } else { }
  if b == 125 {
    if depth == 1 { return pos + 1; } else { }
    return tl_balanced(src, pos + 1, end, depth - 1);
  } else { }
  if b == 34 { return tl_balanced(src, tl_skip_str(src, pos + 1, end), end, depth); } else { }
  if b == 47 {
    if unwrap_or(byte_at(src, pos + 1), 0) == 47 { return tl_balanced(src, tl_skip_line(src, pos + 2, end), end, depth); } else { }
    return tl_balanced(src, pos + 1, end, depth);
  } else { }
  return tl_balanced(src, pos + 1, end, depth);
}
fn tl_word(src: str, pos: int, end: int, w: str): bool {
  if beq(src, pos, w, 0, len(w)) { } else { return false; }
  if is_wb(src, pos - 1) { } else { return false; }
  return is_wb(src, pos + len(w));
}
fn tl_next(src: str, f: int, pos: int, end: int): TI {
  let p: int = tl_skip_ws(src, pos, end);
  if p >= end { return TI(k: 0, s: p, l: 0, p: p, d: dok()); } else { }
  if tl_word(src, p, end, "fn") { return TI(k: 1, s: p, l: tl_balanced(src, p, end, 0) - p, p: tl_balanced(src, p, end, 0), d: dok()); } else { }
  if tl_word(src, p, end, "record") { return TI(k: 2, s: p, l: tl_balanced(src, p, end, 0) - p, p: tl_balanced(src, p, end, 0), d: dok()); } else { }
  if tl_word(src, p, end, "use") { return TI(k: 3, s: p, l: tl_use_end(src, p, end) - p, p: tl_use_end(src, p, end), d: dok()); } else { }
  return TI(k: 0 - 1, s: p, l: 0, p: p + 1, d: derr(7, f, p));
}
fn tl_use_end(src: str, pos: int, end: int): int {
  if pos >= end { return pos; } else { }
  let b: int = unwrap_or(byte_at(src, pos), 0);
  if b == 59 { return pos + 1; } else { }
  if b == 34 { return tl_use_end(src, tl_skip_str(src, pos + 1, end), end); } else { }
  return tl_use_end(src, pos + 1, end);
}
fn is_reserved_w(src: str, s: int, l: int): bool {
  if l == 3 {
    if beq(src, s, "len", 0, 3) { return true; } else { }
    if beq(src, s, "get", 0, 3) { return true; } else { }
    if beq(src, s, "err", 0, 3) { return true; } else { }
    if beq(src, s, "use", 0, 3) { return true; } else { }
  } else { }
  if l == 2 {
    if beq(src, s, "ok", 0, 2) { return true; } else { }
  } else { }
  if l == 4 {
    if beq(src, s, "push", 0, 4) { return true; } else { }
  } else { }
  if l == 5 {
    if beq(src, s, "is_ok", 0, 5) { return true; } else { }
  } else { }
  if l == 6 {
    if beq(src, s, "insert", 0, 6) { return true; } else { }
    if beq(src, s, "is_err", 0, 6) { return true; } else { }
  } else { }
  if l == 7 {
    if beq(src, s, "byte_at", 0, 7) { return true; } else { }
  } else { }
  if l == 9 {
    if beq(src, s, "unwrap_or", 0, 9) { return true; } else { }
  } else { }
  if l == 5 {
    if beq(src, s, "fread", 0, 5) { return true; } else { }
    if beq(src, s, "fjoin", 0, 5) { return true; } else { }
  } else { }
  if l == 4 {
    if beq(src, s, "argv", 0, 4) { return true; } else { }
  } else { }
  return false;
}
fn res_fn(src: str, f: int, ns: int, nl: int, lim: int): FR {
  return res_fn_at(src, f, ns, nl, lim, 0, 0, 0 - 1);
}
fn res_fn_at(src: str, f: int, ns: int, nl: int, lim: int, pos: int, ordinal: int, found: int): FR {
  let it: TI = tl_next(src, f, pos, lim);
  if it->k == 0 { if found == 0 - 1 { return FR(i: 0 - 1, d: derr(13, f, ns)); } else { } return FR(i: found, d: dok()); } else { }
  if it->k == 0 - 1 { return res_fn_at(src, f, ns, nl, lim, it->p, ordinal, found); } else { }
  if it->k == 1 {
    return res_fn_name(src, f, ns, nl, lim, it, pos, ordinal, found);
  } else { }
  return res_fn_at(src, f, ns, nl, lim, it->p, ordinal, found);
}
fn res_fn_name(src: str, f: int, ns: int, nl: int, lim: int, it: TI, pos: int, ordinal: int, found: int): FR {
  let t: Tok = next_tok(src, tl_skip_ws(src, it->s + 2, it->s + it->l));
  if t->k == 1 { if t->l == nl { if beq(src, t->s, src, ns, nl) { return res_fn_at(src, f, ns, nl, lim, it->p, ordinal + 1, ordinal); } else { } } else { } } else { }
  return res_fn_at(src, f, ns, nl, lim, it->p, ordinal + 1, found);
}
fn res_rec(src: str, f: int, ns: int, nl: int, lim: int): FR {
  return res_rec_at(src, f, ns, nl, lim, 0, 0, 0 - 1);
}
fn res_rec_at(src: str, f: int, ns: int, nl: int, lim: int, pos: int, ordinal: int, found: int): FR {
  let it: TI = tl_next(src, f, pos, lim);
  if it->k == 0 { if found == 0 - 1 { return FR(i: 0 - 1, d: derr(9, f, ns)); } else { } return FR(i: found, d: dok()); } else { }
  if it->k == 0 - 1 { return res_rec_at(src, f, ns, nl, lim, it->p, ordinal, found); } else { }
  if it->k == 2 {
    return res_rec_name(src, f, ns, nl, lim, it, pos, ordinal, found);
  } else { }
  return res_rec_at(src, f, ns, nl, lim, it->p, ordinal, found);
}
fn res_rec_name(src: str, f: int, ns: int, nl: int, lim: int, it: TI, pos: int, ordinal: int, found: int): FR {
  let t: Tok = next_tok(src, tl_skip_ws(src, it->s + 6, it->s + it->l));
  if t->k == 1 { if t->l == nl { if beq(src, t->s, src, ns, nl) { return res_rec_at(src, f, ns, nl, lim, it->p, ordinal + 1, ordinal); } else { } } else { } } else { }
  return res_rec_at(src, f, ns, nl, lim, it->p, ordinal + 1, found);
}
fn dup_fn(src: str, f: int, ns: int, nl: int, lim: int): D {
  return dup_at(src, f, ns, nl, lim, 0, 1, 2, 0);
}
fn dup_rec(src: str, f: int, ns: int, nl: int, lim: int): D {
  return dup_at(src, f, ns, nl, lim, 0, 2, 6, 0);
}
fn dup_at(src: str, f: int, ns: int, nl: int, lim: int, pos: int, kind: int, kw: int, seen: int): D {
  let it: TI = tl_next(src, f, pos, lim);
  if it->k == 0 { return dok(); } else { }
  if it->k == 0 - 1 { return dup_at(src, f, ns, nl, lim, it->p, kind, kw, seen); } else { }
  if it->k == kind {
    return dup_name(src, f, ns, nl, lim, it, pos, kind, kw, seen);
  } else { }
  return dup_at(src, f, ns, nl, lim, it->p, kind, kw, seen);
}
fn dup_name(src: str, f: int, ns: int, nl: int, lim: int, it: TI, pos: int, kind: int, kw: int, seen: int): D {
  let t: Tok = next_tok(src, tl_skip_ws(src, it->s + kw, it->s + it->l));
  if t->k == 1 { if t->l == nl { if beq(src, t->s, src, ns, nl) { if seen == 1 { return derr(10, f, it->s); } else { } return dup_at(src, f, ns, nl, lim, it->p, kind, kw, 1); } else { } } else { } } else { }
  return dup_at(src, f, ns, nl, lim, it->p, kind, kw, seen);
}
record VS { off: int, k: int, ts: int, tl: int, slot: int, d: D }
fn res_var(src: str, f: int, fnstart: int, fnend: int, useoff: int, ns: int, nl: int): VS {
  let lo: int = flat_let(src, fnstart, useoff, ns, nl, fnstart, 0, 0 - 1);
  let po: VS = hdr_find(src, fnstart, fnend, ns, nl);
  if has_err(po->d) { return po; } else { }
  let mo: VS = arm_find(src, f, fnstart, fnend, useoff, ns, nl, 0 - 1);
  if has_err(mo->d) { return mo; } else { }
  let n: int = present(lo) + present(po->off) + present(mo->off);
  if n == 0 { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: derr(9, f, ns)); } else { }
  if n == 1 { } else { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: derr(10, f, ns)); }
  return res_var_one(src, f, fnstart, fnend, useoff, ns, nl, lo, po, mo);
}
fn present(off: int): int {
  if off == 0 - 1 { return 0; } else { }
  return 1;
}
fn scope_slot(src: str, f: int, fnstart: int, fnend: int, bound: int): int {
  return slot_params(src, f, fnstart, fnend, 0) + slot_lets(src, f, fnstart, bound, 0, 0);
}
fn slot_params(src: str, f: int, fnstart: int, fnend: int, acc: int): int {
  let k: Tok = next_tok(src, fnstart);
  let nm: Tok = next_tok(src, k->p);
  let op: Tok = next_tok(src, nm->p);
  return slot_params_at(src, f, fnstart, fnend, op->p, acc);
}
fn slot_params_at(src: str, f: int, fnstart: int, fnend: int, pos: int, acc: int): int {
  if pos >= fnend { return acc; } else { }
  let t: Tok = next_tok(src, pos);
  if t->k == 0 - 2 { return slot_params_at(src, f, fnstart, fnend, t->p, acc); } else { }
  if t->k == 4 {
    if t->l == 1 {
      if tok_byte(src, t->s) == 41 { return acc; } else { }
      if tok_byte(src, t->s) == 123 { return acc; } else { }
    } else { }
    return slot_params_at(src, f, fnstart, fnend, t->p, acc);
  } else { }
  if t->k == 1 {
    return slot_params_ident(src, f, fnstart, fnend, t, acc);
  } else { }
  return slot_params_at(src, f, fnstart, fnend, t->p, acc);
}
fn slot_params_ident(src: str, f: int, fnstart: int, fnend: int, t: Tok, acc: int): int {
  let an: VS = annot_span(src, t->p);
  if an->tl == 0 { return slot_params_at(src, f, fnstart, fnend, t->p, acc); } else { }
  let ty: TR = intern_ty(src, f, an->ts, an->ts + an->tl, 0);
  if has_err(ty->d) { return slot_params_at(src, f, fnstart, fnend, an->ts + an->tl, acc + 1); } else { }
  return slot_params_at(src, f, fnstart, fnend, an->ts + an->tl, acc + tslots(ty->t, src, f));
}
fn slot_lets(src: str, f: int, pos: int, bound: int, depth: int, acc: int): int {
  if pos >= bound { return acc; } else { }
  let t: Tok = next_tok(src, pos);
  if t->k == 0 - 2 { return slot_lets(src, f, t->p, bound, depth, acc); } else { }
  if t->k == 0 { return acc; } else { }
  if t->k == 4 {
    if t->l == 1 {
      if tok_byte(src, t->s) == 123 { return slot_lets(src, f, t->p, bound, depth + 1, acc); } else { }
      if tok_byte(src, t->s) == 125 { return slot_lets(src, f, t->p, bound, depth - 1, acc); } else { }
    } else { }
    return slot_lets(src, f, t->p, bound, depth, acc);
  } else { }
  if t->k == 1 {
    if t->l == 3 { if beq(src, t->s, "let", 0, 3) { return slot_let_at(src, f, t->p, bound, depth, acc); } else { } } else { }
  } else { }
  return slot_lets(src, f, t->p, bound, depth, acc);
}
fn slot_let_at(src: str, f: int, pos: int, bound: int, depth: int, acc: int): int {
  let t: Tok = next_tok(src, pos);
  if t->k == 1 { if depth == 1 { if peek_colon(src, t->p) { return slot_lets(src, f, t->p, bound, depth, acc + slot_annot(src, f, t->p)); } else { } } else { } } else { }
  return slot_lets(src, f, t->p, bound, depth, acc);
}
fn slot_annot(src: str, f: int, pos: int): int {
  let an: VS = annot_span(src, pos);
  if an->tl == 0 { return 1; } else { }
  let ty: TR = intern_ty(src, f, an->ts, an->ts + an->tl, 0);
  if has_err(ty->d) { return 1; } else { }
  return tslots(ty->t, src, f);
}
fn infer_scrut(src: str, f: int, fnstart: int, fnend: int, ss: int, sl: int): TR {
  let t: Tok = next_tok(src, ss);
  if t->k == 2 { return TR(t: tscal(1), p: t->p, d: dok()); } else { }
  if t->k == 3 { return TR(t: tscal(3), p: t->p, d: dok()); } else { }
  if t->k == 1 {
    return infer_scrut_ident(src, f, fnstart, fnend, ss, sl, t);
  } else { }
  return TR(t: tscal(1), p: t->p, d: derr(11, f, ss));
}
fn infer_scrut_ident(src: str, f: int, fnstart: int, fnend: int, ss: int, sl: int, t: Tok): TR {
  if beq(src, t->s, "true", 0, 4) { if t->l == 4 { return TR(t: tscal(2), p: t->p, d: dok()); } else { } } else { }
  if beq(src, t->s, "false", 0, 5) { if t->l == 5 { return TR(t: tscal(2), p: t->p, d: dok()); } else { } } else { }
  let nx: Tok = next_tok(src, t->p);
  if nx->k == 4 { if nx->l == 1 { if tok_byte(src, nx->s) == 40 { return infer_call(src, f, fnstart, fnend, t->s, t->l, ss); } else { } } else { } } else { }
  let v: VS = res_var(src, f, fnstart, fnend, ss, t->s, t->l);
  if has_err(v->d) { return TR(t: tscal(1), p: t->p, d: v->d); } else { }
  return infer_var_ty(src, f, fnstart, fnend, v);
}
fn infer_var_ty(src: str, f: int, fnstart: int, fnend: int, v: VS): TR {
  if v->k == 0 { return infer_annot(src, f, v->ts, v->tl); } else { }
  if v->k == 1 { return infer_annot(src, f, v->ts, v->tl); } else { }
  if v->k == 3 { return TR(t: tscal(1), p: v->ts, d: dok()); } else { }
  let st: TR = infer_scrut(src, f, fnstart, fnend, v->ts, v->tl);
  if has_err(st->d) { return st; } else { }
  if v->k == 2 { return TR(t: scrut_payload(st->t), p: v->ts, d: dok()); } else { }
  return st;
}
fn scrut_payload(t: list<int,24>): list<int,24> {
  if tbase(t) == 6 { return tsub(t, 1, []); } else { }
  return tscal(1);
}
fn infer_annot(src: str, f: int, ts: int, tl: int): TR {
  if tl == 0 { return TR(t: tscal(1), p: ts, d: derr(11, f, ts)); } else { }
  return intern_ty(src, f, ts, ts + tl, 0);
}
fn is_int_ty(t: list<int,24>): bool {
  if tbase(t) == 1 { return true; } else { }
  return false;
}
fn is_bool_ty(t: list<int,24>): bool {
  if tbase(t) == 2 { return true; } else { }
  return false;
}
fn check_binop(src: str, f: int, os: int, ol: int, t1: list<int,24>, t2: list<int,24>, o: int): TR {
  let c: int = tok_byte(src, os);
  if ol == 1 {
    if c == 43 { if is_int_ty(t1) { if is_int_ty(t2) { return TR(t: tscal(1), p: o, d: dok()); } else { } } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
    if c == 45 { if is_int_ty(t1) { if is_int_ty(t2) { return TR(t: tscal(1), p: o, d: dok()); } else { } } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
    if c == 42 { if is_int_ty(t1) { if is_int_ty(t2) { return TR(t: tscal(1), p: o, d: dok()); } else { } } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
    if c == 47 { if is_int_ty(t1) { if is_int_ty(t2) { return TR(t: tscal(1), p: o, d: dok()); } else { } } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
    if c == 37 { if is_int_ty(t1) { if is_int_ty(t2) { return TR(t: tscal(1), p: o, d: dok()); } else { } } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
    if c == 38 { if is_int_ty(t1) { if is_int_ty(t2) { return TR(t: tscal(1), p: o, d: dok()); } else { } } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
    if c == 124 { if is_int_ty(t1) { if is_int_ty(t2) { return TR(t: tscal(1), p: o, d: dok()); } else { } } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
    if c == 94 { if is_int_ty(t1) { if is_int_ty(t2) { return TR(t: tscal(1), p: o, d: dok()); } else { } } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
    if c == 60 { if is_int_ty(t1) { if is_int_ty(t2) { return TR(t: tscal(2), p: o, d: dok()); } else { } } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
    if c == 62 { if is_int_ty(t1) { if is_int_ty(t2) { return TR(t: tscal(2), p: o, d: dok()); } else { } } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
  } else { }
  if ol == 2 {
    if beq(src, os, "==", 0, 2) { if teq(t1, t2) { if tbase(t1) == 0 { return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { } return TR(t: tscal(2), p: o, d: dok()); } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
    if beq(src, os, "!=", 0, 2) { if teq(t1, t2) { if tbase(t1) == 0 { return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { } return TR(t: tscal(2), p: o, d: dok()); } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
    if beq(src, os, "<=", 0, 2) { if is_int_ty(t1) { if is_int_ty(t2) { return TR(t: tscal(2), p: o, d: dok()); } else { } } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
    if beq(src, os, ">=", 0, 2) { if is_int_ty(t1) { if is_int_ty(t2) { return TR(t: tscal(2), p: o, d: dok()); } else { } } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
    if beq(src, os, "&&", 0, 2) { if is_bool_ty(t1) { if is_bool_ty(t2) { return TR(t: tscal(2), p: o, d: dok()); } else { } } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
    if beq(src, os, "||", 0, 2) { if is_bool_ty(t1) { if is_bool_ty(t2) { return TR(t: tscal(2), p: o, d: dok()); } else { } } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
    if beq(src, os, "<<", 0, 2) { if is_int_ty(t1) { if is_int_ty(t2) { return TR(t: tscal(1), p: o, d: dok()); } else { } } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
    if beq(src, os, ">>", 0, 2) { if is_int_ty(t1) { if is_int_ty(t2) { return TR(t: tscal(1), p: o, d: dok()); } else { } } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
  } else { }
  return TR(t: tscal(1), p: o, d: derr(11, f, o));
}
fn check_unop(src: str, f: int, os: int, ol: int, t1: list<int,24>, o: int): TR {
  let c: int = tok_byte(src, os);
  if ol == 1 {
    if c == 45 { if is_int_ty(t1) { return TR(t: tscal(1), p: o, d: dok()); } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
    if c == 33 { if is_bool_ty(t1) { return TR(t: tscal(2), p: o, d: dok()); } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
    if c == 126 { if is_int_ty(t1) { return TR(t: tscal(1), p: o, d: dok()); } else { } return TR(t: tscal(1), p: o, d: derr(11, f, o)); } else { }
  } else { }
  return TR(t: tscal(1), p: o, d: derr(11, f, o));
}
fn builtin_arity(src: str, ns: int, nl: int): int {
  if nl == 5 { if beq(src, ns, "print", 0, 5) { return 1; } else { } } else { }
  if nl == 3 { if beq(src, ns, "len", 0, 3) { return 1; } else { } } else { }
  if nl == 5 { if beq(src, ns, "is_ok", 0, 5) { return 1; } else { } } else { }
  if nl == 6 { if beq(src, ns, "is_err", 0, 6) { return 1; } else { } } else { }
  if nl == 4 { if beq(src, ns, "push", 0, 4) { return 2; } else { } } else { }
  if nl == 7 { if beq(src, ns, "byte_at", 0, 7) { return 2; } else { } } else { }
  if nl == 9 { if beq(src, ns, "unwrap_or", 0, 9) { return 2; } else { } } else { }
  if nl == 5 { if beq(src, ns, "fread", 0, 5) { return 3; } else { } } else { }
  if nl == 5 { if beq(src, ns, "fjoin", 0, 5) { return 2; } else { } } else { }
  if nl == 4 { if beq(src, ns, "argv", 0, 4) { return 1; } else { } } else { }
  return 0 - 1;
}
fn check_barg(src: str, f: int, ns: int, nl: int, idx: int, t: list<int,24>, ctx: list<int,24>, o: int): D {
  if nl == 3 { if beq(src, ns, "len", 0, 3) { if tbase(t) == 5 { return dok(); } else { } if tbase(t) == 3 { return dok(); } else { } return derr(11, f, o); } else { } } else { }
  if nl == 5 { if beq(src, ns, "is_ok", 0, 5) { if tbase(t) == 6 { return dok(); } else { } return derr(11, f, o); } else { } } else { }
  if nl == 6 { if beq(src, ns, "is_err", 0, 6) { if tbase(t) == 6 { return dok(); } else { } return derr(11, f, o); } else { } } else { }
  if nl == 4 { if beq(src, ns, "push", 0, 4) { if idx == 0 { if tbase(t) == 5 { return push_empty(t, f, o); } else { } return derr(11, f, o); } else { } if teq(t, push_elem(ctx)) { return dok(); } else { } return derr(11, f, o); } else { } } else { }
  if nl == 7 { if beq(src, ns, "byte_at", 0, 7) { if idx == 0 { if tbase(t) == 3 { return dok(); } else { } return derr(11, f, o); } else { } if is_int_ty(t) { return dok(); } else { } return derr(11, f, o); } else { } } else { }
  if nl == 9 { if beq(src, ns, "unwrap_or", 0, 9) { if idx == 0 { if tbase(t) == 6 { return dok(); } else { } return derr(11, f, o); } else { } if teq(t, unwrap_pay(ctx)) { return dok(); } else { } return derr(11, f, o); } else { } } else { }
  if nl == 5 { if beq(src, ns, "fread", 0, 5) { if idx == 0 { if tbase(t) == 3 { return dok(); } else { } return derr(11, f, o); } else { } if is_int_ty(t) { return dok(); } else { } return derr(11, f, o); } else { } } else { }
  if nl == 5 { if beq(src, ns, "fjoin", 0, 5) { if tbase(t) == 3 { return dok(); } else { } return derr(11, f, o); } else { } } else { }
  if nl == 4 { if beq(src, ns, "argv", 0, 4) { if is_int_ty(t) { return dok(); } else { } return derr(11, f, o); } else { } } else { }
  if nl == 5 { if beq(src, ns, "print", 0, 5) { if core_printable(t) { return dok(); } else { } return derr(15, f, o); } else { } } else { }
  return derr(11, f, o);
}
fn push_elem(t: list<int,24>): list<int,24> {
  if tbase(t) == 5 { return tsub(t, 1, []); } else { }
  return tscal(1);
}
fn push_empty(t: list<int,24>, f: int, o: int): D {
  if push_is_empty(t) == 1 { return derr(11, f, o); } else { }
  return dok();
}
fn push_is_empty(t: list<int,24>): int {
  if tbase(t) == 5 { if unwrap_or(t[len(t) - 1], 1) == 0 { return 1; } else { } } else { }
  return 0;
}
fn unwrap_pay(t: list<int,24>): list<int,24> {
  if tbase(t) == 6 { return tsub(t, 1, []); } else { }
  return tscal(1);
}
fn core_printable(t: list<int,24>): bool {
  let b: int = tbase(t);
  if b == 1 { return true; } else { }
  if b == 2 { return true; } else { }
  if b == 3 { return true; } else { }
  if b == 6 { return core_printable(tsub(t, 1, [])); } else { }
  return false;
}
fn check_builtin_ret(src: str, f: int, ns: int, nl: int, c0: list<int,24>, o: int): TR {
  if nl == 5 { if beq(src, ns, "print", 0, 5) { return TR(t: tscal(0), p: o, d: dok()); } else { } } else { }
  if nl == 3 { if beq(src, ns, "len", 0, 3) { return TR(t: tscal(1), p: o, d: dok()); } else { } } else { }
  if nl == 5 { if beq(src, ns, "is_ok", 0, 5) { return TR(t: tscal(2), p: o, d: dok()); } else { } } else { }
  if nl == 6 { if beq(src, ns, "is_err", 0, 6) { return TR(t: tscal(2), p: o, d: dok()); } else { } } else { }
  if nl == 4 { if beq(src, ns, "push", 0, 4) { return TR(t: tcons(6, c0, 0, []), p: o, d: dok()); } else { } } else { }
  if nl == 7 { if beq(src, ns, "byte_at", 0, 7) { return TR(t: tcons(6, tscal(1), 0, []), p: o, d: dok()); } else { } } else { }
  if nl == 9 { if beq(src, ns, "unwrap_or", 0, 9) { return TR(t: tsub(c0, 1, []), p: o, d: dok()); } else { } } else { }
  if nl == 5 { if beq(src, ns, "fread", 0, 5) { return TR(t: tcons(6, tscal(3), 0, []), p: o, d: dok()); } else { } } else { }
  if nl == 5 { if beq(src, ns, "fjoin", 0, 5) { return TR(t: tcons(6, tscal(3), 0, []), p: o, d: dok()); } else { } } else { }
  if nl == 4 { if beq(src, ns, "argv", 0, 4) { return TR(t: tcons(6, tscal(3), 0, []), p: o, d: dok()); } else { } } else { }
  return TR(t: tscal(1), p: o, d: derr(11, f, o));
}
fn match_has(arms: list<int,16>, want: int, i: int): bool {
  if i >= len(arms) { return false; } else { }
  if unwrap_or(arms[i], 0 - 1) == want { return true; } else { }
  return match_has(arms, want, i + 1);
}
fn match_exhaustive(base: int, arms: list<int,16>): bool {
  if base == 6 { if match_has(arms, 0, 0) { if match_has(arms, 1, 0) { return true; } else { } } else { } if match_has(arms, 6, 0) { return true; } else { } return false; } else { }
  if base == 2 { if match_has(arms, 4, 0) { if match_has(arms, 5, 0) { return true; } else { } } else { } if match_has(arms, 6, 0) { return true; } else { } return false; } else { }
  if base == 1 { if match_has(arms, 6, 0) { return true; } else { } return false; } else { }
  if base == 3 { if match_has(arms, 6, 0) { return true; } else { } return false; } else { }
  return false;
}
fn next_use(src: str, pos: int, end: int): VS {
  if pos >= end { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
  let t: Tok = next_tok(src, pos);
  if t->k == 0 - 2 { return next_use(src, t->p, end); } else { }
  if t->k == 1 {
    if t->l == 3 { if beq(src, t->s, "use", 0, 3) { return next_use_str(src, t->p, end); } else { } } else { }
  } else { }
  return next_use(src, t->p, end);
}
fn next_use_str(src: str, pos: int, end: int): VS {
  let t: Tok = next_tok(src, tl_skip_ws(src, pos, end));
  if t->k == 3 { return VS(off: t->s + 1, k: 0, ts: t->s + 1, tl: t->l - 2, slot: t->s + t->l, d: dok()); } else { }
  return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok());
}
fn stem_of(src: str, s: int, l: int): VS {
  return stem_at(src, s, s + l, s);
}
fn stem_at(src: str, pos: int, end: int, last: int): VS {
  if pos >= end { return stem_cut(src, last, end); } else { }
  let b: int = unwrap_or(byte_at(src, pos), 0);
  if b == 47 { return stem_at(src, pos + 1, end, pos + 1); } else { }
  return stem_at(src, pos + 1, end, last);
}
fn stem_cut(src: str, s: int, e: int): VS {
  if e - s >= 3 { if unwrap_or(byte_at(src, e - 3), 0) == 46 { if unwrap_or(byte_at(src, e - 2), 0) == 114 { if unwrap_or(byte_at(src, e - 1), 0) == 108 { return VS(off: s, k: 0, ts: s, tl: e - 3 - s, slot: 0, d: dok()); } else { } } else { } } else { } } else { }
  return VS(off: s, k: 0, ts: s, tl: e - s, slot: 0, d: dok());
}
fn is_alias_span(src: str, s: int, l: int): bool {
  if l == 0 { return false; } else { }
  if l > 64 { return false; } else { }
  let b: int = unwrap_or(byte_at(src, s), 0);
  if is_alpha(b) { } else { return false; }
  return is_alias_rest(src, s + 1, s + l);
}
fn is_alias_rest(src: str, pos: int, end: int): bool {
  if pos >= end { return true; } else { }
  let b: int = unwrap_or(byte_at(src, pos), 0);
  if is_alpha(b) { return is_alias_rest(src, pos + 1, end); } else { }
  if is_digit(b) { return is_alias_rest(src, pos + 1, end); } else { }
  return false;
}
fn path_kind(src: str, s: int, l: int): int {
  if l == 0 { return 2; } else { }
  if unwrap_or(byte_at(src, s), 0) == 47 { return 2; } else { }
  if l >= 4 { if beq(src, s, "std/", 0, 4) { if path_segs(src, s + 4, s + l, s + 4) == 0 { return 1; } else { } return 2; } else { } } else { }
  return path_segs(src, s, s + l, s);
}
fn path_segs(src: str, s: int, e: int, seg: int): int {
  if s >= e { return path_seg_end(src, seg, e); } else { }
  let b: int = unwrap_or(byte_at(src, s), 0);
  if b == 47 { if path_seg_bad(src, seg, s) { return 2; } else { } return path_segs(src, s + 1, e, s + 1); } else { }
  return path_segs(src, s + 1, e, seg);
}
fn path_seg_end(src: str, seg: int, e: int): int {
  if path_seg_bad(src, seg, e) { return 2; } else { }
  return 0;
}
fn path_seg_bad(src: str, s: int, e: int): bool {
  if e - s == 0 { return true; } else { }
  if e - s == 1 { if unwrap_or(byte_at(src, s), 0) == 46 { return true; } else { } } else { }
  if e - s == 2 { if unwrap_or(byte_at(src, s), 0) == 46 { if unwrap_or(byte_at(src, s + 1), 0) == 46 { return true; } else { } } else { } } else { }
  return false;
}
fn urot(x: int, n: int): int {
  return ((x >> n) | (x << (32 - n))) & 4294967295;
}
fn usum(a: int, b: int): int {
  return (a + b) & 4294967295;
}
fn sha_padded(total: int): int {
  return ((total + 8) / 64 + 1) * 64;
}
fn sha_byte(msg: str, s: int, total: int, padded: int, bitlen: int, i: int): int {
  if i < total { return unwrap_or(byte_at(msg, s + i), 0); } else { }
  if i == total { return 128; } else { }
  if i < padded - 8 { return 0; } else { }
  let k: int = i - (padded - 8);
  return (bitlen >> (8 * (7 - k))) & 255;
}
fn sha_word(msg: str, s: int, total: int, padded: int, bitlen: int, i: int): int {
  let b0: int = sha_byte(msg, s, total, padded, bitlen, i);
  let b1: int = sha_byte(msg, s, total, padded, bitlen, i + 1);
  let b2: int = sha_byte(msg, s, total, padded, bitlen, i + 2);
  let b3: int = sha_byte(msg, s, total, padded, bitlen, i + 3);
  return ((b0 * 256 + b1) * 256 + b2) * 256 + b3;
}
fn sha_sched(msg: str, s: int, total: int, padded: int, bitlen: int, blk: int, t: int, acc: list<int,64>): list<int,64> {
  if t >= 64 { return acc; } else { }
  if t < 16 { return sha_sched(msg, s, total, padded, bitlen, blk, t + 1, unwrap_or(push(acc, sha_word(msg, s, total, padded, bitlen, blk + t * 4)), acc)); } else { }
  let w2: int = unwrap_or(acc[t - 2], 0);
  let w7: int = unwrap_or(acc[t - 7], 0);
  let w15: int = unwrap_or(acc[t - 15], 0);
  let w16: int = unwrap_or(acc[t - 16], 0);
  let s0: int = urot(w15, 7) ^ urot(w15, 18) ^ ((w15 >> 3) & 536870911);
  let s1: int = urot(w2, 17) ^ urot(w2, 19) ^ ((w2 >> 10) & 4194303);
  let w: int = usum(usum(usum(w16, s0), w7), s1);
  return sha_sched(msg, s, total, padded, bitlen, blk, t + 1, unwrap_or(push(acc, w), acc));
}
record H8 { a: int, b: int, c: int, d: int, e: int, f: int, g: int, h: int }
fn hexval(src: str, s: int): int {
  let b: int = unwrap_or(byte_at(src, s), 0);
  if b >= 48 { if b <= 57 { return b - 48; } else { } } else { }
  if b >= 97 { if b <= 102 { return b - 87; } else { } } else { }
  if b >= 65 { if b <= 70 { return b - 55; } else { } } else { }
  return 0 - 1;
}
fn kval_at(t: int): int {
  let k: str = "428a2f9871374491b5c0fbcfe9b5dba53956c25b59f111f1923f82a4ab1c5ed5d807aa9812835b01243185be550c7dc372be5d7480deb1fe9bdc06a7c19bf174e49b69c1efbe47860fc19dc6240ca1cc2de92c6f4a7484aa5cb0a9dc76f988da983e5152a831c66db00327c8bf597fc7c6e00bf3d5a7914706ca63511429296727b70a852e1b21384d2c6dfc53380d13650a7354766a0abb81c2c92e92722c85a2bfe8a1a81a664bc24b8b70c76c51a3d192e819d6990624f40e3585106aa07019a4c1161e376c082748774c34b0bcb5391c0cb34ed8aa4a5b9cca4f682e6ff3748f82ee78a5636f84c878148cc7020890befffaa4506cebbef9a3f7c67178f2";
  return kval_hex(k, t * 8, 0, 0);
}
fn kval_hex(k: str, pos: int, i: int, acc: int): int {
  if i >= 8 { return acc; } else { }
  let v: int = hexval(k, pos + i);
  if v == 0 - 1 { return 0; } else { }
  return kval_hex(k, pos, i + 1, acc * 16 + v);
}
fn sha_round(h: H8, sched: list<int,64>, t: int): H8 {
  if t >= 64 { return h; } else { }
  let w: int = unwrap_or(sched[t], 0);
  let k: int = kval_at(t);
  let s1: int = urot(h->e, 6) ^ urot(h->e, 11) ^ urot(h->e, 25);
  let ch: int = (h->e & h->f) ^ ((4294967295 ^ h->e) & h->g);
  let t1: int = usum(usum(usum(usum(h->h, s1), ch), k), w);
  let s0: int = urot(h->a, 2) ^ urot(h->a, 13) ^ urot(h->a, 22);
  let mj: int = (h->a & h->b) ^ (h->a & h->c) ^ (h->b & h->c);
  let t2: int = usum(s0, mj);
  return sha_round(H8(a: usum(t1, t2), b: h->a, c: h->b, d: h->c, e: usum(h->d, t1), f: h->e, g: h->f, h: h->g), sched, t + 1);
}
fn sha_init(): H8 {
  return H8(a: 1779033703, b: 3144134277, c: 1013904242, d: 2773480762, e: 1359893119, f: 2600822924, g: 528734635, h: 1541459225);
}
fn sha_blocks(msg: str, s: int, total: int, padded: int, bitlen: int, blk: int, h: H8): H8 {
  if blk >= padded { return h; } else { }
  let sched: list<int,64> = sha_sched(msg, s, total, padded, bitlen, blk, 0, []);
  let r: H8 = sha_round(h, sched, 0);
  let n: H8 = H8(a: usum(h->a, r->a), b: usum(h->b, r->b), c: usum(h->c, r->c), d: usum(h->d, r->d), e: usum(h->e, r->e), f: usum(h->f, r->f), g: usum(h->g, r->g), h: usum(h->h, r->h));
  return sha_blocks(msg, s, total, padded, bitlen, blk + 64, n);
}
fn sha_words(msg: str, s: int, total: int): H8 {
  let padded: int = sha_padded(total);
  let bitlen: int = total * 8;
  return sha_blocks(msg, s, total, padded, bitlen, 0, sha_init());
}
fn infer_call(src: str, f: int, fnstart: int, fnend: int, cs: int, cl: int, ss: int): TR {
  let r: FR = res_fn(src, f, cs, cl, ss);
  if has_err(r->d) { return TR(t: tscal(1), p: ss, d: r->d); } else { }
  if r->i == 0 - 1 { return TR(t: tscal(1), p: ss, d: derr(13, f, ss)); } else { }
  let ret: VS = fn_ret_span(src, r->i);
  if ret->tl == 0 { if ret->off == 1 { return TR(t: tscal(0), p: ss, d: dok()); } else { } return TR(t: tscal(1), p: ss, d: derr(11, f, ss)); } else { }
  return intern_ty(src, f, ret->ts, ret->ts + ret->tl, 0);
}
fn fn_ret_span(src: str, idx: int): VS {
  return fn_ret_at(src, idx, 0, 0);
}
fn fn_ret_at(src: str, idx: int, pos: int, seen: int): VS {
  let it: TI = tl_next(src, 0, pos, len(src));
  if it->k == 0 { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
  if it->k == 0 - 1 { return fn_ret_at(src, idx, it->p, seen); } else { }
  if it->k == 1 {
    if seen == idx { return fn_ret_parse(src, it->s, it->s + it->l); } else { }
    return fn_ret_at(src, idx, it->p, seen + 1);
  } else { }
  return fn_ret_at(src, idx, it->p, seen);
}
fn fn_ret_parse(src: str, s: int, end: int): VS {
  let t: Tok = next_tok(src, s);
  if t->k == 4 { if t->l == 1 { if unwrap_or(byte_at(src, t->s), 0) == 41 { return fn_ret_ann(src, t->p, end); } else { } } else { } } else { }
  return fn_ret_parse(src, t->p, end);
}
fn fn_ret_ann(src: str, pos: int, end: int): VS {
  let t: Tok = next_tok(src, tl_skip_ws(src, pos, end));
  if t->k == 4 { if t->l == 1 { if unwrap_or(byte_at(src, t->s), 0) == 58 { return fn_ret_span_end(src, t->p, end); } else { } } else { } } else { }
  return VS(off: 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok());
}
fn fn_ret_span_end(src: str, pos: int, end: int): VS {
  let an: VS = annot_span_end(src, pos, pos, 0);
  return VS(off: 0, k: 0, ts: an->ts, tl: an->tl, slot: 0, d: dok());
}
fn peek_colon(src: str, pos: int): bool {
  let t: Tok = next_tok(src, tl_skip_ws(src, pos, len(src)));
  if t->k == 4 { if t->l == 1 { if unwrap_or(byte_at(src, t->s), 0) == 58 { return true; } else { } } else { } } else { }
  return false;
}
fn flat_let(src: str, fnstart: int, useoff: int, ns: int, nl: int, pos: int, depth: int, best: int): int {
  if pos >= useoff { return best; } else { }
  let t: Tok = next_tok(src, pos);
  if t->k == 0 - 2 { return flat_let(src, fnstart, useoff, ns, nl, t->p, depth, best); } else { }
  if t->s >= useoff { return best; } else { }
  if t->k == 4 {
    if t->l == 1 {
      if tok_byte(src, t->s) == 123 { return flat_let(src, fnstart, useoff, ns, nl, t->p, depth + 1, best); } else { }
      if tok_byte(src, t->s) == 125 { return flat_let(src, fnstart, useoff, ns, nl, t->p, depth - 1, best); } else { }
    } else { }
    return flat_let(src, fnstart, useoff, ns, nl, t->p, depth, best);
  } else { }
  if t->k == 1 {
    if t->l == 3 { if beq(src, t->s, "let", 0, 3) { return flat_let_name(src, fnstart, useoff, ns, nl, t->p, depth, best); } else { } } else { }
  } else { }
  return flat_let(src, fnstart, useoff, ns, nl, t->p, depth, best);
}
fn flat_let_name(src: str, fnstart: int, useoff: int, ns: int, nl: int, pos: int, depth: int, best: int): int {
  let t: Tok = next_tok(src, pos);
  if t->k == 1 { if t->l == nl { if beq(src, t->s, src, ns, nl) { if depth == 1 { if peek_colon(src, t->p) { return flat_let(src, fnstart, useoff, ns, nl, t->p, depth, t->s); } else { } } else { } } else { } } else { } }
  return flat_let(src, fnstart, useoff, ns, nl, t->p, depth, best);
}
fn arm_open(src: str, pos: int, end: int, depth: int): int {
  if pos >= end { return 0 - 1; } else { }
  let t: Tok = next_tok(src, pos);
  if t->k == 0 - 2 { return arm_open(src, t->p, end, depth); } else { }
  if t->k == 4 {
    if t->l == 1 {
      if tok_byte(src, t->s) == 123 { if depth == 0 { return t->s; } else { } return arm_open(src, t->p, end, depth + 1); } else { }
      if tok_byte(src, t->s) == 125 { return arm_open(src, t->p, end, depth - 1); } else { }
    } else { }
    return arm_open(src, t->p, end, depth);
  } else { }
  return arm_open(src, t->p, end, depth);
}
fn arm_end(src: str, pos: int, end: int): int {
  return tl_balanced(src, pos, end, 0);
}
fn arm_find(src: str, f: int, fnstart: int, fnend: int, useoff: int, ns: int, nl: int, skip: int): VS {
  let total: int = scope_slot(src, f, fnstart, fnend, fnend);
  return arm_scan(src, f, fnstart, useoff, ns, nl, skip, fnstart, len(src), 0, total);
}
fn arm_scan(src: str, f: int, fnstart: int, useoff: int, ns: int, nl: int, skip: int, pos: int, end: int, mdepth: int, total: int): VS {
  if pos >= end { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
  let t: Tok = next_tok(src, pos);
  if t->k == 0 - 2 { return arm_scan(src, f, fnstart, useoff, ns, nl, skip, t->p, end, mdepth, total); } else { }
  if t->k == 1 {
    if t->l == 5 { if beq(src, t->s, "match", 0, 5) { return arm_match(src, f, fnstart, useoff, ns, nl, skip, t->p, end, mdepth, total); } else { } } else { }
  } else { }
  return arm_scan(src, f, fnstart, useoff, ns, nl, skip, t->p, end, mdepth, total);
}
fn arm_match(src: str, f: int, fnstart: int, useoff: int, ns: int, nl: int, skip: int, pos: int, end: int, mdepth: int, total: int): VS {
  let ao: int = arm_open(src, pos, end, 0);
  if ao == 0 - 1 { return arm_scan(src, f, fnstart, useoff, ns, nl, skip, pos, end, mdepth, total); } else { }
  let ae: int = arm_end(src, ao, end);
  if useoff < ao { return arm_scan(src, f, fnstart, useoff, ns, nl, skip, ae, end, mdepth, total); } else { }
  if useoff >= ae { return arm_scan(src, f, fnstart, useoff, ns, nl, skip, ae, end, mdepth, total); } else { }
  let inner: VS = arm_scan(src, f, fnstart, useoff, ns, nl, skip, ao + 1, ae, mdepth + 1, total);
  if inner->off == 0 - 1 { } else { return inner; }
  let ss: int = tl_skip_ws(src, pos, ao);
  return arm_binds(src, f, ss, ao - ss, ao, ae, end, useoff, ns, nl, skip, mdepth, total, 0, ao + 1);
}
fn arm_binds(src: str, f: int, ss: int, sl: int, ao: int, ae: int, end: int, useoff: int, ns: int, nl: int, skip: int, mdepth: int, total: int, armidx: int, pos: int): VS {
  let p: int = tl_skip_ws(src, pos, ae);
  if p >= ae { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
  let b: int = unwrap_or(byte_at(src, p), 0);
  if b == 125 { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
  return arm_pat(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, p);
}
fn arm_pat(src: str, f: int, ss: int, sl: int, ao: int, ae: int, end: int, useoff: int, ns: int, nl: int, skip: int, mdepth: int, total: int, armidx: int, p: int): VS {
  let t: Tok = next_tok(src, p);
  if t->k == 0 - 2 { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
  if t->k == 1 {
    if beq(src, t->s, "ok", 0, 2) { if t->l == 2 { return arm_pat_ok(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, t); } else { } } else { }
    if beq(src, t->s, "err", 0, 3) { if t->l == 3 { return arm_pat_ok(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, t); } else { } } else { }
    if beq(src, t->s, "true", 0, 4) { if t->l == 4 { return arm_after_pat(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, t->p); } else { } } else { }
    if beq(src, t->s, "false", 0, 5) { if t->l == 5 { return arm_after_pat(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, t->p); } else { } } else { }
    if t->l == 1 { if unwrap_or(byte_at(src, t->s), 0) == 95 { return arm_after_pat(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, t->p); } else { } } else { }
    return arm_bare(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, t);
  } else { }
  return arm_after_pat(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, t->p);
}
fn arm_pat_ok(src: str, f: int, ss: int, sl: int, ao: int, ae: int, end: int, useoff: int, ns: int, nl: int, skip: int, mdepth: int, total: int, armidx: int, t: Tok): VS {
  let nx: Tok = next_tok(src, tl_skip_ws(src, t->p, ae));
  if nx->k == 4 { if nx->l == 1 { if unwrap_or(byte_at(src, nx->s), 0) == 40 { return arm_pat_ctor(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, t); } else { } } else { } } else { }
  return arm_bare(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, t);
}
fn arm_pat_ctor(src: str, f: int, ss: int, sl: int, ao: int, ae: int, end: int, useoff: int, ns: int, nl: int, skip: int, mdepth: int, total: int, armidx: int, t: Tok): VS {
  if t->l == 2 { return arm_ctor(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, t->p, 2); } else { }
  return arm_ctor(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, t->p, 3);
}
fn arm_ctor(src: str, f: int, ss: int, sl: int, ao: int, ae: int, end: int, useoff: int, ns: int, nl: int, skip: int, mdepth: int, total: int, armidx: int, pos: int, kind: int): VS {
  let o: Tok = next_tok(src, tl_skip_ws(src, pos, ae));
  if o->k == 4 { if o->l == 1 { if unwrap_or(byte_at(src, o->s), 0) == 40 { } else { return arm_after_pat(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, o->p); } } else { return arm_after_pat(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, o->p); } } else { return arm_after_pat(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, o->p); }
  let nm: Tok = next_tok(src, o->p);
  if nm->k == 1 { if nm->l == nl { if beq(src, nm->s, src, ns, nl) { if nm->s == skip { } else { if is_reserved_w(src, nm->s, nm->l) { } else { return arm_hit(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, kind, nm); } } } else { } } else { } }
  return arm_skip(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, nm->p);
}
fn arm_body_of(src: str, pos: int, ae: int): VS {
  let t: Tok = next_tok(src, pos);
  if t->s >= ae { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
  if t->k == 0 - 2 { return arm_body_of(src, t->p, ae); } else { }
  if t->k == 0 { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
  if t->k == 4 { if t->l == 1 { if unwrap_or(byte_at(src, t->s), 0) == 61 { return arm_body_gt(src, t->p, ae, t); } else { } } else { } } else { }
  if t->p <= pos { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
  return arm_body_of(src, t->p, ae);
}
fn arm_body_gt(src: str, pos: int, ae: int, e: Tok): VS {
  let g: Tok = next_tok(src, pos);
  if g->k == 4 { if g->l == 1 { if unwrap_or(byte_at(src, g->s), 0) == 62 { if e->s + 1 == g->s { return arm_body_bo(src, g->p, ae); } else { } } else { } } else { } } else { }
  return arm_body_of(src, g->p, ae);
}
fn arm_body_bo(src: str, pos: int, ae: int): VS {
  let b: Tok = next_tok(src, tl_skip_ws(src, pos, ae));
  if b->k == 4 { if b->l == 1 { if unwrap_or(byte_at(src, b->s), 0) == 123 { return arm_body_be(src, b->s, ae); } else { } } else { } } else { }
  return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok());
}
fn arm_body_be(src: str, bo: int, ae: int): VS {
  let be: int = tl_balanced(src, bo, ae, 0);
  return VS(off: bo, k: 0, ts: be, tl: 0, slot: 0, d: dok());
}
fn arm_hit(src: str, f: int, ss: int, sl: int, ao: int, ae: int, end: int, useoff: int, ns: int, nl: int, skip: int, mdepth: int, total: int, armidx: int, kind: int, nm: Tok): VS {
  let c: Tok = next_tok(src, nm->p);
  if c->k == 4 { if c->l == 1 { if unwrap_or(byte_at(src, c->s), 0) == 41 { return arm_hit_in(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, kind, nm); } else { } } else { } }
  return arm_after_pat(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, nm->p);
}
fn arm_cont(src: str, f: int, ss: int, sl: int, ao: int, ae: int, end: int, useoff: int, ns: int, nl: int, skip: int, mdepth: int, total: int, armidx: int, be: int): VS {
  let nx: Tok = next_tok(src, be);
  if nx->k == 4 { if nx->l == 1 { if unwrap_or(byte_at(src, nx->s), 0) == 44 { return arm_binds(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx + 1, nx->p); } else { } } else { } } else { }
  return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok());
}
fn arm_skip(src: str, f: int, ss: int, sl: int, ao: int, ae: int, end: int, useoff: int, ns: int, nl: int, skip: int, mdepth: int, total: int, armidx: int, pos: int): VS {
  let rg: VS = arm_body_of(src, pos, ae);
  if rg->off == 0 - 1 { return arm_after_pat(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, pos); } else { }
  return arm_cont(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, rg->ts);
}
fn arm_hit_in(src: str, f: int, ss: int, sl: int, ao: int, ae: int, end: int, useoff: int, ns: int, nl: int, skip: int, mdepth: int, total: int, armidx: int, kind: int, nm: Tok): VS {
  let rg: VS = arm_body_of(src, nm->p, ae);
  if rg->off == 0 - 1 { return arm_after_pat(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, nm->p); } else { }
  if rg->off <= useoff { if useoff < rg->ts { return VS(off: nm->s, k: kind, ts: ss, tl: sl, slot: total + armidx + mdepth * 8, d: dok()); } else { } } else { }
  return arm_cont(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, rg->ts);
}
fn arm_bare(src: str, f: int, ss: int, sl: int, ao: int, ae: int, end: int, useoff: int, ns: int, nl: int, skip: int, mdepth: int, total: int, armidx: int, t: Tok): VS {
  let e: Tok = next_tok(src, t->p);
  if e->k == 4 { if e->l == 1 { if unwrap_or(byte_at(src, e->s), 0) == 61 { } else { return arm_after_pat(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, e->p); } } else { return arm_after_pat(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, e->p); } } else { return arm_after_pat(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, e->p); }
  let g: Tok = next_tok(src, e->p);
  if g->k == 4 { if g->l == 1 { if unwrap_or(byte_at(src, g->s), 0) == 62 { if e->s + 1 == g->s { } else { return arm_after_pat(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, g->p); } } else { return arm_after_pat(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, g->p); } } else { return arm_after_pat(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, g->p); } } else { return arm_after_pat(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, g->p); }
  if t->l == nl { if beq(src, t->s, src, ns, nl) { if t->s == skip { } else { if is_reserved_w(src, t->s, t->l) { } else { return arm_bare_in(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, t, g); } } } else { } } else { }
  return arm_after_pat(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, g->p);
}
fn arm_bare_in(src: str, f: int, ss: int, sl: int, ao: int, ae: int, end: int, useoff: int, ns: int, nl: int, skip: int, mdepth: int, total: int, armidx: int, t: Tok, g: Tok): VS {
  let rg: VS = arm_body_bo(src, g->p, ae);
  if rg->off == 0 - 1 { return arm_after_pat(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, g->p); } else { }
  if rg->off <= useoff { if useoff < rg->ts { return VS(off: t->s, k: 4, ts: ss, tl: sl, slot: total + armidx + mdepth * 8, d: dok()); } else { } } else { }
  return arm_cont(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx, rg->ts);
}
fn arm_after_pat(src: str, f: int, ss: int, sl: int, ao: int, ae: int, end: int, useoff: int, ns: int, nl: int, skip: int, mdepth: int, total: int, armidx: int, pos: int): VS {
  let e: Tok = next_tok(src, tl_skip_ws(src, pos, ae));
  if e->k == 4 { if e->l == 1 { if unwrap_or(byte_at(src, e->s), 0) == 61 { } else { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } } else { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } } else { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); }
  let g: Tok = next_tok(src, e->p);
  if g->k == 4 { if g->l == 1 { if unwrap_or(byte_at(src, g->s), 0) == 62 { if e->s + 1 == g->s { } else { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } } else { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } } else { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } } else { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); }
  let bo: int = tl_skip_ws(src, g->p, ae);
  let be: int = tl_balanced(src, bo, ae, 0);
  let nx: Tok = next_tok(src, be);
  if nx->k == 4 { if nx->l == 1 { if unwrap_or(byte_at(src, nx->s), 0) == 44 { return arm_binds(src, f, ss, sl, ao, ae, end, useoff, ns, nl, skip, mdepth, total, armidx + 1, nx->p); } else { } } else { } } else { }
  return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok());
}
fn hdr_find(src: str, fnstart: int, fnend: int, ns: int, nl: int): VS {
  let k: Tok = next_tok(src, fnstart);
  let nm: Tok = next_tok(src, k->p);
  let op: Tok = next_tok(src, nm->p);
  return hdr_at(src, fnstart, fnend, ns, nl, op->p, 0);
}
fn hdr_at(src: str, fnstart: int, fnend: int, ns: int, nl: int, pos: int, idx: int): VS {
  if pos >= fnend { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
  let t: Tok = next_tok(src, pos);
  if t->k == 0 - 2 { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
  if t->k == 4 {
    if t->l == 1 {
      if tok_byte(src, t->s) == 41 { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
      if tok_byte(src, t->s) == 123 { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
    } else { }
    return hdr_at(src, fnstart, fnend, ns, nl, t->p, idx);
  } else { }
  if t->k == 1 {
    return hdr_ident(src, fnstart, fnend, ns, nl, t, idx);
  } else { }
  return hdr_at(src, fnstart, fnend, ns, nl, t->p, idx);
}
fn hdr_ident(src: str, fnstart: int, fnend: int, ns: int, nl: int, t: Tok, idx: int): VS {
  let an: VS = annot_span(src, t->p);
  if an->tl == 0 { return hdr_at(src, fnstart, fnend, ns, nl, t->p, idx); } else { }
  if t->l == nl { if beq(src, t->s, src, ns, nl) { return VS(off: t->s, k: 1, ts: an->ts, tl: an->tl, slot: idx, d: dok()); } else { } } else { }
  return hdr_at(src, fnstart, fnend, ns, nl, an->ts + an->tl, idx + 1);
}
fn annot_span(src: str, nameoff: int): VS {
  return annot_at(src, nameoff, nameoff);
}
fn annot_at(src: str, nameoff: int, pos: int): VS {
  if pos >= len(src) { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
  let t: Tok = next_tok(src, pos);
  if t->k == 0 - 2 { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
  if t->k == 4 {
    if t->l == 1 {
      if tok_byte(src, t->s) == 58 { return annot_span_end(src, t->p, t->p, 0); } else { }
      if tok_byte(src, t->s) == 61 { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
      if tok_byte(src, t->s) == 59 { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
      if tok_byte(src, t->s) == 123 { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
      if tok_byte(src, t->s) == 125 { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
    } else { }
    return annot_at(src, nameoff, t->p);
  } else { }
  return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok());
}
fn annot_span_end(src: str, pos: int, start: int, depth: int): VS {
  let p: int = tl_skip_ws(src, pos, len(src));
  if p >= len(src) { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
  let t: Tok = next_tok(src, p);
  if t->k == 0 - 2 { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
  if t->k == 4 {
    if t->l == 1 {
      if tok_byte(src, t->s) == 60 { return annot_span_end(src, t->p, start, depth + 1); } else { }
      if tok_byte(src, t->s) == 62 { if depth == 0 { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { } return annot_span_end(src, t->p, start, depth - 1); } else { }
      if depth == 0 {
        if tok_byte(src, t->s) == 61 { return VS(off: 0, k: 0, ts: start, tl: p - start, slot: 0, d: dok()); } else { }
        if tok_byte(src, t->s) == 44 { return VS(off: 0, k: 0, ts: start, tl: p - start, slot: 0, d: dok()); } else { }
        if tok_byte(src, t->s) == 41 { return VS(off: 0, k: 0, ts: start, tl: p - start, slot: 0, d: dok()); } else { }
        if tok_byte(src, t->s) == 59 { return VS(off: 0, k: 0, ts: start, tl: p - start, slot: 0, d: dok()); } else { }
        if tok_byte(src, t->s) == 123 { return VS(off: 0, k: 0, ts: start, tl: p - start, slot: 0, d: dok()); } else { }
        if tok_byte(src, t->s) == 125 { return VS(off: 0, k: 0, ts: start, tl: p - start, slot: 0, d: dok()); } else { }
      } else { }
    } else {
      if depth == 0 {
        if tok_byte(src, t->s) == 58 { if t->l == 2 { return annot_span_end(src, t->p, start, depth); } else { } } else { }
      } else { }
      if t->l == 2 {
        if tok_byte(src, t->s) == 62 { if tok_byte(src, t->s + 1) == 62 { if depth == 0 { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { } if depth == 1 { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { } return annot_span_end(src, t->p, start, depth - 2); } else { } } else { }
      } else { }
    }
  } else { }
  return annot_span_end(src, t->p, start, depth);
}
fn res_var_one(src: str, f: int, fnstart: int, fnend: int, useoff: int, ns: int, nl: int, lo: int, po: VS, mo: VS): VS {
  if lo == 0 - 1 { } else {
    return res_var_lo(src, f, fnstart, fnend, lo, nl);
  }
  if po->off == 0 - 1 { } else { return po; }
  return mo;
}
fn res_var_lo(src: str, f: int, fnstart: int, fnend: int, lo: int, nl: int): VS {
  let an: VS = annot_span(src, lo + nl);
  let sl: int = scope_slot(src, f, fnstart, fnend, lo);
  return VS(off: lo, k: 0, ts: an->ts, tl: an->tl, slot: sl, d: dok());
}

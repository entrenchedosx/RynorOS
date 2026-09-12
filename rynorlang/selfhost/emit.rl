// selfhost backend BE-A: scalar machine-code emitter (direct span -> x86-64).
// Entry: be_main(src, f) prints lowercase hex of a normal RYNX v2 image
// (28-byte header + code, no data) and returns D. Two passes over the same
// span walkers: be_size counts, be_emit prints; counted-vs-printed mismatch
// fails closed (28). Consumes pgm_check-clean core programs only; be_main
// re-verifies via pgm_check first and emits nothing for invalid input.
// BE-A subset: exactly `fn main(): int` (no params), scalar int/bool
// lets + `return`, arithmetic/logic/shift/compare, no calls/division/
// aggregates/control. Anything else is 25 (unsupported backend construct).
// Magic bytes below are decimal ASCII: 82 R, 89 Y, 78 N, 88 X.
// Register model (frameless-leaf is FORBIDDEN here; rbp frames always):
// homes at [rbp-8*slot] (slot = res_var slot, 1-based: scope_slot(lo)
// includes the let itself, so first let is slot 1 -> [rbp-8]; main has
// no params); RAX accumulates expression values; RCX is the
// single scratch (shift counts, right operands); RDX/RBX untouched except
// the _start exit sequence. rsp stays 16-aligned at every boundary:
// prologue pushes rbp (8) then sub FRAME (multiple of 16); eval push/pop
// pairs balance; leave restores. No red zone use, no SSE, no PIC.
// BE-B calling convention (SysV-subset, verified against the host
// compiler): scalar args in rdi, rsi, rdx, rcx, r8, r9 (max 6, no
// stack args); return in rax; callee preserves rbx, rbp, rsp,
// r12-r15 and all argument registers (bodies use rax/rcx only);
// caller marshals evaluated args on the machine stack (push each,
// pop into regs in reverse, call), so the push/pop pairs balance
// and the net rsp effect of a call sequence is zero. rsp is
// 16-aligned at every function entry/exit and statement boundary;
// interior expression push depth is inherited at call sites (no
// SSE or stack-arg traffic exists that could fault).
fn e_b(b: int, acc: int): int {
  print(hexch((b >> 4) & 15));
  print(hexch(b & 15));
  return acc + 1;
}
fn e_le32(v: int, acc: int): int {
  let a0: int = e_b(v & 255, acc);
  let a1: int = e_b((v >> 8) & 255, a0);
  let a2: int = e_b((v >> 16) & 255, a1);
  let a3: int = e_b((v >> 24) & 255, a2);
  return a3;
}
fn e_le64(v: int, acc: int): int {
  let a0: int = e_le32(v & 4294967295, acc);
  let a1: int = e_le32((v >> 32) & 4294967295, a0);
  return a1;
}
fn be_rnyx_header(code: int, acc: int): int {
  let a00: int = e_b(82, acc);
  let a01: int = e_b(89, a00);
  let a02: int = e_b(78, a01);
  let a03: int = e_b(88, a02);
  let a04: int = e_b(2, a03);
  let a05: int = e_b(0, a04);
  let a06: int = e_b(1, a05);
  let a07: int = e_b(0, a06);
  let a08: int = e_b(28, a07);
  let a09: int = e_b(0, a08);
  let a10: int = e_b(0, a09);
  let a11: int = e_b(0, a10);
  let a12: int = e_le32(0, a11);
  let a13: int = e_le32(code, a12);
  let a14: int = e_le32(0, a13);
  let a15: int = e_le32(0, a14);
  return a15;
}
fn sz_rnyx_header(): int {
  return 28;
}
fn sz_start(): int {
  return 14;
}
fn e_start(mainoff: int, acc: int): int {
  let disp: int = mainoff - 5;
  let a0: int = e_b(232, acc);
  let a1: int = e_le32(disp, a0);
  let a2: int = e_b(137, a1);
  let a3: int = e_b(195, a2);
  let a4: int = e_b(184, a3);
  let a5: int = e_le32(0, a4);
  let a6: int = e_b(205, a5);
  let a7: int = e_b(128, a6);
  return a7;
}
fn sz_frame(nlets: int): int {
  if nlets == 0 { return 4; } else { }
  return 11;
}
fn e_frame(nlets: int, acc: int): int {
  let a0: int = e_b(85, acc);
  let a1: int = e_b(72, a0);
  let a2: int = e_b(137, a1);
  let a3: int = e_b(229, a2);
  if nlets == 0 { return a3; } else { }
  return e_frame_sub(nlets, a3);
}
fn e_frame_sub(nlets: int, acc: int): int {
  let a0: int = e_b(72, acc);
  let a1: int = e_b(129, a0);
  let a2: int = e_b(236, a1);
  let a3: int = e_le32(be_frame_bytes(nlets), a2);
  return a3;
}
fn be_frame_bytes(nlets: int): int {
  return ((nlets * 8 + 15) / 16) * 16;
}
fn sz_leave_ret(): int {
  return 2;
}
fn e_leave_ret(acc: int): int {
  let a0: int = e_b(201, acc);
  let a1: int = e_b(195, a0);
  return a1;
}
fn sz_push_rax(): int {
  return 1;
}
fn e_push_rax(acc: int): int {
  return e_b(80, acc);
}
fn sz_pop_rax(): int {
  return 1;
}
fn e_pop_rax(acc: int): int {
  return e_b(88, acc);
}
fn sz_pop_rcx(): int {
  return 1;
}
fn e_pop_rcx(acc: int): int {
  return e_b(89, acc);
}
fn sz_mov_rcx_rax(): int {
  return 3;
}
fn e_mov_rcx_rax(acc: int): int {
  let a0: int = e_b(72, acc);
  let a1: int = e_b(137, a0);
  let a2: int = e_b(193, a1);
  return a2;
}
fn sz_mov_ebx_eax(): int {
  return 2;
}
fn e_mov_ebx_eax(acc: int): int {
  let a0: int = e_b(137, acc);
  let a1: int = e_b(195, a0);
  return a1;
}
fn sz_mov_eax_0(): int {
  return 5;
}
fn e_mov_eax_0(acc: int): int {
  let a0: int = e_b(184, acc);
  let a1: int = e_le32(0, a0);
  return a1;
}
fn sz_int80(): int {
  return 2;
}
fn e_int80(acc: int): int {
  let a0: int = e_b(205, acc);
  let a1: int = e_b(128, a0);
  return a1;
}
fn be_home_disp(slot: int): int {
  return slot * 8;
}
fn be_home_slot(v: VS): int {
  if v->k == 1 { return v->slot + 1; } else { }
  return v->slot;
}
fn sz_mov_home_rax(slot: int): int {
  if slot <= 15 { return 4; } else { }
  return 7;
}
fn e_mov_home_rax(slot: int, acc: int): int {
  if slot <= 15 { return e_mov_home_8(slot, acc); } else { }
  return e_mov_home_32(slot, acc);
}
fn e_mov_home_8(slot: int, acc: int): int {
  let a0: int = e_b(72, acc);
  let a1: int = e_b(137, a0);
  let a2: int = e_b(69, a1);
  let a3: int = e_b(256 - be_home_disp(slot), a2);
  return a3;
}
fn e_mov_home_32(slot: int, acc: int): int {
  let a0: int = e_b(72, acc);
  let a1: int = e_b(137, a0);
  let a2: int = e_b(133, a1);
  let a3: int = e_le32(0 - be_home_disp(slot), a2);
  return a3;
}
fn sz_mov_rax_home(slot: int): int {
  if slot <= 15 { return 4; } else { }
  return 7;
}
fn e_mov_rax_home(slot: int, acc: int): int {
  if slot <= 15 { return e_load_home_8(slot, acc); } else { }
  return e_load_home_32(slot, acc);
}
fn e_load_home_8(slot: int, acc: int): int {
  let a0: int = e_b(72, acc);
  let a1: int = e_b(139, a0);
  let a2: int = e_b(69, a1);
  let a3: int = e_b(256 - be_home_disp(slot), a2);
  return a3;
}
fn e_load_home_32(slot: int, acc: int): int {
  let a0: int = e_b(72, acc);
  let a1: int = e_b(139, a0);
  let a2: int = e_b(133, a1);
  let a3: int = e_le32(0 - be_home_disp(slot), a2);
  return a3;
}
fn sz_mov_rax_imm(v: int): int {
  if v >= 0 { if v <= 4294967295 { return 5; } else { } } else { }
  if v >= 0 - 2147483648 { return 7; } else { }
  return 10;
}
fn e_mov_rax_imm(v: int, acc: int): int {
  if v >= 0 { if v <= 4294967295 { return e_mov_eax_imm(v, acc); } else { } } else { }
  if v >= 0 - 2147483648 { return e_mov_rax_imm32(v, acc); } else { }
  return e_movabs(v, acc);
}
fn e_mov_eax_imm(v: int, acc: int): int {
  let a0: int = e_b(184, acc);
  let a1: int = e_le32(v, a0);
  return a1;
}
fn e_mov_rax_imm32(v: int, acc: int): int {
  let a0: int = e_b(72, acc);
  let a1: int = e_b(199, a0);
  let a2: int = e_b(192, a1);
  let a3: int = e_le32(v, a2);
  return a3;
}
fn e_movabs(v: int, acc: int): int {
  let a0: int = e_b(72, acc);
  let a1: int = e_b(184, a0);
  let a2: int = e_le64(v, a1);
  return a2;
}
fn sz_add_rax_rcx(): int {
  return 3;
}
fn e_add_rax_rcx(acc: int): int {
  let a0: int = e_b(72, acc);
  let a1: int = e_b(1, a0);
  let a2: int = e_b(200, a1);
  return a2;
}
fn sz_sub_rax_rcx(): int {
  return 3;
}
fn e_sub_rax_rcx(acc: int): int {
  let a0: int = e_b(72, acc);
  let a1: int = e_b(41, a0);
  let a2: int = e_b(200, a1);
  return a2;
}
fn sz_imul_rax_rcx(): int {
  return 4;
}
fn e_imul_rax_rcx(acc: int): int {
  let a0: int = e_b(72, acc);
  let a1: int = e_b(15, a0);
  let a2: int = e_b(175, a1);
  let a3: int = e_b(193, a2);
  return a3;
}
fn sz_and_rax_rcx(): int {
  return 3;
}
fn e_and_rax_rcx(acc: int): int {
  let a0: int = e_b(72, acc);
  let a1: int = e_b(33, a0);
  let a2: int = e_b(200, a1);
  return a2;
}
fn sz_or_rax_rcx(): int {
  return 3;
}
fn e_or_rax_rcx(acc: int): int {
  let a0: int = e_b(72, acc);
  let a1: int = e_b(9, a0);
  let a2: int = e_b(200, a1);
  return a2;
}
fn sz_xor_rax_rcx(): int {
  return 3;
}
fn e_xor_rax_rcx(acc: int): int {
  let a0: int = e_b(72, acc);
  let a1: int = e_b(49, a0);
  let a2: int = e_b(200, a1);
  return a2;
}
fn sz_neg_rax(): int {
  return 3;
}
fn e_neg_rax(acc: int): int {
  let a0: int = e_b(72, acc);
  let a1: int = e_b(247, a0);
  let a2: int = e_b(216, a1);
  return a2;
}
fn sz_not_rax(): int {
  return 3;
}
fn e_not_rax(acc: int): int {
  let a0: int = e_b(72, acc);
  let a1: int = e_b(247, a0);
  let a2: int = e_b(208, a1);
  return a2;
}
fn sz_lnot_eax(): int {
  return 3;
}
fn e_lnot_eax(acc: int): int {
  let a0: int = e_b(131, acc);
  let a1: int = e_b(240, a0);
  let a2: int = e_b(1, a1);
  return a2;
}
fn sz_shl_rax_cl(): int {
  return 3;
}
fn e_shl_rax_cl(acc: int): int {
  let a0: int = e_b(72, acc);
  let a1: int = e_b(211, a0);
  let a2: int = e_b(224, a1);
  return a2;
}
fn sz_sar_rax_cl(): int {
  return 3;
}
fn e_sar_rax_cl(acc: int): int {
  let a0: int = e_b(72, acc);
  let a1: int = e_b(211, a0);
  let a2: int = e_b(248, a1);
  return a2;
}
fn sz_cmp_rax_rcx(): int {
  return 3;
}
fn e_cmp_rax_rcx(acc: int): int {
  let a0: int = e_b(72, acc);
  let a1: int = e_b(57, a0);
  let a2: int = e_b(200, a1);
  return a2;
}
fn sz_setcc(cc: int): int {
  return 3;
}
fn e_setcc(cc: int, acc: int): int {
  let a0: int = e_b(15, acc);
  let a1: int = e_b(cc, a0);
  let a2: int = e_b(192, a1);
  return a2;
}
fn sz_movzx_eax_al(): int {
  return 3;
}
fn e_movzx_eax_al(acc: int): int {
  let a0: int = e_b(15, acc);
  let a1: int = e_b(182, a0);
  let a2: int = e_b(192, a1);
  return a2;
}
fn be_op_match(src: str, lv: int, t: Tok): int {
  if lv == 0 { if t->l == 2 { if beq(src, t->s, "||", 0, 2) { return 1; } else { } } else { } return 0; } else { }
  if lv == 1 { if t->l == 2 { if beq(src, t->s, "&&", 0, 2) { return 2; } else { } } else { } return 0; } else { }
  if lv == 2 { if t->l == 1 { if tok_byte(src, t->s) == 124 { return 3; } else { } } else { } return 0; } else { }
  if lv == 3 { if t->l == 1 { if tok_byte(src, t->s) == 94 { return 4; } else { } } else { } return 0; } else { }
  if lv == 4 { if t->l == 1 { if tok_byte(src, t->s) == 38 { return 5; } else { } } else { } return 0; } else { }
  if lv == 5 { return be_op_eq(src, t); } else { }
  if lv == 6 { return be_op_rel(src, t); } else { }
  if lv == 7 { return be_op_shift(src, t); } else { }
  if lv == 8 { return be_op_add(src, t); } else { }
  if lv == 9 { return be_op_mul(src, t); } else { }
  return 0;
}
fn be_op_eq(src: str, t: Tok): int {
  if t->l == 2 { if beq(src, t->s, "==", 0, 2) { return 6; } else { } } else { }
  if t->l == 2 { if beq(src, t->s, "!=", 0, 2) { return 7; } else { } } else { }
  return 0;
}
fn be_op_rel(src: str, t: Tok): int {
  if t->l == 1 { if tok_byte(src, t->s) == 60 { return 8; } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 62 { return 9; } else { } } else { }
  if t->l == 2 { if beq(src, t->s, "<=", 0, 2) { return 10; } else { } } else { }
  if t->l == 2 { if beq(src, t->s, ">=", 0, 2) { return 11; } else { } } else { }
  return 0;
}
fn be_op_shift(src: str, t: Tok): int {
  if t->l == 2 { if beq(src, t->s, "<<", 0, 2) { return 12; } else { } } else { }
  if t->l == 2 { if beq(src, t->s, ">>", 0, 2) { return 13; } else { } } else { }
  return 0;
}
fn be_op_add(src: str, t: Tok): int {
  if t->l == 1 { if tok_byte(src, t->s) == 43 { return 14; } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 45 { return 15; } else { } } else { }
  return 0;
}
fn be_op_mul(src: str, t: Tok): int {
  if t->l == 1 { if tok_byte(src, t->s) == 42 { return 16; } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 47 { return 17; } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 37 { return 18; } else { } } else { }
  return 0;
}
fn be_combine_size(op: int): int {
  if op == 16 { return 9; } else { }
  if op == 6 { return 14; } else { }
  if op == 7 { return 14; } else { }
  if op == 8 { return 14; } else { }
  if op == 9 { return 14; } else { }
  if op == 10 { return 14; } else { }
  if op == 11 { return 14; } else { }
  return 8;
}
fn be_combine_emit(op: int, acc: int): int {
  if op == 14 { return e_add_rax_rcx(acc); } else { }
  if op == 15 { return e_sub_rax_rcx(acc); } else { }
  if op == 16 { return e_imul_rax_rcx(acc); } else { }
  if op == 1 { return e_or_rax_rcx(acc); } else { }
  if op == 2 { return e_and_rax_rcx(acc); } else { }
  if op == 3 { return e_or_rax_rcx(acc); } else { }
  if op == 4 { return e_xor_rax_rcx(acc); } else { }
  if op == 5 { return e_and_rax_rcx(acc); } else { }
  if op == 12 { return e_shl_rax_cl(acc); } else { }
  if op == 13 { return e_sar_rax_cl(acc); } else { }
  return be_combine_cmp(op, acc);
}
fn be_combine_cmp(op: int, acc: int): int {
  let a0: int = e_cmp_rax_rcx(acc);
  let a1: int = e_setcc(be_cc(op), a0);
  let a2: int = e_movzx_eax_al(a1);
  return a2;
}
fn be_cc(op: int): int {
  if op == 6 { return 148; } else { }
  if op == 7 { return 149; } else { }
  if op == 8 { return 156; } else { }
  if op == 10 { return 158; } else { }
  if op == 9 { return 159; } else { }
  return 157;
}
record BZ { p: int, n: int, c: int, o: int }
fn be_s_level(src: str, f: int, fs: int, fe: int, lv: int, pos: int, end: int): BZ {
  if lv == 10 { return be_s_unary(src, f, fs, fe, pos, end); } else { }
  let l: BZ = be_s_level(src, f, fs, fe, lv + 1, pos, end);
  if l->c == 0 { } else { return l; }
  return be_s_rest(src, f, fs, fe, lv, l->p, end, l->n);
}
fn be_s_rest(src: str, f: int, fs: int, fe: int, lv: int, pos: int, end: int, acc: int): BZ {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return BZ(p: pos, n: acc, c: 0, o: 0); } else { }
  if t->k == 4 { return be_s_rest_op(src, f, fs, fe, lv, pos, end, acc, t); } else { }
  return BZ(p: pos, n: acc, c: 0, o: 0);
}
fn be_s_rest_op(src: str, f: int, fs: int, fe: int, lv: int, pos: int, end: int, acc: int, t: Tok): BZ {
  let op: int = be_op_match(src, lv, t);
  if op == 0 { return BZ(p: pos, n: acc, c: 0, o: 0); } else { }
  if op == 17 { return BZ(p: pos, n: 0, c: 25, o: t->s); } else { }
  if op == 18 { return BZ(p: pos, n: 0, c: 25, o: t->s); } else { }
  let r: BZ = be_s_level(src, f, fs, fe, lv + 1, t->p, end);
  if r->c == 0 { } else { return r; }
  return be_s_rest(src, f, fs, fe, lv, r->p, end, acc + r->n + be_combine_size(op));
}
fn be_e_level(src: str, f: int, fs: int, fe: int, lv: int, pos: int, end: int, acc: int): BZ {
  if lv == 10 { return be_e_unary(src, f, fs, fe, pos, end, acc); } else { }
  let l: BZ = be_e_level(src, f, fs, fe, lv + 1, pos, end, acc);
  if l->c == 0 { } else { return l; }
  return be_e_rest(src, f, fs, fe, lv, l->p, end, l->n);
}
fn be_e_rest(src: str, f: int, fs: int, fe: int, lv: int, pos: int, end: int, acc: int): BZ {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return BZ(p: pos, n: acc, c: 0, o: 0); } else { }
  if t->k == 4 { return be_e_rest_op(src, f, fs, fe, lv, pos, end, acc, t); } else { }
  return BZ(p: pos, n: acc, c: 0, o: 0);
}
fn be_e_rest_op(src: str, f: int, fs: int, fe: int, lv: int, pos: int, end: int, acc: int, t: Tok): BZ {
  let op: int = be_op_match(src, lv, t);
  if op == 0 { return BZ(p: pos, n: acc, c: 0, o: 0); } else { }
  if op == 17 { return BZ(p: pos, n: 0, c: 25, o: t->s); } else { }
  if op == 18 { return BZ(p: pos, n: 0, c: 25, o: t->s); } else { }
  let a0: int = e_push_rax(acc);
  let r: BZ = be_e_level(src, f, fs, fe, lv + 1, t->p, end, a0);
  if r->c == 0 { } else { return r; }
  let a1: int = e_mov_rcx_rax(r->n);
  let a2: int = e_pop_rax(a1);
  let a3: int = be_combine_emit(op, a2);
  return be_e_rest(src, f, fs, fe, lv, r->p, end, a3);
}
fn be_s_unary(src: str, f: int, fs: int, fe: int, pos: int, end: int): BZ {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return BZ(p: pos, n: 0, c: 29, o: pos); } else { }
  if t->k == 4 { if t->l == 1 { if be_unary_op(src, t) == 1 { return be_s_unary_go(src, f, fs, fe, t, end); } else { } } else { } } else { }
  return be_s_postfix(src, f, fs, fe, pos, end);
}
fn be_unary_op(src: str, t: Tok): int {
  if tok_byte(src, t->s) == 45 { return 1; } else { }
  if tok_byte(src, t->s) == 33 { return 1; } else { }
  if tok_byte(src, t->s) == 126 { return 1; } else { }
  return 0;
}
fn be_s_unary_go(src: str, f: int, fs: int, fe: int, t: Tok, end: int): BZ {
  let r: BZ = be_s_unary(src, f, fs, fe, t->p, end);
  if r->c == 0 { } else { return r; }
  return BZ(p: r->p, n: r->n + 3, c: 0, o: 0);
}
fn be_e_unary(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int): BZ {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return BZ(p: pos, n: acc, c: 29, o: pos); } else { }
  if t->k == 4 { if t->l == 1 { if be_unary_op(src, t) == 1 { return be_e_unary_go(src, f, fs, fe, t, end, acc); } else { } } else { } } else { }
  return be_e_postfix(src, f, fs, fe, pos, end, acc);
}
fn be_e_unary_go(src: str, f: int, fs: int, fe: int, t: Tok, end: int, acc: int): BZ {
  let r: BZ = be_e_unary(src, f, fs, fe, t->p, end, acc);
  if r->c == 0 { } else { return r; }
  return BZ(p: r->p, n: be_unary_emit(src, t, r->n), c: 0, o: 0);
}
fn be_unary_emit(src: str, t: Tok, acc: int): int {
  if tok_byte(src, t->s) == 45 { return e_neg_rax(acc); } else { }
  if tok_byte(src, t->s) == 33 { return e_lnot_eax(acc); } else { }
  return e_not_rax(acc);
}
fn be_s_postfix(src: str, f: int, fs: int, fe: int, pos: int, end: int): BZ {
  let b: BZ = be_s_primary(src, f, fs, fe, pos, end);
  if b->c == 0 { } else { return b; }
  return be_s_post_rest(src, f, fs, fe, b->p, end, b->n);
}
fn be_s_post_rest(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int): BZ {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return BZ(p: pos, n: acc, c: 0, o: 0); } else { }
  if t->k == 4 { return be_s_post_op(src, f, fs, fe, pos, end, acc, t); } else { }
  return BZ(p: pos, n: acc, c: 0, o: 0);
}
fn be_s_post_op(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int, t: Tok): BZ {
  if t->l == 2 { if beq(src, t->s, "->", 0, 2) { return BZ(p: pos, n: 0, c: 25, o: t->s); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 91 { return BZ(p: pos, n: 0, c: 25, o: t->s); } else { } } else { }
  return BZ(p: pos, n: acc, c: 0, o: 0);
}
fn be_e_postfix(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int): BZ {
  let b: BZ = be_e_primary(src, f, fs, fe, pos, end, acc);
  if b->c == 0 { } else { return b; }
  return be_e_post_rest(src, f, fs, fe, b->p, end, b->n);
}
fn be_e_post_rest(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int): BZ {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return BZ(p: pos, n: acc, c: 0, o: 0); } else { }
  if t->k == 4 { return be_e_post_op(src, f, fs, fe, pos, end, acc, t); } else { }
  return BZ(p: pos, n: acc, c: 0, o: 0);
}
fn be_e_post_op(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int, t: Tok): BZ {
  if t->l == 2 { if beq(src, t->s, "->", 0, 2) { return BZ(p: pos, n: acc, c: 25, o: t->s); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 91 { return BZ(p: pos, n: acc, c: 25, o: t->s); } else { } } else { }
  return BZ(p: pos, n: acc, c: 0, o: 0);
}
fn be_s_primary(src: str, f: int, fs: int, fe: int, pos: int, end: int): BZ {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return BZ(p: pos, n: 0, c: 29, o: pos); } else { }
  if t->k == 0 - 2 { return BZ(p: pos, n: 0, c: 29, o: pos); } else { }
  if t->k == 2 { return BZ(p: t->p, n: sz_mov_rax_imm(span_int(src, t->s, t->l)), c: 0, o: 0); } else { }
  if t->k == 3 { return BZ(p: pos, n: 0, c: 25, o: t->s); } else { }
  if t->k == 1 { return be_s_ident(src, f, fs, fe, t, end); } else { }
  if t->k == 4 { return be_s_punct(src, f, fs, fe, t, end); } else { }
  return BZ(p: pos, n: 0, c: 29, o: pos);
}
fn be_s_ident(src: str, f: int, fs: int, fe: int, t: Tok, end: int): BZ {
  if t->l == 4 { if beq(src, t->s, "true", 0, 4) { return BZ(p: t->p, n: 5, c: 0, o: 0); } else { } } else { }
  if t->l == 5 { if beq(src, t->s, "false", 0, 5) { return BZ(p: t->p, n: 5, c: 0, o: 0); } else { } } else { }
  let nx: Tok = pgm_tok(src, t->p, end);
  if nx->k == 4 { if nx->l == 1 { if tok_byte(src, nx->s) == 40 { return be_s_call(src, f, fs, fe, t, end); } else { } } else { } } else { }
  if nx->k == 4 { if nx->l == 2 { if beq(src, nx->s, "::", 0, 2) { return BZ(p: t->s, n: 0, c: 25, o: t->s); } else { } } else { } } else { }
  let v: VS = res_var(src, f, fs, fe, t->s, t->s, t->l);
  if has_err(v->d) { return BZ(p: t->s, n: 0, c: 29, o: t->s); } else { }
  return BZ(p: t->p, n: sz_mov_rax_home(be_home_slot(v)), c: 0, o: 0);
}
fn be_s_punct(src: str, f: int, fs: int, fe: int, t: Tok, end: int): BZ {
  if t->l == 1 { if tok_byte(src, t->s) == 40 { return be_s_paren(src, f, fs, fe, t, end); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 91 { return BZ(p: t->s, n: 0, c: 25, o: t->s); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 123 { return BZ(p: t->s, n: 0, c: 25, o: t->s); } else { } } else { }
  return BZ(p: t->s, n: 0, c: 29, o: t->s);
}
fn be_s_paren(src: str, f: int, fs: int, fe: int, t: Tok, end: int): BZ {
  let e: BZ = be_s_level(src, f, fs, fe, 0, t->p, end);
  if e->c == 0 { } else { return e; }
  let cb: Tok = pgm_tok(src, e->p, end);
  if cb->k == 4 { if cb->l == 1 { if tok_byte(src, cb->s) == 41 { return BZ(p: cb->p, n: e->n, c: 0, o: 0); } else { } } else { } } else { }
  return BZ(p: t->s, n: 0, c: 29, o: cb->s);
}
fn be_e_primary(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int): BZ {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return BZ(p: pos, n: acc, c: 29, o: pos); } else { }
  if t->k == 0 - 2 { return BZ(p: pos, n: acc, c: 29, o: pos); } else { }
  if t->k == 2 { return BZ(p: t->p, n: e_mov_rax_imm(span_int(src, t->s, t->l), acc), c: 0, o: 0); } else { }
  if t->k == 3 { return BZ(p: pos, n: acc, c: 25, o: t->s); } else { }
  if t->k == 1 { return be_e_ident(src, f, fs, fe, t, end, acc); } else { }
  if t->k == 4 { return be_e_punct(src, f, fs, fe, t, end, acc); } else { }
  return BZ(p: pos, n: acc, c: 29, o: pos);
}
fn be_e_ident(src: str, f: int, fs: int, fe: int, t: Tok, end: int, acc: int): BZ {
  if t->l == 4 { if beq(src, t->s, "true", 0, 4) { return BZ(p: t->p, n: e_mov_rax_imm(1, acc), c: 0, o: 0); } else { } } else { }
  if t->l == 5 { if beq(src, t->s, "false", 0, 5) { return BZ(p: t->p, n: e_mov_rax_imm(0, acc), c: 0, o: 0); } else { } } else { }
  let nx: Tok = pgm_tok(src, t->p, end);
  if nx->k == 4 { if nx->l == 1 { if tok_byte(src, nx->s) == 40 { return be_e_call(src, f, fs, fe, t, end, acc); } else { } } else { } } else { }
  if nx->k == 4 { if nx->l == 2 { if beq(src, nx->s, "::", 0, 2) { return BZ(p: t->s, n: acc, c: 25, o: t->s); } else { } } else { } } else { }
  let v: VS = res_var(src, f, fs, fe, t->s, t->s, t->l);
  if has_err(v->d) { return BZ(p: t->s, n: acc, c: 29, o: t->s); } else { }
  return BZ(p: t->p, n: e_mov_rax_home(be_home_slot(v), acc), c: 0, o: 0);
}
fn be_e_punct(src: str, f: int, fs: int, fe: int, t: Tok, end: int, acc: int): BZ {
  if t->l == 1 { if tok_byte(src, t->s) == 40 { return be_e_paren(src, f, fs, fe, t, end, acc); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 91 { return BZ(p: t->s, n: acc, c: 25, o: t->s); } else { } } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 123 { return BZ(p: t->s, n: acc, c: 25, o: t->s); } else { } } else { }
  return BZ(p: t->s, n: acc, c: 29, o: t->s);
}
fn be_e_paren(src: str, f: int, fs: int, fe: int, t: Tok, end: int, acc: int): BZ {
  let e: BZ = be_e_level(src, f, fs, fe, 0, t->p, end, acc);
  if e->c == 0 { } else { return e; }
  let cb: Tok = pgm_tok(src, e->p, end);
  if cb->k == 4 { if cb->l == 1 { if tok_byte(src, cb->s) == 41 { return BZ(p: cb->p, n: e->n, c: 0, o: 0); } else { } } else { } } else { }
  return BZ(p: t->s, n: acc, c: 29, o: cb->s);
}
fn be_s_block(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int): BZ {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return BZ(p: pos, n: acc, c: 0, o: 0); } else { }
  if pgm_is_cbrace(src, t) { return BZ(p: pos, n: acc, c: 0, o: 0); } else { }
  let s: BZ = be_s_stmt(src, f, fs, fe, t->s, end, acc);
  if s->c == 0 { } else { return s; }
  return be_s_block(src, f, fs, fe, s->p, end, s->n);
}
fn be_s_stmt(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int): BZ {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return BZ(p: pos, n: acc, c: 29, o: pos); } else { }
  if t->k == 1 { return be_s_stmt_kw(src, f, fs, fe, pos, end, acc, t); } else { }
  if pgm_is_obrace(src, t) { return be_s_block_in(src, f, fs, fe, t, end, acc); } else { }
  return be_s_exprstmt(src, f, fs, fe, pos, end, acc);
}
fn be_s_stmt_kw(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int, t: Tok): BZ {
  if t->l == 3 { if beq(src, t->s, "let", 0, 3) { return be_s_let(src, f, fs, fe, pos, end, acc, t); } else { } } else { }
  if t->l == 6 { if beq(src, t->s, "return", 0, 6) { return be_s_return(src, f, fs, fe, pos, end, acc, t); } else { } } else { }
  if t->l == 2 { if beq(src, t->s, "if", 0, 2) { return be_s_if(src, f, fs, fe, pos, end, acc, t); } else { } } else { }
  if t->l == 5 { if beq(src, t->s, "while", 0, 5) { return be_s_while(src, f, fs, fe, pos, end, acc, t); } else { } } else { }
  if t->l == 5 { if beq(src, t->s, "break", 0, 5) { return be_s_jump(src, f, pos, end, acc, t); } else { } } else { }
  if t->l == 8 { if beq(src, t->s, "continue", 0, 8) { return be_s_jump(src, f, pos, end, acc, t); } else { } } else { }
  if be_s_ctrl(src, t) == 1 { return BZ(p: pos, n: acc, c: 25, o: t->s); } else { }
  if t->l == 3 { if beq(src, t->s, "use", 0, 3) { return be_s_usevar(src, f, fs, fe, pos, end, acc, t); } else { } } else { }
  return be_s_exprstmt(src, f, fs, fe, pos, end, acc);
}
fn be_s_usevar(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int, t: Tok): BZ {
  let nx: Tok = pgm_tok(src, t->p, end);
  if nx->k == 3 { return BZ(p: pos, n: acc, c: 25, o: t->s); } else { }
  return be_s_exprstmt(src, f, fs, fe, pos, end, acc);
}
fn be_s_ctrl(src: str, t: Tok): int {
  if t->l == 5 { if beq(src, t->s, "match", 0, 5) { return 1; } else { } } else { }
  return 0;
}
fn be_s_block_in(src: str, f: int, fs: int, fe: int, t: Tok, end: int, acc: int): BZ {
  let be: int = pgm_brace_end(src, t->s, end);
  if be == 0 - 1 { return BZ(p: t->s, n: acc, c: 29, o: t->s); } else { }
  let d: BZ = be_s_block(src, f, fs, fe, t->p, be, acc);
  if d->c == 0 { } else { return d; }
  return BZ(p: be, n: d->n, c: 0, o: 0);
}
fn be_s_exprstmt(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int): BZ {
  let e: BZ = be_s_level(src, f, fs, fe, 0, pos, end);
  if e->c == 0 { } else { return BZ(p: e->p, n: acc, c: e->c, o: e->o); }
  let sc: Tok = pgm_tok(src, e->p, end);
  if pgm_is_semi(src, sc) { return BZ(p: sc->p, n: acc + e->n, c: 0, o: 0); } else { }
  return BZ(p: pos, n: acc, c: 29, o: sc->s);
}
fn be_s_let(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int, t: Tok): BZ {
  let nm: Tok = pgm_tok(src, t->p, end);
  let cn: Tok = pgm_tok(src, nm->p, end);
  let ts: Tok = pgm_tok(src, cn->p, end);
  let ty: TR = intern_ty(src, f, ts->s, end, 0);
  if has_err(ty->d) { return BZ(p: pos, n: acc, c: 29, o: pos); } else { }
  if tbase(ty->t) == 1 { } else { if tbase(ty->t) == 2 { } else { return BZ(p: pos, n: acc, c: 25, o: ts->s); } }
  let eq: Tok = pgm_tok(src, ty->p, end);
  let e: BZ = be_s_level(src, f, fs, fe, 0, eq->p, end);
  if e->c == 0 { } else { return BZ(p: e->p, n: acc, c: e->c, o: e->o); }
  let sc: Tok = pgm_tok(src, e->p, end);
  if pgm_is_semi(src, sc) { } else { return BZ(p: pos, n: acc, c: 29, o: sc->s); }
  let v: VS = res_var(src, f, fs, fe, nm->s, nm->s, nm->l);
  if has_err(v->d) { return BZ(p: pos, n: acc, c: 29, o: nm->s); } else { }
  if v->off == nm->s { } else { return BZ(p: pos, n: acc, c: 29, o: nm->s); }
  return BZ(p: sc->p, n: acc + e->n + sz_mov_home_rax(v->slot), c: 0, o: 0);
}
fn be_s_return(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int, t: Tok): BZ {
  let nx: Tok = pgm_tok(src, t->p, end);
  if pgm_is_semi(src, nx) { return BZ(p: pos, n: acc, c: 25, o: t->s); } else { }
  let e: BZ = be_s_level(src, f, fs, fe, 0, nx->s, end);
  if e->c == 0 { } else { return BZ(p: e->p, n: acc, c: e->c, o: e->o); }
  let sc: Tok = pgm_tok(src, e->p, end);
  if pgm_is_semi(src, sc) { } else { return BZ(p: pos, n: acc, c: 29, o: sc->s); }
  return BZ(p: sc->p, n: acc + e->n + sz_leave_ret(), c: 0, o: 0);
}
fn be_e_block(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int, bx: int, bc: int): BZ {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return BZ(p: pos, n: acc, c: 0, o: 0); } else { }
  if pgm_is_cbrace(src, t) { return BZ(p: pos, n: acc, c: 0, o: 0); } else { }
  let s: BZ = be_e_stmt(src, f, fs, fe, t->s, end, acc, bx, bc);
  if s->c == 0 { } else { return s; }
  return be_e_block(src, f, fs, fe, s->p, end, s->n, bx, bc);
}
fn be_e_stmt(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int, bx: int, bc: int): BZ {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 0 { return BZ(p: pos, n: acc, c: 29, o: pos); } else { }
  if t->k == 1 { return be_e_stmt_kw(src, f, fs, fe, pos, end, acc, bx, bc, t); } else { }
  if pgm_is_obrace(src, t) { return be_e_block_in(src, f, fs, fe, t, end, acc, bx, bc); } else { }
  return be_e_exprstmt(src, f, fs, fe, pos, end, acc);
}
fn be_e_stmt_kw(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int, bx: int, bc: int, t: Tok): BZ {
  if t->l == 3 { if beq(src, t->s, "let", 0, 3) { return be_e_let(src, f, fs, fe, pos, end, acc, t); } else { } } else { }
  if t->l == 6 { if beq(src, t->s, "return", 0, 6) { return be_e_return(src, f, fs, fe, pos, end, acc, t); } else { } } else { }
  if t->l == 2 { if beq(src, t->s, "if", 0, 2) { return be_e_if(src, f, fs, fe, pos, end, acc, bx, bc, t); } else { } } else { }
  if t->l == 5 { if beq(src, t->s, "while", 0, 5) { return be_e_while(src, f, fs, fe, pos, end, acc, bx, bc, t); } else { } } else { }
  if t->l == 5 { if beq(src, t->s, "break", 0, 5) { return be_e_break(src, f, pos, end, acc, bx, bc, t); } else { } } else { }
  if t->l == 8 { if beq(src, t->s, "continue", 0, 8) { return be_e_continue(src, f, pos, end, acc, bx, bc, t); } else { } } else { }
  if be_s_ctrl(src, t) == 1 { return BZ(p: pos, n: acc, c: 25, o: t->s); } else { }
  if t->l == 3 { if beq(src, t->s, "use", 0, 3) { return be_e_usevar(src, f, fs, fe, pos, end, acc, t); } else { } } else { }
  return be_e_exprstmt(src, f, fs, fe, pos, end, acc);
}
fn be_e_usevar(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int, t: Tok): BZ {
  let nx: Tok = pgm_tok(src, t->p, end);
  if nx->k == 3 { return BZ(p: pos, n: acc, c: 25, o: t->s); } else { }
  return be_e_exprstmt(src, f, fs, fe, pos, end, acc);
}
fn be_e_block_in(src: str, f: int, fs: int, fe: int, t: Tok, end: int, acc: int, bx: int, bc: int): BZ {
  let be: int = pgm_brace_end(src, t->s, end);
  if be == 0 - 1 { return BZ(p: t->s, n: acc, c: 29, o: t->s); } else { }
  let d: BZ = be_e_block(src, f, fs, fe, t->p, be, acc, bx, bc);
  if d->c == 0 { } else { return d; }
  return BZ(p: be, n: d->n, c: 0, o: 0);
}
fn be_e_exprstmt(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int): BZ {
  let e: BZ = be_e_level(src, f, fs, fe, 0, pos, end, acc);
  if e->c == 0 { } else { return e; }
  let sc: Tok = pgm_tok(src, e->p, end);
  if pgm_is_semi(src, sc) { return BZ(p: sc->p, n: e->n, c: 0, o: 0); } else { }
  return BZ(p: pos, n: acc, c: 29, o: sc->s);
}
fn be_e_let(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int, t: Tok): BZ {
  let nm: Tok = pgm_tok(src, t->p, end);
  let cn: Tok = pgm_tok(src, nm->p, end);
  let ts: Tok = pgm_tok(src, cn->p, end);
  let ty: TR = intern_ty(src, f, ts->s, end, 0);
  if has_err(ty->d) { return BZ(p: pos, n: acc, c: 29, o: pos); } else { }
  if tbase(ty->t) == 1 { } else { if tbase(ty->t) == 2 { } else { return BZ(p: pos, n: acc, c: 25, o: ts->s); } }
  let eq: Tok = pgm_tok(src, ty->p, end);
  let e: BZ = be_e_level(src, f, fs, fe, 0, eq->p, end, acc);
  if e->c == 0 { } else { return e; }
  let sc: Tok = pgm_tok(src, e->p, end);
  if pgm_is_semi(src, sc) { } else { return BZ(p: pos, n: acc, c: 29, o: sc->s); }
  let v: VS = res_var(src, f, fs, fe, nm->s, nm->s, nm->l);
  if has_err(v->d) { return BZ(p: pos, n: acc, c: 29, o: nm->s); } else { }
  if v->off == nm->s { } else { return BZ(p: pos, n: acc, c: 29, o: nm->s); }
  return BZ(p: sc->p, n: e_mov_home_rax(v->slot, e->n), c: 0, o: 0);
}
fn be_e_return(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int, t: Tok): BZ {
  let nx: Tok = pgm_tok(src, t->p, end);
  if pgm_is_semi(src, nx) { return BZ(p: pos, n: acc, c: 25, o: t->s); } else { }
  let e: BZ = be_e_level(src, f, fs, fe, 0, nx->s, end, acc);
  if e->c == 0 { } else { return e; }
  let sc: Tok = pgm_tok(src, e->p, end);
  if pgm_is_semi(src, sc) { } else { return BZ(p: pos, n: acc, c: 29, o: sc->s); }
  return BZ(p: sc->p, n: e_leave_ret(e->n), c: 0, o: 0);
}
fn be_main(src: str, f: int): D {
  let chk: D = pgm_check(src, f);
  if has_err(chk) { return chk; } else { }
  let g: D = be_prog_gate(src, f);
  if has_err(g) { return g; } else { }
  let s: BZ = be_size_prog(src, f);
  if s->c == 0 { } else { return derr(s->c, f, s->o); }
  if s->n <= 65536 { } else { return derr(26, f, 0); }
  let e: BZ = be_emit_prog(src, f);
  if e->c == 0 { } else { return derr(e->c, f, e->o); }
  if e->n == s->n { return dok(); } else { }
  return derr(28, f, 0);
}
fn be_prog_gate(src: str, f: int): D {
  return be_gate_items(src, f, 0, len(src), 0, 0);
}
fn be_gate_items(src: str, f: int, pos: int, end: int, nfns: int, nmain: int): D {
  let it: TI = tl_next(src, f, pos, end);
  if it->k == 0 { return be_gate_count(src, f, nfns, nmain); } else { }
  if it->k == 1 { return be_gate_fn(src, f, it, end, nfns, nmain); } else { }
  if it->k == 2 { return derr(25, f, it->s); } else { }
  if it->k == 3 { return derr(25, f, it->s); } else { }
  return derr(29, f, it->s);
}
fn be_gate_count(src: str, f: int, nfns: int, nmain: int): D {
  if nfns == 0 { return derr(25, f, 0); } else { }
  if nmain == 1 { } else { return derr(25, f, 0); }
  if nfns <= 16 { return dok(); } else { }
  return derr(26, f, 0);
}
fn be_gate_fn(src: str, f: int, it: TI, end: int, nfns: int, nmain: int): D {
  let nm: Tok = next_tok(src, next_tok(src, it->s)->p);
  if nm->k == 1 { if nm->l == 4 { if beq(src, nm->s, "main", 0, 4) { return be_gate_main(src, f, it, end, nfns, nmain); } else { } } else { } } else { }
  return be_gate_helper(src, f, it, end, nfns, nmain);
}
fn be_gate_main(src: str, f: int, it: TI, end: int, nfns: int, nmain: int): D {
  let cs: int = it->s;
  let ce: int = it->s + it->l;
  let kw: Tok = next_tok(src, cs);
  let nm: Tok = next_tok(src, kw->p);
  let lp: Tok = next_tok(src, nm->p);
  let bo: int = pgm_body_open(src, lp->p, ce);
  if bo == 0 - 1 { return derr(29, f, cs); } else { }
  if pgm_hparam_count(src, cs, ce) == 0 { } else { return derr(25, f, cs); }
  let rt: list<int,24> = pgm_body_ret(src, f, lp->p, bo);
  if tbase(rt) == 1 { } else { return derr(25, f, cs); }
  let nl: int = scope_slot(src, f, cs, ce, ce);
  if nl <= 128 { } else { return derr(26, f, cs); }
  return be_gate_items(src, f, it->p, end, nfns + 1, nmain + 1);
}
fn be_size_prog(src: str, f: int): BZ {
  let n: int = be_fn_count(src, f);
  let m: BZ = be_size_all(src, f, 0, n, 28 + sz_start());
  if m->c == 0 { } else { return m; }
  return BZ(p: m->p, n: m->n, c: 0, o: 0);
}
fn be_emit_prog(src: str, f: int): BZ {
  let n: int = be_fn_count(src, f);
  let mi: int = be_main_index(src, f);
  let mo: int = be_fn_codeoff(src, f, mi);
  let h: int = be_rnyx_header(14 + be_all_len(src, f), 0);
  let s: int = e_start(mo, h);
  return be_emit_all(src, f, 0, n, s);
}
fn be_gate_helper(src: str, f: int, it: TI, end: int, nfns: int, nmain: int): D {
  let hs: int = it->s;
  let he: int = it->s + it->l;
  let hk: Tok = next_tok(src, hs);
  let hn2: Tok = next_tok(src, hk->p);
  let hp: Tok = next_tok(src, hn2->p);
  let hb: int = pgm_body_open(src, hp->p, he);
  if hb == 0 - 1 { return derr(29, f, hs); } else { }
  let np: int = pgm_hparam_count(src, hs, he);
  if np <= 6 { } else { return derr(25, f, hs); }
  let gd: D = be_gate_hparams(src, f, hs, he, np, 0);
  if has_err(gd) { return gd; } else { }
  let hr: list<int,24> = pgm_body_ret(src, f, hp->p, hb);
  if tbase(hr) == 1 { } else { return derr(25, f, hs); }
  let hn: int = scope_slot(src, f, hs, he, he);
  if hn <= 128 { } else { return derr(26, f, hs); }
  return be_gate_items(src, f, it->p, end, nfns + 1, nmain);
}
fn be_gate_hparams(src: str, f: int, hs: int, he: int, np: int, i: int): D {
  if i >= np { return dok(); } else { }
  let pt: TR = pgm_hparam_ty(src, f, hs, he, i);
  if has_err(pt->d) { return pt->d; } else { }
  if tbase(pt->t) == 1 { } else { return derr(25, f, hs); }
  return be_gate_hparams(src, f, hs, he, np, i + 1);
}
fn be_fn_count(src: str, f: int): int {
  return be_fn_count_at(src, f, 0, len(src), 0);
}
fn be_fn_count_at(src: str, f: int, pos: int, end: int, acc: int): int {
  let it: TI = tl_next(src, f, pos, end);
  if it->k == 0 { return acc; } else { }
  if it->k == 1 { return be_fn_count_at(src, f, it->p, end, acc + 1); } else { }
  return be_fn_count_at(src, f, it->p, end, acc);
}
fn be_is_main(src: str, cs: int): int {
  let kw: Tok = next_tok(src, cs);
  let nm: Tok = next_tok(src, kw->p);
  if nm->k == 1 { if nm->l == 4 { if beq(src, nm->s, "main", 0, 4) { return 1; } else { } } else { } } else { }
  return 0;
}
fn be_main_index(src: str, f: int): int {
  return be_main_at(src, f, 0, len(src), 0);
}
fn be_main_at(src: str, f: int, pos: int, end: int, idx: int): int {
  let it: TI = tl_next(src, f, pos, end);
  if it->k == 0 { return 0 - 1; } else { }
  if it->k == 1 { if be_is_main(src, it->s) == 1 { return idx; } else { } return be_main_at(src, f, it->p, end, idx + 1); } else { }
  return be_main_at(src, f, it->p, end, idx);
}
fn be_fn_span(src: str, f: int, idx: int): VS {
  return be_fn_span_at(src, f, idx, 0, len(src), 0);
}
fn be_fn_span_at(src: str, f: int, idx: int, pos: int, end: int, seen: int): VS {
  let it: TI = tl_next(src, f, pos, end);
  if it->k == 0 { return VS(off: 0 - 1, k: 0, ts: 0, tl: 0, slot: 0, d: dok()); } else { }
  if it->k == 1 { if seen == idx { return VS(off: it->s, k: 0, ts: it->s, tl: it->l, slot: 0, d: dok()); } else { } return be_fn_span_at(src, f, idx, it->p, end, seen + 1); } else { }
  return be_fn_span_at(src, f, idx, it->p, end, seen);
}
fn be_find_fn(src: str, f: int, ns: int, nl: int, fs: int): int {
  return be_find_at(src, f, ns, nl, fs, 0, len(src), 0);
}
fn be_find_at(src: str, f: int, ns: int, nl: int, fs: int, pos: int, end: int, idx: int): int {
  let it: TI = tl_next(src, f, pos, end);
  if it->k == 0 { return 0 - 1; } else { }
  if it->k == 1 { return be_find_hit(src, f, ns, nl, fs, end, it, idx); } else { }
  return be_find_at(src, f, ns, nl, fs, it->p, end, idx);
}
fn be_find_hit(src: str, f: int, ns: int, nl: int, fs: int, end: int, it: TI, idx: int): int {
  if it->s >= fs { return 0 - 1; } else { }
  let kw: Tok = next_tok(src, it->s);
  let nm: Tok = next_tok(src, kw->p);
  if nm->l == nl { if beq(src, nm->s, src, ns, nl) { return idx; } else { } } else { }
  return be_find_at(src, f, ns, nl, fs, it->p, end, idx + 1);
}
fn be_size_fn(src: str, f: int, cs: int, ce: int): BZ {
  let kw: Tok = next_tok(src, cs);
  let nm: Tok = next_tok(src, kw->p);
  let lp: Tok = next_tok(src, nm->p);
  let bo: int = pgm_body_open(src, lp->p, ce);
  let be: int = pgm_brace_end(src, bo, ce);
  let nl: int = scope_slot(src, f, cs, ce, ce);
  let np: int = pgm_hparam_count(src, cs, ce);
  let b: BZ = be_s_block(src, f, cs, ce, bo + 1, be, sz_frame(nl) + 4 * np);
  if b->c == 0 { } else { return b; }
  return BZ(p: be, n: b->n + 1, c: 0, o: 0);
}
fn be_size_all(src: str, f: int, idx: int, n: int, acc: int): BZ {
  if idx >= n { return BZ(p: 0, n: acc, c: 0, o: 0); } else { }
  let sp: VS = be_fn_span(src, f, idx);
  let m: BZ = be_size_fn(src, f, sp->off, sp->off + sp->tl);
  if m->c == 0 { } else { return m; }
  return be_size_all(src, f, idx + 1, n, acc + m->n);
}
fn be_fn_codeoff(src: str, f: int, idx: int): int {
  return be_codeoff_at(src, f, idx, 0, 14);
}
fn be_codeoff_at(src: str, f: int, idx: int, j: int, acc: int): int {
  if j >= idx { return acc; } else { }
  let sp: VS = be_fn_span(src, f, j);
  let m: BZ = be_size_fn(src, f, sp->off, sp->off + sp->tl);
  if m->c == 0 { return be_codeoff_at(src, f, idx, j + 1, acc + m->n); } else { }
  return 0;
}
fn be_all_len(src: str, f: int): int {
  let s: BZ = be_size_prog(src, f);
  if s->c == 0 { return s->n - 28 - sz_start(); } else { }
  return 0;
}
fn be_emit_fn(src: str, f: int, cs: int, ce: int, acc: int): BZ {
  let kw: Tok = next_tok(src, cs);
  let nm: Tok = next_tok(src, kw->p);
  let lp: Tok = next_tok(src, nm->p);
  let bo: int = pgm_body_open(src, lp->p, ce);
  let be: int = pgm_brace_end(src, bo, ce);
  let nl: int = scope_slot(src, f, cs, ce, ce);
  let np: int = pgm_hparam_count(src, cs, ce);
  let fr: int = e_frame(nl, acc);
  let sp: int = be_e_spills(fr, np, 0);
  let b: BZ = be_e_block(src, f, cs, ce, bo + 1, be, sp, 0 - 1, 0 - 1);
  if b->c == 0 { } else { return b; }
  let t: int = e_b(204, b->n);
  return BZ(p: be, n: t, c: 0, o: 0);
}
fn be_emit_all(src: str, f: int, idx: int, n: int, acc: int): BZ {
  if idx >= n { return BZ(p: 0, n: acc, c: 0, o: 0); } else { }
  let sp: VS = be_fn_span(src, f, idx);
  let b: BZ = be_emit_fn(src, f, sp->off, sp->off + sp->tl, acc);
  if b->c == 0 { } else { return b; }
  return be_emit_all(src, f, idx + 1, n, b->n);
}
fn e_spill_4(rx: int, mod: int, disp: int, acc: int): int {
  let a0: int = e_b(rx, acc);
  let a1: int = e_b(137, a0);
  let a2: int = e_b(mod, a1);
  let a3: int = e_b(disp, a2);
  return a3;
}
fn e_spill_reg(i: int, acc: int): int {
  let d: int = 256 - (i + 1) * 8;
  if i == 0 { return e_spill_4(72, 125, d, acc); } else { }
  if i == 1 { return e_spill_4(72, 117, d, acc); } else { }
  if i == 2 { return e_spill_4(72, 85, d, acc); } else { }
  if i == 3 { return e_spill_4(72, 77, d, acc); } else { }
  if i == 4 { return e_spill_4(76, 69, d, acc); } else { }
  return e_spill_4(76, 77, d, acc);
}
fn be_e_spills(acc: int, np: int, i: int): int {
  if i >= np { return acc; } else { }
  return be_e_spills(e_spill_reg(i, acc), np, i + 1);
}
fn e_pop_r8(acc: int): int {
  let a0: int = e_b(65, acc);
  let a1: int = e_b(88, a0);
  return a1;
}
fn e_pop_r9(acc: int): int {
  let a0: int = e_b(65, acc);
  let a1: int = e_b(89, a0);
  return a1;
}
fn e_pop_reg(i: int, acc: int): int {
  if i == 0 { return e_b(95, acc); } else { }
  if i == 1 { return e_b(94, acc); } else { }
  if i == 2 { return e_b(90, acc); } else { }
  if i == 3 { return e_b(89, acc); } else { }
  if i == 4 { return e_pop_r8(acc); } else { }
  return e_pop_r9(acc);
}
fn be_arg_pop_size(i: int): int {
  if i <= 3 { return 1; } else { }
  return 2;
}
fn be_pop_size(np: int, i: int): int {
  if i >= np { return 0; } else { }
  return be_arg_pop_size(i) + be_pop_size(np, i + 1);
}
fn be_e_popargs(acc: int, i: int): int {
  if i <= 0 - 1 { return acc; } else { }
  return be_e_popargs(e_pop_reg(i, acc), i - 1);
}
fn sz_call_op(): int {
  return 5;
}
fn e_call_rel(disp: int, acc: int): int {
  let a0: int = e_b(232, acc);
  let a1: int = e_le32(disp, a0);
  return a1;
}
fn be_s_call(src: str, f: int, fs: int, fe: int, t: Tok, end: int): BZ {
  let nx: Tok = pgm_tok(src, t->p, end);
  let ci: int = be_find_fn(src, f, t->s, t->l, fs);
  if ci == 0 - 1 { return BZ(p: t->s, n: 0, c: 25, o: t->s); } else { }
  let cs: VS = be_fn_span(src, f, ci);
  let np: int = pgm_hparam_count(src, cs->off, cs->off + cs->tl);
  if np <= 6 { } else { return BZ(p: t->s, n: 0, c: 25, o: t->s); }
  let a: BZ = be_s_callargs(src, f, fs, fe, nx->p, end, np, 0, 0);
  if a->c == 0 { } else { return a; }
  return BZ(p: a->p, n: a->n + np + be_pop_size(np, 0) + sz_call_op(), c: 0, o: 0);
}
fn be_s_callargs(src: str, f: int, fs: int, fe: int, pos: int, end: int, np: int, i: int, acc: int): BZ {
  if i >= np { return be_s_callclose(src, pos, end, acc); } else { }
  let r: BZ = be_s_level(src, f, fs, fe, 0, pos, end);
  if r->c == 0 { } else { return r; }
  return be_s_callsep(src, f, fs, fe, r->p, end, np, i, acc + r->n);
}
fn be_s_callsep(src: str, f: int, fs: int, fe: int, pos: int, end: int, np: int, i: int, acc: int): BZ {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 4 { return be_s_callsepp(src, f, fs, fe, t, end, np, i, acc); } else { }
  return BZ(p: pos, n: 0, c: 29, o: pos);
}
fn be_s_callsepp(src: str, f: int, fs: int, fe: int, t: Tok, end: int, np: int, i: int, acc: int): BZ {
  if i + 1 == np { if t->l == 1 { if tok_byte(src, t->s) == 41 { return BZ(p: t->p, n: acc, c: 0, o: 0); } else { } } else { } return BZ(p: t->s, n: 0, c: 29, o: t->s); } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 44 { return be_s_callargs(src, f, fs, fe, t->p, end, np, i + 1, acc); } else { } } else { }
  return BZ(p: t->s, n: 0, c: 29, o: t->s);
}
fn be_s_callclose(src: str, pos: int, end: int, acc: int): BZ {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 41 { return BZ(p: t->p, n: acc, c: 0, o: 0); } else { } } else { } } else { }
  return BZ(p: pos, n: 0, c: 29, o: pos);
}
fn be_e_call(src: str, f: int, fs: int, fe: int, t: Tok, end: int, acc: int): BZ {
  let nx: Tok = pgm_tok(src, t->p, end);
  let ci: int = be_find_fn(src, f, t->s, t->l, fs);
  if ci == 0 - 1 { return BZ(p: t->s, n: acc, c: 25, o: t->s); } else { }
  let cs: VS = be_fn_span(src, f, ci);
  let np: int = pgm_hparam_count(src, cs->off, cs->off + cs->tl);
  if np <= 6 { } else { return BZ(p: t->s, n: acc, c: 25, o: t->s); }
  let a: BZ = be_e_callargs(src, f, fs, fe, nx->p, end, np, 0, acc);
  if a->c == 0 { } else { return a; }
  let q0: int = be_e_popargs(a->n, np - 1);
  let co: int = be_fn_codeoff(src, f, ci);
  let dp: int = 28 + co - (q0 + sz_call_op());
  let q1: int = e_call_rel(dp, q0);
  return BZ(p: a->p, n: q1, c: 0, o: 0);
}
fn be_e_callargs(src: str, f: int, fs: int, fe: int, pos: int, end: int, np: int, i: int, acc: int): BZ {
  if i >= np { return be_e_callclose(src, pos, end, acc); } else { }
  let r: BZ = be_e_level(src, f, fs, fe, 0, pos, end, acc);
  if r->c == 0 { } else { return r; }
  let a0: int = e_push_rax(r->n);
  return be_e_callsep(src, f, fs, fe, r->p, end, np, i, a0);
}
fn be_e_callsep(src: str, f: int, fs: int, fe: int, pos: int, end: int, np: int, i: int, acc: int): BZ {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 4 { return be_e_callsepp(src, f, fs, fe, t, end, np, i, acc); } else { }
  return BZ(p: pos, n: acc, c: 29, o: pos);
}
fn be_e_callsepp(src: str, f: int, fs: int, fe: int, t: Tok, end: int, np: int, i: int, acc: int): BZ {
  if i + 1 == np { if t->l == 1 { if tok_byte(src, t->s) == 41 { return BZ(p: t->p, n: acc, c: 0, o: 0); } else { } } else { } return BZ(p: t->s, n: acc, c: 29, o: t->s); } else { }
  if t->l == 1 { if tok_byte(src, t->s) == 44 { return be_e_callargs(src, f, fs, fe, t->p, end, np, i + 1, acc); } else { } } else { }
  return BZ(p: t->s, n: acc, c: 29, o: t->s);
}
fn be_e_callclose(src: str, pos: int, end: int, acc: int): BZ {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 4 { if t->l == 1 { if tok_byte(src, t->s) == 41 { return BZ(p: t->p, n: acc, c: 0, o: 0); } else { } } else { } } else { }
  return BZ(p: pos, n: acc, c: 29, o: pos);
}
fn sz_test(): int {
  return 2;
}
fn e_test_eax(acc: int): int {
  let a0: int = e_b(133, acc);
  let a1: int = e_b(192, a0);
  return a1;
}
fn sz_jcc(): int {
  return 6;
}
fn e_jcc_z(disp: int, acc: int): int {
  let a0: int = e_b(15, acc);
  let a1: int = e_b(132, a0);
  let a2: int = e_le32(disp, a1);
  return a2;
}
fn sz_jmp(): int {
  return 5;
}
fn e_jmp_rel(disp: int, acc: int): int {
  let a0: int = e_b(233, acc);
  let a1: int = e_le32(disp, a0);
  return a1;
}
fn be_rel32(targ: int, pos: int, len: int): int {
  return targ - (pos + len);
}
fn be_s_jump(src: str, f: int, pos: int, end: int, acc: int, t: Tok): BZ {
  let nx: Tok = pgm_tok(src, t->p, end);
  if pgm_is_semi(src, nx) { } else { return BZ(p: pos, n: acc, c: 29, o: nx->s); }
  return BZ(p: nx->p, n: acc + sz_jmp(), c: 0, o: 0);
}
fn be_e_break(src: str, f: int, pos: int, end: int, acc: int, bx: int, bc: int, t: Tok): BZ {
  let nx: Tok = pgm_tok(src, t->p, end);
  if pgm_is_semi(src, nx) { } else { return BZ(p: pos, n: acc, c: 29, o: nx->s); }
  if bx == 0 - 1 { return BZ(p: pos, n: acc, c: 29, o: t->s); } else { }
  return BZ(p: nx->p, n: e_jmp_rel(be_rel32(bx, acc, sz_jmp()), acc), c: 0, o: 0);
}
fn be_e_continue(src: str, f: int, pos: int, end: int, acc: int, bx: int, bc: int, t: Tok): BZ {
  let nx: Tok = pgm_tok(src, t->p, end);
  if pgm_is_semi(src, nx) { } else { return BZ(p: pos, n: acc, c: 29, o: nx->s); }
  if bc == 0 - 1 { return BZ(p: pos, n: acc, c: 29, o: t->s); } else { }
  return BZ(p: nx->p, n: e_jmp_rel(be_rel32(bc, acc, sz_jmp()), acc), c: 0, o: 0);
}
fn be_s_if(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int, t: Tok): BZ {
  let c: BZ = be_s_level(src, f, fs, fe, 0, t->p, end);
  if c->c == 0 { } else { return c; }
  let bo: Tok = pgm_tok(src, c->p, end);
  if pgm_is_obrace(src, bo) { } else { return BZ(p: pos, n: acc, c: 29, o: bo->s); }
  let be: int = pgm_brace_end(src, bo->s, end);
  if be == 0 - 1 { return BZ(p: pos, n: acc, c: 29, o: bo->s); } else { }
  let th: BZ = be_s_block(src, f, fs, fe, bo->p, be, 0);
  if th->c == 0 { } else { return th; }
  return be_s_if_else(src, f, fs, fe, be, end, acc, c->n, th->n);
}
fn be_s_if_else(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int, cn: int, tn: int): BZ {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 1 { if t->l == 4 { if beq(src, t->s, "else", 0, 4) { return be_s_else(src, f, fs, fe, pos, end, acc, cn, tn, t); } else { } } else { } } else { }
  return BZ(p: pos, n: acc + cn + sz_test() + sz_jcc() + tn, c: 0, o: 0);
}
fn be_s_else(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int, cn: int, tn: int, t: Tok): BZ {
  let nx: Tok = pgm_tok(src, t->p, end);
  if nx->k == 1 { if nx->l == 2 { if beq(src, nx->s, "if", 0, 2) { return be_s_if(src, f, fs, fe, nx->s, end, acc + cn + sz_test() + sz_jcc() + tn + sz_jmp(), nx); } else { } } else { } } else { }
  if pgm_is_obrace(src, nx) { } else { return BZ(p: pos, n: acc, c: 29, o: nx->s); }
  let be: int = pgm_brace_end(src, nx->s, end);
  if be == 0 - 1 { return BZ(p: pos, n: acc, c: 29, o: nx->s); } else { }
  let el: BZ = be_s_block(src, f, fs, fe, nx->p, be, 0);
  if el->c == 0 { } else { return el; }
  return BZ(p: be, n: acc + cn + sz_test() + sz_jcc() + tn + sz_jmp() + el->n, c: 0, o: 0);
}
fn be_s_while(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int, t: Tok): BZ {
  let c: BZ = be_s_level(src, f, fs, fe, 0, t->p, end);
  if c->c == 0 { } else { return c; }
  let bo: Tok = pgm_tok(src, c->p, end);
  if pgm_is_obrace(src, bo) { } else { return BZ(p: pos, n: acc, c: 29, o: bo->s); }
  let be: int = pgm_brace_end(src, bo->s, end);
  if be == 0 - 1 { return BZ(p: pos, n: acc, c: 29, o: bo->s); } else { }
  let bd: BZ = be_s_block(src, f, fs, fe, bo->p, be, 0);
  if bd->c == 0 { } else { return bd; }
  return BZ(p: be, n: acc + c->n + sz_test() + sz_jcc() + bd->n + sz_jmp(), c: 0, o: 0);
}
fn be_e_if(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int, bx: int, bc: int, t: Tok): BZ {
  let c: BZ = be_e_level(src, f, fs, fe, 0, t->p, end, acc);
  if c->c == 0 { } else { return c; }
  let bo: Tok = pgm_tok(src, c->p, end);
  if pgm_is_obrace(src, bo) { } else { return BZ(p: pos, n: acc, c: 29, o: bo->s); }
  let be: int = pgm_brace_end(src, bo->s, end);
  if be == 0 - 1 { return BZ(p: pos, n: acc, c: 29, o: bo->s); } else { }
  let th: BZ = be_s_block(src, f, fs, fe, bo->p, be, 0);
  if th->c == 0 { } else { return th; }
  let hasel: int = be_is_else(src, be, end);
  let ex: BZ = be_e_else_measure(src, f, fs, fe, be, end, hasel);
  if ex->c == 0 { } else { return ex; }
  let tt: int = e_test_eax(c->n);
  let eo: int = tt + sz_jcc() + th->n + hasel * sz_jmp() + ex->n;
  let jo: int = e_jcc_z(be_rel32(tt + sz_jcc() + th->n + hasel * sz_jmp(), tt, sz_jcc()), tt);
  let tb: BZ = be_e_block(src, f, fs, fe, bo->p, be, jo, bx, bc);
  if tb->c == 0 { } else { return tb; }
  return be_e_if_tail(src, f, fs, fe, be, end, tb->n, bx, bc, hasel, eo);
}
fn be_is_else(src: str, pos: int, end: int): int {
  let t: Tok = pgm_tok(src, pos, end);
  if t->k == 1 { if t->l == 4 { if beq(src, t->s, "else", 0, 4) { return 1; } else { } } else { } } else { }
  return 0;
}
fn be_e_else_measure(src: str, f: int, fs: int, fe: int, pos: int, end: int, hasel: int): BZ {
  if hasel == 0 { return BZ(p: pos, n: 0, c: 0, o: 0); } else { }
  let t: Tok = pgm_tok(src, pos, end);
  return be_e_else_size(src, f, fs, fe, t, end);
}
fn be_e_else_size(src: str, f: int, fs: int, fe: int, t: Tok, end: int): BZ {
  let nx: Tok = pgm_tok(src, t->p, end);
  if nx->k == 1 { if nx->l == 2 { if beq(src, nx->s, "if", 0, 2) { return be_s_if(src, f, fs, fe, nx->s, end, 0, nx); } else { } } else { } } else { }
  if pgm_is_obrace(src, nx) { } else { return BZ(p: t->s, n: 0, c: 29, o: nx->s); }
  let be: int = pgm_brace_end(src, nx->s, end);
  if be == 0 - 1 { return BZ(p: t->s, n: 0, c: 29, o: nx->s); } else { }
  let el: BZ = be_s_block(src, f, fs, fe, nx->p, be, 0);
  if el->c == 0 { } else { return el; }
  return BZ(p: be, n: el->n, c: 0, o: 0);
}
fn be_e_if_tail(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int, bx: int, bc: int, hasel: int, eo: int): BZ {
  if hasel == 0 { return BZ(p: pos, n: acc, c: 0, o: 0); } else { }
  let t: Tok = pgm_tok(src, pos, end);
  return be_e_else(src, f, fs, fe, t, end, acc, bx, bc, eo);
}
fn be_e_else(src: str, f: int, fs: int, fe: int, t: Tok, end: int, acc: int, bx: int, bc: int, eo: int): BZ {
  let nx: Tok = pgm_tok(src, t->p, end);
  if nx->k == 1 { if nx->l == 2 { if beq(src, nx->s, "if", 0, 2) { return be_e_if(src, f, fs, fe, nx->s, end, acc, bx, bc, nx); } else { } } else { } } else { }
  if pgm_is_obrace(src, nx) { } else { return BZ(p: t->s, n: acc, c: 29, o: nx->s); }
  let be: int = pgm_brace_end(src, nx->s, end);
  if be == 0 - 1 { return BZ(p: t->s, n: acc, c: 29, o: nx->s); } else { }
  let jp: int = e_jmp_rel(be_rel32(eo, acc, sz_jmp()), acc);
  let eb: BZ = be_e_block(src, f, fs, fe, nx->p, be, jp, bx, bc);
  if eb->c == 0 { } else { return eb; }
  return BZ(p: be, n: eb->n, c: 0, o: 0);
}
fn be_e_while(src: str, f: int, fs: int, fe: int, pos: int, end: int, acc: int, bx: int, bc: int, t: Tok): BZ {
  let c: BZ = be_e_level(src, f, fs, fe, 0, t->p, end, acc);
  if c->c == 0 { } else { return c; }
  let bo: Tok = pgm_tok(src, c->p, end);
  if pgm_is_obrace(src, bo) { } else { return BZ(p: pos, n: acc, c: 29, o: bo->s); }
  let be: int = pgm_brace_end(src, bo->s, end);
  if be == 0 - 1 { return BZ(p: pos, n: acc, c: 29, o: bo->s); } else { }
  let bd: BZ = be_s_block(src, f, fs, fe, bo->p, be, 0);
  if bd->c == 0 { } else { return bd; }
  let tt: int = e_test_eax(c->n);
  let eo: int = tt + sz_jcc() + bd->n + sz_jmp();
  let jo: int = e_jcc_z(be_rel32(eo, tt, sz_jcc()), tt);
  let bb: BZ = be_e_block(src, f, fs, fe, bo->p, be, jo, eo, acc);
  if bb->c == 0 { } else { return bb; }
  let ko: int = e_jmp_rel(be_rel32(acc, bb->n, sz_jmp()), bb->n);
  return BZ(p: be, n: ko, c: 0, o: 0);
}

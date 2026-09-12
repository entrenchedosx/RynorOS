// selfhost lexer: bytes to a 2-token window (no token table).
// Kinds: 0 EOF, 1 IDENT, 2 INT, 3 STR (span covers both quotes),
// 4 OP (span covers 1-2 chars). k = 0-2 is a lex error (s = offset,
// l = 1 char, 2 int-overflow, 3 string, 4 escape; p advances past
// the bad input so every consumer terminates).
record Tok { k: int, s: int, l: int, p: int }
fn scan_ident(src: str, pos: int, start: int): Tok {
  if pos >= len(src) { return Tok(k: 1, s: start, l: pos - start, p: pos); } else { }
  if is_alnum(unwrap_or(byte_at(src, pos), 0)) { return scan_ident(src, pos + 1, start); } else { }
  return Tok(k: 1, s: start, l: pos - start, p: pos);
}
fn scan_num(src: str, pos: int, start: int, acc: int): Tok {
  if pos >= len(src) { return Tok(k: 2, s: start, l: pos - start, p: pos); } else { }
  let b: int = unwrap_or(byte_at(src, pos), 0);
  if is_digit(b) { } else { return Tok(k: 2, s: start, l: pos - start, p: pos); }
  let d: int = b - 48;
  if acc > 922337203685477580 { return Tok(k: 0 - 2, s: pos, l: 2, p: pos + 1); } else { }
  if acc == 922337203685477580 { if d > 7 { return Tok(k: 0 - 2, s: pos, l: 2, p: pos + 1); } else { } } else { }
  return scan_num(src, pos + 1, start, acc * 10 + d);
}
fn scan_str(src: str, pos: int, start: int): Tok {
  if pos >= len(src) { return Tok(k: 0 - 2, s: start, l: 3, p: pos); } else { }
  let b: int = unwrap_or(byte_at(src, pos), 0);
  let e: int = unwrap_or(byte_at(src, pos + 1), 0);
  if b == 34 { return Tok(k: 3, s: start, l: pos + 1 - start, p: pos + 1); } else { }
  if b == 10 { return Tok(k: 0 - 2, s: pos, l: 3, p: pos + 1); } else { }
  if b == 92 {
    if e == 92 { return scan_str(src, pos + 2, start); } else { }
    if e == 34 { return scan_str(src, pos + 2, start); } else { }
    if e == 110 { return scan_str(src, pos + 2, start); } else { }
    if e == 116 { return scan_str(src, pos + 2, start); } else { }
    return Tok(k: 0 - 2, s: pos, l: 4, p: pos + 1);
  } else { }
  return scan_str(src, pos + 1, start);
}
fn skip_line(src: str, pos: int): Tok {
  if pos >= len(src) { return Tok(k: 0, s: pos, l: 0, p: pos); } else { }
  if unwrap_or(byte_at(src, pos), 0) == 10 { return next_tok(src, pos + 1); } else { }
  return skip_line(src, pos + 1);
}
fn dbl_op(src: str, pos: int, pair: int): Tok {
  if pair == 15677 { return Tok(k: 4, s: pos, l: 2, p: pos + 2); } else { }
  if pair == 8509 { return Tok(k: 4, s: pos, l: 2, p: pos + 2); } else { }
  if pair == 15421 { return Tok(k: 4, s: pos, l: 2, p: pos + 2); } else { }
  if pair == 15933 { return Tok(k: 4, s: pos, l: 2, p: pos + 2); } else { }
  if pair == 9766 { return Tok(k: 4, s: pos, l: 2, p: pos + 2); } else { }
  if pair == 31868 { return Tok(k: 4, s: pos, l: 2, p: pos + 2); } else { }
  if pair == 11582 { return Tok(k: 4, s: pos, l: 2, p: pos + 2); } else { }
  if pair == 15420 { return Tok(k: 4, s: pos, l: 2, p: pos + 2); } else { }
  if pair == 15934 { return Tok(k: 4, s: pos, l: 2, p: pos + 2); } else { }
  if pair == 14906 { return Tok(k: 4, s: pos, l: 2, p: pos + 2); } else { }
  return Tok(k: 0 - 2, s: 0, l: 0, p: pos);
}
fn next_tok(src: str, pos: int): Tok {
  if pos >= len(src) { return Tok(k: 0, s: pos, l: 0, p: pos); } else { }
  let b: int = unwrap_or(byte_at(src, pos), 0);
  if b == 32 { return next_tok(src, pos + 1); } else { }
  if b == 9 { return next_tok(src, pos + 1); } else { }
  if b == 10 { return next_tok(src, pos + 1); } else { }
  if b == 13 { return next_tok(src, pos + 1); } else { }
  if b == 47 {
    if unwrap_or(byte_at(src, pos + 1), 0) == 47 { return skip_line(src, pos + 2); } else { }
    return Tok(k: 4, s: pos, l: 1, p: pos + 1);
  } else { }
  if is_alpha(b) { return scan_ident(src, pos, pos); } else { }
  if is_digit(b) { return scan_num(src, pos, pos, 0); } else { }
  if b == 34 { return scan_str(src, pos + 1, pos); } else { }
  let c2: int = unwrap_or(byte_at(src, pos + 1), 0);
  let pair: int = b * 256 + c2;
  let d: Tok = dbl_op(src, pos, pair);
  if d->k == (0 - 2) { } else { return d; }
  if b == 43 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 45 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 42 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 47 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 37 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 33 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 61 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 60 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 62 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 40 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 41 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 123 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 125 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 59 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 44 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 58 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 91 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 93 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 38 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 124 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 94 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  if b == 126 { return Tok(k: 4, s: pos, l: 1, p: pos + 1); } else { }
  return Tok(k: 0 - 2, s: pos, l: 1, p: pos + 1);
}

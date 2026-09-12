// selfhost utilities: core-dialect only (no match/result/maps,
// no print-aggregates, def-before-use). Shared by every later file.
// Conventions: D{c,f,o} diagnostics (c=0 ok, f=file idx, o=offset);
// internal err ints map to frozen codes in main.rl `emap`:
// 0 ok, 1 lex-char, 2 lex-int-overflow, 3 lex-string, 4 lex-escape,
// 5 depth, 6 par-want, 7 par-got, 8 par-eof, 9 undeclared,
// 10 duplicate, 11 type, 12 arity, 13 unknown-fn, 14 limit,
// 15 profile, 16 mod-find, 17 mod-cycle, 18 mod-dup, 19 mod-pin,
// 20 mod-edition, 21 mod-manifest, 22 no-entry, 23 emit, 24 usage.
// Token kinds: 0 EOF, 1 IDENT, 2 INT, 3 STR, 4 OP.
// Backend codes (additive, BE-A): 25 unsupported construct,
// 26 code/data overflow, 27 reserved, 28 size/emit mismatch,
// 29 bad checked-state invariant, 30 displacement overflow.
record D { c: int, f: int, o: int }
fn beq(s: str, a: int, t: str, b: int, n: int): bool {
  if n == 0 { return true; } else { }
  if unwrap_or(byte_at(s, a), 0 - 1) == unwrap_or(byte_at(t, b), 0 - 2) { return beq(s, a + 1, t, b + 1, n - 1); } else { }
  return false;
}
fn is_alpha(b: int): bool {
  if b >= 65 { if b <= 90 { return true; } else { } } else { }
  if b >= 97 { if b <= 122 { return true; } else { } } else { }
  if b == 95 { return true; } else { }
  return false;
}
fn is_digit(b: int): bool {
  if b >= 48 { if b <= 57 { return true; } else { } } else { }
  return false;
}
fn is_alnum(b: int): bool {
  if is_alpha(b) { return true; } else { }
  return is_digit(b);
}
fn find_last(s: str, ch: int, i: int): int {
  if i < 0 { return 0 - 1; } else { }
  if unwrap_or(byte_at(s, i), 0) == ch { return i; } else { }
  return find_last(s, ch, i - 1);
}
fn fnv_basis(): int {
  let h0: int = 203;
  let h1: int = h0 * 256 + 242;
  let h2: int = h1 * 256 + 156;
  let h3: int = h2 * 256 + 228;
  let h4: int = h3 * 256 + 132;
  let h5: int = h4 * 256 + 34;
  let h6: int = h5 * 256 + 35;
  let h7: int = h6 * 256 + 37;
  return h7;
}
fn fnv_str(s: str, i: int, h: int): int {
  if i >= len(s) { return h; } else { }
  let b: status<int> = byte_at(s, i);
  match b {
    ok(v) => { return fnv_str(s, i + 1, (h + v) * 1099511628211); },
    err(e) => { return h; }
  }
}
fn fnv(s: str): int {
  return fnv_str(s, 0, fnv_basis());
}
fn hexch(d: int): str {
  let h: list<str,16> = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "a", "b", "c", "d", "e", "f"];
  return unwrap_or(h[d], "?");
}
fn line_of(s: str, off: int, i: int, line: int): int {
  if i >= off { return line; } else { }
  if unwrap_or(byte_at(s, i), 0) == 10 { return line_of(s, off, i + 1, line + 1); } else { }
  return line_of(s, off, i + 1, line);
}
fn sline(s: str, off: int): int {
  return line_of(s, off, 0, 1);
}
fn tok_byte(src: str, s: int): int {
  return unwrap_or(byte_at(src, s), 0);
}

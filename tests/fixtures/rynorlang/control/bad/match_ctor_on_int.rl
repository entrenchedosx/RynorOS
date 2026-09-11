fn main(): int {
  match 1 {
    ok(x) => { return x; },
    _ => { return 0; }
  }
  return 0;
}

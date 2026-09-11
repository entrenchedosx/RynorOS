fn main(): int {
  let s: status<int> = get({"k": 1}, "k");
  match s {
    ok(v) => { return v; }
  }
  return 0;
}

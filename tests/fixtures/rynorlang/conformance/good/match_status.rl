fn main(): int {
  let s: status<int> = get({"k": 5}, "k");
  match s {
    ok(v) => { print(v); return 1; },
    err(e) => { print(e); return 0; }
  }
}

fn classify(x: int): int {
  match x {
    0 => { return 100; },
    1 => {
      match x {
        1 => { return 101; },
        _ => { return 199; }
      }
    },
    _ => { return 199; }
  }
}
fn main(): int {
  print(classify(0));
  print(classify(1));
  print(classify(9));
  let s: status<int> = get({"k": 3}, "k");
  match s {
    ok(v) => { print(v); },
    err(_) => { print(0); }
  }
  match get({"k": 3}, "z") {
    ok(v) => { print(v); return 1; },
    err(c) => { print(c); }
  }
  return 0;
}

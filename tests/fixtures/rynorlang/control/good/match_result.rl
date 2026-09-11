record Error { code: int, message: str }
fn div(a: int, b: int): result<int,Error> {
  if b == 0 {
    return err(Error(code: 1, message: "div0"));
  } else {
    return ok(a / b);
  }
}
fn main(): int {
  print(unwrap_or(get({"k": 7}, "k"), 0));
  match div(20, 4) {
    ok(v) => { print(v); },
    err(e) => { print(e->code); return 1; }
  }
  match div(20, 0) {
    ok(v) => { print(v); return 1; },
    err(e) => { print(e->code); }
  }
  return 0;
}

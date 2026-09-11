record Error { code: int, message: str }
fn step1(x: int): result<int,Error> {
  if x < 0 {
    return err(Error(code: 10, message: "neg"));
  } else {
    return ok(x + 1);
  }
}
fn step2(x: int): result<int,Error> {
  match step1(x) {
    ok(v) => { return ok(v * 2); },
    err(e) => { return err(e); }
  }
}
fn main(): int {
  match step2(5) {
    ok(v) => { print(v); },
    err(e) => { print(e->code); return 1; }
  }
  match step2(0 - 5) {
    ok(v) => { print(v); return 1; },
    err(e) => { print(e->code); print(e->message); }
  }
  return 0;
}

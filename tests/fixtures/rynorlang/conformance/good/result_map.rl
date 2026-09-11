fn pick(flag: bool): result<map<str,int,4>,int> {
  if flag {
    return ok({"a": 1});
  } else {
    return err(0 - 1);
  }
}
fn main(): int {
  match pick(true) {
    ok(v) => { print(v); return 0; },
    err(e) => { print(e); return 1; }
  }
}

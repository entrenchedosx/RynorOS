fn main(): int {
  let r: result<int,int> = ok(1);
  match r {
    ok(x) => { return x; },
    ok(y) => { return y; },
    err(e) => { return e; }
  }
}

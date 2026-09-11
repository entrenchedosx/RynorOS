use "lib/errors.rl";
fn main(): int {
  match errors::fail(7) {
    ok(v) => { print(v); return 1; },
    err(e) => { print(e->code); print(e->message); }
  }
  return 0;
}

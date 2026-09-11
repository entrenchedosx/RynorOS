use "lib/calc.rl";
fn main(): int {
  let p: calc::Pair = calc::Pair(a: 3, b: 4);
  print(p->a + p->b);
  return 0;
}

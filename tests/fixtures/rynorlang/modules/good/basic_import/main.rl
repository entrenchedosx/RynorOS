use "lib/calc.rl";
fn main(): int {
  let p: calc::Pair = calc::make_pair(3, 4);
  print(calc::double(p->a) + p->b);
  print(p);
  return 0;
}

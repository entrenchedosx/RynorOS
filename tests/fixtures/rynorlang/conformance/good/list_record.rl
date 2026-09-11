record Pair { a: int, b: int }
fn main(): int {
  let l: list<Pair,2> = [Pair(a: 1, b: 2), Pair(a: 3, b: 4)];
  print(l);
  return 0;
}

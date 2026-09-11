fn Point(x: int): int { return x; }
record Q { x: int }
fn main(): int {
  let q: Q = Point(x: 1);
  return 0;
}

record Point { x: int, y: int }
fn move(p: Point, dx: int): Point { return Point(x: p->x + dx, y: p->y); }
fn sum_at(l: list<int,8>, i: int): int {
  if i < len(l) {
    return unwrap_or(l[i], 0) + sum_at(l, i + 1);
  } else {
    return 0;
  }
}
fn main(): int {
  let p: Point = Point(x: 1, y: 2);
  print(move(p, 10));
  print(sum_at([5, 6, 7], 0));
  return 0;
}

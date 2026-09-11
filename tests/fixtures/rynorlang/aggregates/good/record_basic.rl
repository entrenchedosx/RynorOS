record Point { x: int, y: int }
fn dist(p: Point): int { return p->x + p->y; }
fn main(): int {
  let p: Point = Point(x: 3, y: 4);
  let q: Point = Point(y: 10, x: 1);
  print(p->x);
  print(q->y);
  print(dist(p) + dist(q));
  print(p == p);
  return 0;
}

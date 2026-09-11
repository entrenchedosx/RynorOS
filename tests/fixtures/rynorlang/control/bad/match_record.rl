record Point { x: int, y: int }
fn main(): int {
  let p: Point = Point(x: 1, y: 2);
  match p {
    _ => { return 0; }
  }
  return 0;
}

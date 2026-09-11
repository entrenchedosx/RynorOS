record Point { x: int, y: int }
fn main(): int {
  let p: Point = Point(x: 3, y: 4);
  let l: list<int,4> = [1];
  print(p);
  print([1, 2]);
  print({"a": 1});
  print(push(l, 2));
  print(get({"k": 5}, "z"));
  print(true);
  print("hi");
  print(42);
  return 0;
}

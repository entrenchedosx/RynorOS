record Inner { v: int }
record Outer { a: Inner, b: list<int,2> }
fn main(): int {
  let o: Outer = Outer(a: Inner(v: 5), b: [1, 2]);
  print(o->a->v + unwrap_or(o->b[1], 0));
  let l: list<Inner,2> = [Inner(v: 1), Inner(v: 2)];
  print(l[0]);
  return 0;
}

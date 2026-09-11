record Pair { a: int, b: int }
fn main(): int {
  print([3, 1]);
  print({2: 20, 1: 10});
  print(get({"k": 5}, "k"));
  print(get({"k": 5}, "z"));
  let r: result<int,int> = ok(7);
  print(r);
  print(Pair(a: 1, b: 2));
  return 0;
}

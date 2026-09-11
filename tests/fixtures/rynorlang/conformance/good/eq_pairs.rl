record Pair { a: int, b: int }
fn main(): int {
  print(true == false);
  print("a" == "a");
  print(Pair(a: 1, b: 2) == Pair(a: 1, b: 2));
  print([1, 2] == [1, 2]);
  print({1: 10} == {1: 10});
  let a: result<int,int> = ok(1);
  let b: result<int,int> = ok(1);
  print(a == b);
  let s1: status<int> = get({"k": 5}, "k");
  let s2: status<int> = get({"k": 5}, "k");
  print(s1 == s2);
  return 0;
}

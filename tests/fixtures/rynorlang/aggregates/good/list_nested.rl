fn main(): int {
  let l: list<list<int,2>,2> = [[1, 2], [3]];
  let e: list<int,2> = [0, 0];
  print(len(l));
  print(len(unwrap_or(l[0], e)));
  let n: list<list<int,2>,2> = [[4, 5]];
  let s: status<list<list<int,2>,2>> = push(l, [4, 5]);
  print(is_err(s));
  print(is_ok(push(n, [6])));
  return 0;
}

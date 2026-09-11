fn main(): int {
  let l: list<int,4> = [10, 20];
  let t: list<int,4> = [10, 20];
  let u: list<int,4> = [10, 21];
  print(len(l));
  print(unwrap_or(l[0], 0) + unwrap_or(l[1], 0));
  print(is_ok(l[1]));
  print(is_err(l[9]));
  print(is_err(l[0 - 1]));
  print(l == t);
  print(l == u);
  let s: status<list<int,4>> = push(l, 30);
  print(is_ok(s));
  print(len(unwrap_or(s, l)));
  let l2: list<int,2> = [1, 2];
  let f: status<list<int,2>> = push(l2, 3);
  print(is_err(f));
  return 0;
}

fn main(): int {
  let e: list<int,4> = [];
  let m: map<str,int,4> = {};
  print(len(e));
  print(len(m));
  print(e == e);
  print(m == m);
  print(is_err(e[0]));
  print(is_err(get(m, "k")));
  print(push(e, 1));
  return 0;
}

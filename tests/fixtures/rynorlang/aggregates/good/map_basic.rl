fn main(): int {
  let m: map<str,int,8> = {"a": 1, "b": 2};
  print(len(m));
  print(unwrap_or(get(m, "a"), 0) + unwrap_or(get(m, "b"), 0));
  print(is_err(get(m, "z")));
  let s: status<map<str,int,8>> = insert(m, "c", 3);
  print(is_ok(s));
  let m2: map<str,int,8> = unwrap_or(s, m);
  print(len(m2));
  let u: status<map<str,int,8>> = insert(m2, "a", 9);
  print(unwrap_or(get(unwrap_or(u, m2), "a"), 0));
  print(len(unwrap_or(u, m2)));
  return 0;
}

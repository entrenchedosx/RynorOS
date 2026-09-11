fn main(): int {
  let m: map<str,int,4> = {};
  let s1: status<map<str,int,4>> = insert(m, "a", 1);
  let m1: map<str,int,4> = unwrap_or(s1, m);
  let s2: status<map<str,int,4>> = insert(m1, "e", 2);
  let m2: map<str,int,4> = unwrap_or(s2, m1);
  print(unwrap_or(get(m2, "a"), 0) + unwrap_or(get(m2, "e"), 0));
  print(len(m2));
  print(m2);
  let s3: status<map<str,int,1>> = insert({"k": 1}, "z", 2);
  print(is_err(s3));
  return 0;
}

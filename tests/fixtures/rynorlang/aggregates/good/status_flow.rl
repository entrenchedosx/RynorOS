fn pick(flag: bool): status<int> {
  if flag {
    return get({"k": 5}, "k");
  } else {
    return get({"k": 5}, "z");
  }
}

fn main(): int {
  let a: status<int> = get({"k": 5}, "k");
  let b: status<int> = get({"k": 5}, "z");
  print(is_ok(a));
  print(is_err(b));
  print(unwrap_or(a, 0) + unwrap_or(b, 100));
  print(a == a);
  print(a == b);
  print(unwrap_or(pick(true), 0) + unwrap_or(pick(false), 0));
  return 0;
}

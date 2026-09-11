fn main(): int {
  let s: status<int> = get({"k": 1}, "k");
  print(unwrap_or(s, true));
  return 0;
}

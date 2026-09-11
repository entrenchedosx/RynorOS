fn main(): int {
  let s: status<status<int>> = get({"k": 1}, "k");
  return 0;
}

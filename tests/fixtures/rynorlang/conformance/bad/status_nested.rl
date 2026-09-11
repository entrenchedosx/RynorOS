fn main(): int {
  let s: status<status<int>> = get({"k": 5}, "k");
  return 0;
}

use "lib/helper.rl";
fn main(): int {
  let use: int = helper::value() + helper::extra();
  print(use);
  return 0;
}

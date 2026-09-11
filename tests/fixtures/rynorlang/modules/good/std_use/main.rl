use "std/math.rl";
use "std/str.rl";
use "std/test.rl";
fn main(): int {
  print(math::abs(0 - 5) + math::min(3, 9) + math::max(3, 9) + math::clamp(20, 0, 10));
  print(str::is_empty(""));
  print(str::starts_with("hello", "he"));
  print(str::byte_or("hi", 1, 7) + str::byte_or("hi", 9, 7));
  print(str::starts_at("hello", "ll", 2));
  return test::check_eq(math::abs(0 - 3), 3) + test::check(str::is_empty("")) + test::check_str_eq("a", "a");
}

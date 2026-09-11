fn main(): int {
  print(len("hello"));
  print(len(""));
  print(unwrap_or(byte_at("hello", 1), 0));
  print(unwrap_or(byte_at("hello", 0), 0));
  print(is_err(byte_at("hello", 5)));
  print(is_err(byte_at("hello", 0 - 1)));
  print(is_err(byte_at("", 0)));
  return 0;
}

fn main(): int {
  let b: map<bool,int,4> = {true: 1, false: 0};
  print(b);
  let s: map<str,int,4> = {"x": 9};
  print(s);
  return 0;
}

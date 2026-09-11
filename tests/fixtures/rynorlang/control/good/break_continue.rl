fn count(): int {
  let n: int = 0;
  while true {
    match n {
      0 => { print(10); },
      1 => { print(11); },
      _ => { break; }
    }
    return n;
  }
  return 99;
}
fn main(): int {
  print(count());
  while true {
    print(1);
    break;
    print(2);
  }
  let i: int = 0;
  while i < 2 {
    match i {
      0 => { print(20); },
      _ => { print(21); }
    }
    return 7;
  }
  return 0;
}

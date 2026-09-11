fn main(): int {
  match 7 {
    1 => { print(1); },
    7 => { print(2); },
    _ => { print(3); }
  }
  match "hi" {
    "bye" => { print(1); },
    _ => { print(2); }
  }
  match true {
    true => { print(1); },
    false => { print(0); }
  }
  match 5 {
    x => { print(x); }
  }
  return 0;
}

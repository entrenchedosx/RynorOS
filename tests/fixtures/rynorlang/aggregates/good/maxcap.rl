record Big { a: list<int,100>, b: int }
fn get100(l: list<int,1000>): int { return len(l); }
fn main(): int {
  let l: list<int,1000> = [0];
  print(len(l));
  print(get100(l));
  let m: map<int,int,255> = {};
  print(len(m));
  let b: Big = Big(a: [1], b: 2);
  print(b->b + len(b->a));
  return 0;
}

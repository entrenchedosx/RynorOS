record Hold { l: list<int,4>, m: map<str,bool,4>, n: int }
fn main(): int {
  let h: Hold = Hold(l: [1], m: {"a": true}, n: 7);
  print(len(h->l) + len(h->m) + h->n);
  print(h);
  return 0;
}

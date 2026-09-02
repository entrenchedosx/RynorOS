fn main() {
    let a: int = 1;
    let b: int = 2;
    let c: int = 3;
    let r1: bool = a < b < c;
    let r2: bool = a <= b >= c;
    let r3: bool = a > b > c;
    let r4: bool = a < b <= c >= b > a;
    let r5: bool = 1 < 2 < 3 < 4;
}

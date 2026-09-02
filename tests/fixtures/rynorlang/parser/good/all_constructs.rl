fn add(a: int, b: int): int {
    return a + b;
}
fn main() {
    let x: int = 10;
    let y: int = 20;
    let s: str = "hi\n\t\"\\";
    let flag: bool = true && false || true;
    if x < y && flag {
        let z: int = add(x, y);
        let w: int = -z * 2 + 3;
    } else if x == y {
        while false {
        }
    } else {
        return;
    }
    while x < 100 {
        let t: int = x + 1;
        let u: bool = flag == true;
    }
    let v: int = (1 + 2) * 3;
    let cmp: bool = x < y < 100;
    return;
}

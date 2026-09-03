fn main(): bool {
    let a: int = 5;
    let b: int = 10;
    let c: bool = a < b;
    let d: bool = a > b;
    let e: bool = a <= b;
    let f: bool = a >= b;
    return c && d || e;
}

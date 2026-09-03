fn zero(): int {
    return 0;
}
fn one(a: int): int {
    return a;
}
fn two(a: int, b: bool): int {
    return a;
}
fn main(): int {
    let x: int = zero();
    let y: int = one(5);
    let z: int = two(1, true);
    return x + y + z;
}

fn foo(a: int, b: int): int {
    let x: int = a + b;
    return x;
}
fn bar(): int {
    let y: int = 1;
    let z: int = 2;
    return y + z;
}
fn main(): int {
    let p: int = 10;
    let q: int = 20;
    return foo(p, q) + bar();
}

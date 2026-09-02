fn foo(a: int, b: int): int {
    return a + b;
}
fn main() {
    let x: int = foo(1, 2);
    let y: int = foo(1, 2);
    let z: int = foo(foo(1, 2), 3);
    let w: int = foo(1, foo(2, 3));
    foo(1, 2);
    foo();
}
fn no_args() {
}

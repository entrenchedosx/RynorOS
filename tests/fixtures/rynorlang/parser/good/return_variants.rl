fn foo() {
    return;
}
fn bar(): int {
    return 42;
}
fn baz(x: int): int {
    return x + 1;
}
fn qux(): bool {
    return true;
}
fn main() {
    return;
    // unreachable but syntactically valid
}

fn main(): int {
    let x: int = 10;
    if x > 5 {
        let y: int = x + 1;
        while y < 20 {
            let z: int = y + 1;
        }
        return y;
    } else {
        return x;
    }
}

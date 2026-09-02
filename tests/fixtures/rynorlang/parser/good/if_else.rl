fn main() {
    let a: bool = true;
    if a {
        let x: int = 1;
    } else {
        let y: int = 2;
    }
    if a {
        let p: int = 1;
    } else if a {
        let q: int = 2;
    } else if false {
        let r: int = 3;
    } else {
        let s: int = 4;
    }
    if true {
        if false {
            let n: int = 1;
        } else {
            let m: int = 2;
        }
    }
}

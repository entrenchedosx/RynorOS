fn id(s: str): str {
    return s;
}

fn main(): str {
    let x: str = id("a") |> echo;
    return x;
}

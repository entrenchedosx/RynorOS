fn main(): str {
    let a: int = 1 + 2 * 3;
    if ("a" |> echo) == "a" {
        return "y";
    }
    return "n";
}

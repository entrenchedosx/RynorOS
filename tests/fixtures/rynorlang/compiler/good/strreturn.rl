fn id(s: str): str { return s; }
fn main(): int { if id("") == "" && id("a\n\t\"\\") != "b" { return 2; } return 0; }

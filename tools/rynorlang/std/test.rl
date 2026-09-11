// Stage 19c standard library: conformance-check helpers (pure).
// `check` returns 0/1 so conformance mains can fold failures into the
// exit code without exceptions (there are none).
fn check(ok: bool): int {
  if ok {
    return 0;
  } else {
    return 1;
  }
}
fn check_eq(a: int, b: int): int {
  return check(a == b);
}
fn check_str_eq(a: str, b: str): int {
  return check(a == b);
}

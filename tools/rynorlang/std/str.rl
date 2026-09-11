// Stage 19c standard library: string utilities (pure, tested).
// Total: out-of-range bytes yield caller-chosen defaults.
fn is_empty(s: str): bool {
  return len(s) == 0;
}
fn byte_or(s: str, i: int, dflt: int): int {
  return unwrap_or(byte_at(s, i), dflt);
}
fn starts_with(s: str, prefix: str): bool {
  return starts_at(s, prefix, 0);
}
fn starts_at(s: str, prefix: str, i: int): bool {
  if i < len(prefix) {
    if unwrap_or(byte_at(s, i), 0 - 1) == unwrap_or(byte_at(prefix, i), 0 - 2) {
      return starts_at(s, prefix, i + 1);
    } else {
      return false;
    }
  } else {
    return true;
  }
}

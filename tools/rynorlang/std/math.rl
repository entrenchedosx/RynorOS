// Stage 19c standard library: integer utilities (pure, tested).
// Monomorphic over int by the exact-type rule; no heap, no traps.
fn abs(x: int): int {
  if x < 0 {
    return 0 - x;
  } else {
    return x;
  }
}
fn min(a: int, b: int): int {
  if a < b {
    return a;
  } else {
    return b;
  }
}
fn max(a: int, b: int): int {
  if a < b {
    return b;
  } else {
    return a;
  }
}
fn clamp(x: int, lo: int, hi: int): int {
  if x < lo {
    return lo;
  } else {
    if x > hi {
      return hi;
    } else {
      return x;
    }
  }
}

record Error { code: int, message: str }
fn fail(code: int): result<int,Error> {
  return err(Error(code: code, message: "e"));
}

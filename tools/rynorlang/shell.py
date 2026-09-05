#!/usr/bin/env python3
"""Stage 15b host-side shell surface: stub registry, TEST-ONLY evaluator,
block accumulator for future REPL use.

This module is host test infrastructure, NOT kernel code and NOT part of any
RynorOS image. It never executes in ring 0. Real command implementations
arrive with modules (Stage 19a) and the loader (Stage 16); everything here
that "runs" a command calls an explicit host stub passed in by the test.

Contents:
  DEMO_COMMANDS: documented stub signatures used by the analyze CLI and
    tests. A test-stub abstraction, not a claim about RynorOS commands.
  ShellExecError / ShellOverflow: evaluator failures (abort, never values).
  run_pipeline: evaluate one stable Pipeline AST node to a str.
  BlockAccumulator: per-block input grouping for the future userspace REPL.
"""

from __future__ import annotations

PIPE_BUF_CAP = 4096

# Demo stub signatures: name -> ([param types], return type or None).
# Deliberately tiny (str in/out plus one int/flag demo). Tests inject their
# own tables; this one exists so the CLI and shared fixtures have a stable,
# documented target. It claims nothing about real RynorOS commands.
DEMO_COMMANDS = {
    "upper": (["str"], "str"),
    "count": (["str"], "str"),
    "echo": (["str"], "str"),
    "ls": ([], "str"),
    "digest": (["str"], "str"),
    "emit": (["str"], None),
    "take": (["str", "int"], "str"),
    "withflag": (["flag"], "str"),
}


def _need_str(piped: str | None, args: list, name: str) -> str:
    if args:
        return args[0]
    if piped is not None:
        return piped
    raise ShellExecError("SHELL_COMMAND_ARITY", f"command '{name}' needs piped input or an argument")


def demo_upper(piped: str | None, args: list) -> str:
    return _need_str(piped, args, "upper").upper()


def demo_count(piped: str | None, args: list) -> str:
    return str(len(_need_str(piped, args, "count")))


def demo_echo(piped: str | None, args: list) -> str:
    return _need_str(piped, args, "echo")


def demo_ls(piped: str | None, args: list) -> str:
    void = (piped, args)
    return "f1 f2"


def demo_digest(piped: str | None, args: list) -> str:
    data = _need_str(piped, args, "digest")
    return str(sum(data.encode("ascii")) % 1000)


def demo_emit(piped: str | None, args: list) -> None:
    void = (piped, args)
    return None


def demo_take(piped: str | None, args: list) -> str:
    if len(args) < 2:
        raise ShellExecError("SHELL_COMMAND_ARITY", "take needs a string and an int")
    try:
        count = int(args[1])
    except ValueError:
        raise ShellExecError("SHELL_COMMAND_TYPE_MISMATCH", f"bad take count {args[1]!r}")
    if count < 0:
        raise ShellExecError("SHELL_COMMAND_TYPE_MISMATCH", f"take count must be >= 0, got {count}")
    return args[0][:count]


def demo_withflag(piped: str | None, args: list) -> str:
    if not args:
        raise ShellExecError("SHELL_COMMAND_ARITY", "withflag needs a -flag")
    return "got:" + args[0][1:]


DEMO_IMPLS = {
    "upper": demo_upper,
    "count": demo_count,
    "echo": demo_echo,
    "ls": demo_ls,
    "digest": demo_digest,
    "emit": demo_emit,
    "take": demo_take,
    "withflag": demo_withflag,
}


class ShellExecError(Exception):
    def __init__(self, code: str, message: str, stage: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage


class ShellOverflow(ShellExecError):
    def __init__(self, message: str, stage: int | None = None):
        super().__init__("SHELL_CAPACITY", message, stage)


def _check_cap(value: str, stage: int) -> str:
    if len(value) > PIPE_BUF_CAP:
        raise ShellOverflow(
            f"pipeline stage {stage} output {len(value)} exceeds {PIPE_BUF_CAP}", stage)
    return value


def _arg_value(node: dict, env: dict, impl_args: bool = False) -> str:
    kind = node.get("kind")
    if kind == "StrLit":
        return node["value"]
    if kind == "IntLit":
        return node["value"]
    if kind == "BoolLit":
        return "true" if node["value"] else "false"
    if kind == "Var":
        name = node["name"]
        if name not in env:
            raise ShellExecError("SHELL_UNBOUND", f"unbound pipeline variable '{name}'")
        value = env[name]
        if not isinstance(value, str):
            raise ShellExecError("SHELL_PIPELINE_TYPE_MISMATCH", f"variable '{name}' is not str")
        return value
    if kind == "Flag":
        return "-" + node["name"]
    if kind == "UnOp" and node.get("op") == "-":
        inner = node.get("operand", {})
        if inner.get("kind") == "IntLit":
            return "-" + inner["value"]
    raise ShellExecError("SHELL_UNSUPPORTED_STAGE", f"host evaluator has no rule for {kind!r}")


def _run_cmd(node: dict, env: dict, impls: dict, stage: int, piped: str | None) -> str | None:
    name = node["name"]
    impl = impls.get(name)
    if impl is None:
        raise ShellExecError("SHELL_UNKNOWN_COMMAND", f"unknown command '{name}'", stage)
    args = [_arg_value(a, env) for a in node.get("argv", [])]
    for redir in node.get("redirects", []):
        target = redir.get("target", {})
        if target.get("kind") == "StrLit":
            _check_cap(target["value"], stage)
        elif target.get("kind") == "Var":
            _arg_value(target, env)
        else:
            raise ShellExecError("SHELL_REDIRECT_ERROR", "redirect target must be str", stage)
    # Host evaluator does not model files: redirect targets are validated
    # str (semantic boundary) and otherwise unobserved. Documented MVP limit.
    result = impl(piped, args)
    if result is None:
        return None
    if not isinstance(result, str):
        raise ShellExecError("SHELL_COMMAND_TYPE_MISMATCH", f"command '{name}' returned non-str", stage)
    return _check_cap(result, stage)


def _run_stage(node: dict, env: dict, impls: dict, stage: int, piped: str | None) -> str | None:
    kind = node.get("kind")
    if kind == "Cmd":
        return _run_cmd(node, env, impls, stage, piped)
    if kind == "Pipeline":
        return run_pipeline(node, env, impls)
    if kind in ("StrLit", "Var"):
        return _check_cap(_arg_value(node, env), stage)
    raise ShellExecError("SHELL_UNSUPPORTED_STAGE",
                         f"host pipeline stage has no rule for {kind!r}", stage)


def run_pipeline(node: dict, env: dict | None = None, impls: dict | None = None) -> str | None:
    """Evaluate a stable Pipeline AST node left-to-right (TEST-ONLY).

    Each stage runs to completion into a bounded buffer; overflow raises
    ShellOverflow (never truncation). The first failing stage aborts the
    pipeline (downstream never runs); errors raise, never flow as values.
    A unit final stage returns None (legal only as an ExprStmt; the
    analyzer enforces that boundary).
    """
    if not isinstance(node, dict) or node.get("kind") != "Pipeline":
        raise ShellExecError("SHELL_INVALID_INPUT", "run_pipeline needs a Pipeline AST node")
    env = dict(env or {})
    impls = impls or {}
    stages = node.get("stages", [])
    if not stages:
        raise ShellExecError("SHELL_INVALID_INPUT", "pipeline has no stages")
    value: str | None = None
    for index, stage in enumerate(stages):
        if index > 0 and value is None:
            raise ShellExecError("SHELL_UNIT_STAGE",
                                 f"pipeline stage {index - 1} produced no value", index - 1)
        result = _run_stage(stage, env, impls, index, value)
        value = result
    if value is not None:
        _check_cap(value, len(stages) - 1)
    return value


class BlockAccumulator:
    """Group REPL input lines into brace-balanced blocks (host helper).

    push(text) appends a line and reports ("continue", None) while braces
    are unbalanced or the buffer does not yet parse, else ("ready", buffer).
    Failed submissions never mutate anything beyond the text buffer; semantic
    session state arrives with the userspace REPL (Stage 18d), not here.
    """

    def __init__(self, edition: str = "v1") -> None:
        self.edition = edition
        self.lines: list[str] = []

    def push(self, text: str) -> tuple[str, str | None]:
        self.lines.append(text)
        buffer = "\n".join(self.lines)
        if buffer.count("{") > buffer.count("}"):
            return ("continue", None)
        from tools.rynorlang.parse import parse
        result = parse(buffer, "<repl>", self.edition)
        if not result.ok:
            code = result.diagnostic.code if result.diagnostic else ""
            if code in ("PAR_UNEXPECTED_EOF",):
                return ("continue", None)
            return ("ready", buffer)
        return ("ready", buffer)

    def reset(self) -> None:
        self.lines = []

#!/usr/bin/env python3
"""Stage 19c module loader (host-side, stdlib only).

Resolves `use "path";` imports to a merged stable-AST program: file =
module, mandatory stem aliases, qualified `alias::name` calls and
types, cycle/duplicate/pin/edition errors (MOD_* family), strict
`rlmod.json` manifests, and the `std/` toolchain prefix. See
docs/design/rynorlang-modules.md for the frozen specification.

Pipeline: resolve the import graph (paths only) -> parse every file
-> analyze leaves-first with seeded globals -> merge stable ASTs
(concat + function reindex + symbol hygiene). Load errors precede all
semantic errors deterministically.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

MOD_NOT_FOUND = "MOD_NOT_FOUND"
MOD_CYCLE = "MOD_CYCLE"
MOD_DUPLICATE = "MOD_DUPLICATE"
MOD_PIN_MISMATCH = "MOD_PIN_MISMATCH"
MOD_EDITION_MISMATCH = "MOD_EDITION_MISMATCH"
MOD_BAD_MANIFEST = "MOD_BAD_MANIFEST"

STD_PREFIX = "std/"
MANIFEST_NAME = "rlmod.json"
KNOWN_EDITIONS = ("v1",)

MAX_USES_PER_FILE = 64
MAX_IMPORT_DEPTH = 16
MAX_MODULES = 64
MAX_MANIFEST_BYTES = 64 * 1024
MAX_ALIAS_LEN = 64


def _fail(code: str, message: str):
    return None, {"code": code, "message": message}


def _is_alias(text: object) -> bool:
    return (isinstance(text, str) and 0 < len(text) <= MAX_ALIAS_LEN
            and text.isascii() and (text[0].isalpha() or text[0] == "_")
            and all(c.isalnum() or c == "_" for c in text[1:]))


def _normalize_use(path_text: str):
    """Validate a use-path string; return (kind, value).

    kind "std" -> toolchain-relative path; "proj" -> project-relative
    path; "bad" -> rejection reason (absolute, .. segments, empty).
    """
    if not isinstance(path_text, str) or not path_text:
        return ("bad", "empty use path")
    if path_text.startswith("/"):
        return ("bad", f"absolute use path {path_text!r}")
    parts = path_text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return ("bad", f"illegal use path {path_text!r}")
    if path_text.startswith(STD_PREFIX):
        rest = path_text[len(STD_PREFIX):]
        if not rest or rest.endswith("/"):
            return ("bad", f"illegal use path {path_text!r}")
        return ("std", rest)
    return ("proj", path_text)


def _std_dir() -> Path:
    return Path(__file__).resolve().parent / "std"


def _read_manifest(root: Path):
    """Load and validate rlmod.json (None when absent)."""
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        return None, None
    try:
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            return None, {"code": MOD_BAD_MANIFEST, "message": "manifest too large"}
        raw = manifest_path.read_bytes()
    except OSError as error:
        return None, {"code": MOD_BAD_MANIFEST, "message": f"cannot read manifest: {error}"}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        return None, {"code": MOD_BAD_MANIFEST, "message": f"manifest is not JSON: {error}"}
    if not isinstance(data, dict) or set(data) != {"modules"}:
        return None, {"code": MOD_BAD_MANIFEST, "message": "manifest needs exactly a modules table"}
    modules = data["modules"]
    if not isinstance(modules, dict):
        return None, {"code": MOD_BAD_MANIFEST, "message": "manifest modules must be a table"}
    pins = {}
    for key, value in modules.items():
        if not isinstance(key, str):
            return None, {"code": MOD_BAD_MANIFEST, "message": "manifest keys must be paths"}
        kind, _norm = _normalize_use(key)
        if kind != "proj":
            return None, {"code": MOD_BAD_MANIFEST, "message": f"manifest pins project files only: {key!r}"}
        if not isinstance(value, dict) or set(value) != {"sha256", "edition"}:
            return None, {"code": MOD_BAD_MANIFEST, "message": f"bad pin entry for {key!r}"}
        digest, edition = value["sha256"], value["edition"]
        if (not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)):
            return None, {"code": MOD_BAD_MANIFEST, "message": f"bad sha256 for {key!r}"}
        if edition not in KNOWN_EDITIONS:
            return None, {"code": MOD_EDITION_MISMATCH, "message": f"unknown edition {edition!r} for {key!r}"}
        pins[key] = (digest, edition)
    return pins, None


def _extract_uses(program) -> list:
    """Top-level and nested use-path strings in source order.

    `use` is a statement and may appear in any block (including match
    arms); a generic pre-order walk covers every position uniformly.
    """
    uses = []
    queue = [program]
    while queue:
        node = queue.pop(0)
        for child in getattr(node, "children", ()):
            if getattr(child, "kind", None) == "UseStmt":
                path_node = child.children[0]
                value = getattr(path_node, "value", None)
                uses.append(value if isinstance(value, str) else path_node.text[1:-1])
            else:
                queue.append(child)
    return uses


def _resolve_graph(entry_path: Path, root: Path, std_dir: Path):
    """Resolve the import DAG. Returns (files, None) or (None, error).

    files maps absolute path -> {"rel": project-rel or std path,
    "alias": stem or None (entry), "uses": [path strings], "depth": int}.
    Aliases are stems; entry has alias None. Errors carry MOD_* codes.
    """
    from tools.rynorlang import parse as _parse
    files: dict[str, dict] = {}
    aliases: dict[str, str] = {}
    stack: list[str] = []

    def visit(abspath: Path, rel: str, alias: str | None, depth: int):
        key = str(abspath)
        if depth > MAX_IMPORT_DEPTH:
            return {"code": MOD_CYCLE, "message": f"import depth exceeds {MAX_IMPORT_DEPTH}"}
        if key in stack:
            # Relative chain (project-rel paths keep messages hermetic:
            # absolute paths would leak machine layout into diagnostics).
            chain = " -> ".join([files[k]["rel"] for k in stack[stack.index(key):]] + [rel])
            return {"code": MOD_CYCLE, "message": f"import cycle: {chain}"}
        if key in files:
            return None
        if len(files) >= MAX_MODULES:
            return {"code": MOD_NOT_FOUND, "message": f"too many modules (max {MAX_MODULES})"}
        try:
            result = _parse.parse_file(abspath)
        except (OSError, ValueError) as error:
            return {"code": MOD_NOT_FOUND, "message": f"cannot parse {rel}: {error}"}
        if not result.ok:
            diag = result.diagnostic
            return {"code": diag.code, "message": f"{rel}: {diag.message}"}
        uses = _extract_uses(result.root)
        if len(uses) > MAX_USES_PER_FILE:
            return {"code": MOD_NOT_FOUND, "message": f"too many imports in {rel} (max {MAX_USES_PER_FILE})"}
        files[key] = {"rel": rel, "alias": alias, "uses": uses, "depth": depth,
                      "root": result.root, "deps": []}
        if alias is not None:
            if alias in aliases:
                return {"code": MOD_DUPLICATE, "message": f"duplicate module alias '{alias}'"}
            aliases[alias] = key
        stack.append(key)
        try:
            for path_text in uses:
                kind, norm = _normalize_use(path_text)
                if kind == "bad":
                    return {"code": MOD_NOT_FOUND, "message": f"{rel}: {norm}"}
                if kind == "std":
                    child = std_dir / norm
                    child_rel = STD_PREFIX + norm
                    stem = Path(norm).stem
                else:
                    child = (abspath.parent / norm).resolve()
                    try:
                        # Frozen language-visible paths use forward slashes on
                        # every host (matches manifest keys and diagnostics).
                        child_rel = child.relative_to(root).as_posix()
                    except ValueError:
                        return {"code": MOD_NOT_FOUND, "message": f"{rel}: import escapes the project: {path_text!r}"}
                    stem = Path(norm).stem
                if not _is_alias(stem):
                    return {"code": MOD_NOT_FOUND, "message": f"{rel}: module alias must be an identifier: {stem!r}"}
                if not child.is_file():
                    return {"code": MOD_NOT_FOUND, "message": f"{rel}: no such module {path_text!r}"}
                files[key]["deps"].append((stem, str(child.resolve()), path_text))
                error = visit(child, child_rel, stem, depth + 1)
                if error is not None:
                    return error
        finally:
            stack.pop()
        return None

    if not entry_path.is_file():
        return None, {"code": MOD_NOT_FOUND, "message": f"no such entry {entry_path}"}
    error = visit(entry_path.resolve(), entry_path.name, None, 0)
    if error is not None:
        return None, error
    return files, None


def _check_pins(files: dict, pins: dict | None):
    """Enforce manifest pins (strict: every project import pinned)."""
    if pins is None:
        return None
    for key, info in files.items():
        rel = info["rel"]
        if rel.startswith(STD_PREFIX):
            continue
        if info["alias"] is None:
            continue
        if rel not in pins:
            return {"code": MOD_PIN_MISMATCH, "message": f"unpinned import with manifest present: {rel!r}"}
        digest, _edition = pins[rel]
        try:
            actual = hashlib.sha256(Path(key).read_bytes()).hexdigest()
        except OSError as error:
            return {"code": MOD_NOT_FOUND, "message": f"cannot hash {rel!r}: {error}"}
        if actual != digest:
            return {"code": MOD_PIN_MISMATCH, "message": f"hash mismatch for {rel!r}"}
    return None


def analyze_entry(entry: str | Path, profile: str = "default"):
    """Load, analyze, and merge a multi-file program.

    Returns (merged_ast, None) or (None, {"code","message"}) with
    MOD_* for load failures or the first PAR_*/SEM_* diagnostic in
    dependency order (leaves first). Under profile "strict", project
    imports require a fully-pinning manifest (R2).
    """
    from tools.rynorlang import analyze as _analyze
    entry_path = Path(entry)
    root = entry_path.resolve().parent
    pins, error = _read_manifest(root)
    if error is not None:
        return _fail(error["code"], error["message"])
    files, error = _resolve_graph(entry_path, root, _std_dir())
    if error is not None:
        return None, error
    error = _check_pins(files, pins)
    if error is not None:
        return None, error
    if pins is None and profile == "strict":
        # R2 (reproducible multi-file): strict rejects unpinned
        # project imports; std/ stays toolchain-pinned. Merge order
        # keeps the first offender deterministic.
        for key in _merge_order(files):
            rel = files[key]["rel"]
            if files[key]["alias"] is not None and not rel.startswith(STD_PREFIX):
                return None, {"code": MOD_PIN_MISMATCH,
                              "message": f"unpinned import under --profile=strict: {rel!r}"}
    error = _check_mangle_collisions(files)
    if error is not None:
        return None, error
    order = sorted(files, key=lambda key: (files[key]["depth"], files[key]["rel"]), reverse=True)
    tables: dict[str, dict] = {}
    for key in order:
        info = files[key]
        try:
            source = Path(key).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            return None, {"code": MOD_NOT_FOUND, "message": f"cannot read {info['rel']}: {error}"}
        external = _external_for(info, tables)
        analyzer = _analyze.Analyzer(info["root"], source=source, edition="v1",
                                     external=external,
                                     imports=_imports_for(info),
                                     self_alias=info["alias"],
                                     profile=profile)
        result = analyzer.analyze()
        if not result.ok:
            diag = result.diagnostic
            return None, {"code": diag.code, "message": f"{info['rel']}: {diag.message}"}
        tables[key] = {"ast": result.ast, "alias": info["alias"], "rel": info["rel"]}
    return _merge_programs(files, tables)


def _external_for(info: dict, tables: dict) -> dict:
    """Seeded globals from already-analyzed direct imports.

    Dep stable ASTs already carry mangled names (each file analyzes
    with its own alias), so seeds use them verbatim — mangling again
    would double-prefix. Seed entries mirror Analyzer globals.
    """
    external = {"funcs": {}, "records": {}}
    for alias, dep_key, _path_text in info["deps"]:
        if dep_key not in tables:
            continue
        ast = tables[dep_key]["ast"]
        for fn in ast.get("functions", []):
            external["funcs"][fn["name"]] = {
                "params": [(p["name"], p["type"], None) for p in fn["params"]],
                "ret_type": fn["ret_type"], "span": None, "symbol": -1,
            }
        for rec in ast.get("records", []):
            external["records"][rec["name"]] = {
                "fields": [(f["name"], f["type"], None) for f in rec["fields"]],
                "span": None, "symbol": -1,
            }
    return external


def _imports_for(info: dict) -> dict:
    """Alias -> dep key table for UseStmt bookkeeping."""
    return {alias: dep_key for alias, dep_key, _path_text in info["deps"]}


def _merge_order(files: dict) -> list:
    """Entry first, then depth-first use order (deterministic)."""
    entry_key = next(k for k, v in files.items() if v["alias"] is None)
    seen: set[str] = set()
    ordered: list[str] = []

    def visit(key: str) -> None:
        if key in seen:
            return
        seen.add(key)
        ordered.append(key)
        for _alias, dep_key, _path_text in files[key]["deps"]:
            visit(dep_key)

    visit(entry_key)
    for key in files:
        if key not in seen:
            ordered.append(key)
    return ordered


def _declared_names(root) -> list:
    """Top-level function/record names in source order."""
    return [child.text for child in getattr(root, "children", ())
            if getattr(child, "kind", None) in ("FunctionDef", "RecordDecl")]


def _check_mangle_collisions(files: dict):
    """Reject merged-namespace squats at load (MOD_DUPLICATE).

    Every dep file exports {alias}__{bare} for each top-level name; a
    user-declared bare name equal to any export (in any file, including
    the exporter's own) would merge indistinguishably, so it fails
    here — before analysis, in merge order. Same-file duplicates carry
    no dunder export and stay the analyzer's SEM_DUPLICATE (v1-identical).
    """
    declared: dict[str, str] = {}
    for key in _merge_order(files):
        for name in _declared_names(files[key]["root"]):
            declared.setdefault(name, files[key]["rel"])
    for key in _merge_order(files):
        info = files[key]
        if info["alias"] is None:
            continue
        for name in _declared_names(info["root"]):
            squat = f"{info['alias']}__{name}"
            if squat in declared:
                return {"code": MOD_DUPLICATE,
                        "message": (f"mangled name {squat!r} from {info['rel']!r} "
                                    f"collides with a declaration in {declared[squat]!r}")}
    return None


def _merge_programs(files: dict, tables: dict):
    """Concat stable ASTs: entry first, then depth-first use order.

    Functions reindex 0..n-1 with Call symbols rewritten through the
    merged name map; all other symbols remap with running offsets
    (hygiene: no cross-file aliasing even where engines scope
    per-function). Deterministic.
    """
    ordered = _merge_order(files)
    merged_funcs: list = []
    merged_records: list = []
    name_to_index: dict[str, int] = {}
    for key in ordered:
        ast = tables[key]["ast"]
        for fn in ast.get("functions", []):
            name_to_index[fn["name"]] = len(merged_funcs)
            merged_funcs.append(fn)
        for rec in ast.get("records", []):
            merged_records.append(rec)
    offset = 0
    for key in ordered:
        offset = _remap_tree(tables[key]["ast"], name_to_index, offset)
    program: dict = {"kind": "Program", "span": {}, "functions": merged_funcs}
    if merged_records:
        program["records"] = merged_records
    return program, None


def _remap_tree(ast: dict, name_to_index: dict, offset: int) -> int:
    """Rewrite a merged file's symbols in place; returns next offset.

    Function symbols become merged indices; Call symbols resolve through
    callee names; every other symbol int shifts by the running offset.
    """
    peak = offset

    def fresh(old: int) -> int:
        nonlocal peak
        if not isinstance(old, int) or old < 0:
            return old
        new = offset + old
        if new > peak:
            peak = new
        return new

    def walk(node: object) -> None:
        if isinstance(node, dict):
            kind = node.get("kind")
            if kind == "Function" and isinstance(node.get("name"), str):
                if node["name"] in name_to_index:
                    node["symbol"] = name_to_index[node["name"]]
            elif kind == "Call" and isinstance(node.get("callee"), str):
                if node["callee"] in name_to_index:
                    node["symbol"] = name_to_index[node["callee"]]
            else:
                if type(node.get("symbol")) is int and kind not in ("Function", "Call"):
                    node["symbol"] = fresh(node["symbol"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(ast)
    return peak + 1


def compile_entry(entry: str | Path, profile: str = "default"):
    """Full pipeline for a multi-file program: load/analyze/merge to RIR
    to assembly. Returns (asm_text, None) or (None, {"code","message"})
    with MOD_*/PAR_*/SEM_*/COMP_* codes. Never raises on bad input.
    """
    from tools.rynorlang import rir as _rir
    from tools.rynorlang import compile as _compile
    program, error = analyze_entry(entry, profile=profile)
    if error is not None:
        return None, error
    module, error = _rir.build_rir(program, str(entry))
    if error is not None:
        return None, error
    problems = _rir.verify_module(module)
    if problems:
        return None, {"code": "COMP_BAD_RIR", "message": problems[0]}
    main = next((f for f in module["funcs"] if f["name"] == "main"), None)
    if main is None or main["params"] or main["ret"] not in ("int", None):
        return None, {"code": _compile.COMP_NO_ENTRY,
                      "message": "native entry must be fn main() with ()->int or ()->unit"}
    try:
        return _compile.emit_asm(module), None
    except ValueError as error:
        return None, {"code": "COMP_EMIT_FAILED", "message": str(error)}


if __name__ == "__main__":
    raise SystemExit("module.py is a library; use compile_entry(entry)")

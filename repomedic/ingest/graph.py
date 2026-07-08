"""Build the repository intelligence model with Python AST + NetworkX.

Extracts per-module functions/classes (flagging mutable defaults and mutable
class attributes — inputs to the heuristic investigator), resolves
intra-repository imports into a dependency digraph, and maps each test module
to the source modules it transitively reaches.
"""

from __future__ import annotations

import ast
from pathlib import Path

import networkx as nx

from repomedic.ingest.detector import Detection, detect, iter_source_files
from repomedic.models.repo import ClassInfo, FunctionInfo, ImportEdge, ModuleInfo, RepoModel

MUTABLE_CALLS = {"dict", "list", "set"}


def _is_mutable_literal(node: ast.expr) -> bool:
    if isinstance(node, (ast.List, ast.Dict, ast.Set)):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in MUTABLE_CALLS
    return False


def _function_info(node: ast.FunctionDef | ast.AsyncFunctionDef, qualprefix: str,
                   is_method: bool) -> FunctionInfo:
    args = [a.arg for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs]
    mutable_defaults: list[str] = []
    positional = node.args.posonlyargs + node.args.args
    for arg, default in zip(positional[len(positional) - len(node.args.defaults):],
                            node.args.defaults, strict=True):
        if _is_mutable_literal(default):
            mutable_defaults.append(arg.arg)
    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True):
        if default is not None and _is_mutable_literal(default):
            mutable_defaults.append(arg.arg)
    decorators = [ast.unparse(d) for d in node.decorator_list]
    return FunctionInfo(
        name=node.name,
        qualname=f"{qualprefix}{node.name}",
        lineno=node.lineno,
        end_lineno=node.end_lineno or node.lineno,
        args=args,
        mutable_default_args=mutable_defaults,
        is_method=is_method,
        decorators=decorators,
    )


def parse_module(path: Path, root: Path) -> ModuleInfo:
    rel = path.relative_to(root).as_posix()
    module_name = rel[:-3].replace("/", ".")
    if module_name.endswith(".__init__"):
        module_name = module_name[: -len(".__init__")]
    text = path.read_text(encoding="utf-8", errors="replace")
    info = ModuleInfo(
        path=rel,
        module=module_name,
        is_test=path.name.startswith("test_") or path.name.endswith("_test.py"),
        loc=text.count("\n") + 1,
    )
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        info.parse_error = f"{type(exc).__name__}: {exc}"
        return info

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            info.imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            info.imports.append(base)
            # `from pkg import sub` may target a submodule; record candidates too.
            for alias in node.names:
                sep = "" if base.endswith(".") or not base else "."
                info.imports.append(f"{base}{sep}{alias.name}")

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            info.functions.append(_function_info(node, "", is_method=False))
        elif isinstance(node, ast.ClassDef):
            cls = ClassInfo(
                name=node.name,
                lineno=node.lineno,
                end_lineno=node.end_lineno or node.lineno,
                bases=[ast.unparse(b) for b in node.bases],
            )
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    cls.methods.append(_function_info(item, f"{node.name}.", is_method=True))
                elif isinstance(item, ast.Assign) and _is_mutable_literal(item.value):
                    for target in item.targets:
                        if isinstance(target, ast.Name):
                            cls.mutable_class_attrs[target.id] = item.lineno
                elif (isinstance(item, ast.AnnAssign) and item.value is not None
                      and _is_mutable_literal(item.value)
                      and isinstance(item.target, ast.Name)):
                    cls.mutable_class_attrs[item.target.id] = item.lineno
            info.classes.append(cls)
    return info


def _resolve_import(imported: str, importer: str, known: set[str]) -> str | None:
    """Resolve an import string (possibly relative) to a known repo module."""
    if imported.startswith("."):
        level = len(imported) - len(imported.lstrip("."))
        remainder = imported.lstrip(".")
        parts = importer.split(".")
        base_parts = parts[:-level] if level <= len(parts) else []
        candidate = ".".join([*base_parts, remainder]).strip(".")
    else:
        candidate = imported
    # Longest known prefix wins: "pkg.mod.func" resolves to "pkg.mod".
    probe = candidate
    while probe:
        if probe in known and probe != importer:
            return probe
        probe = probe.rpartition(".")[0]
    return None


def build_repo_model(root: Path, detection: Detection | None = None) -> RepoModel:
    root = Path(root).resolve()
    det = detection or detect(root)
    model = RepoModel(
        root=str(root),
        language=det.language,
        package_manager=det.package_manager,
        test_framework=det.test_framework,
        test_command=det.test_command,
    )
    if det.language != "python":
        return model

    for path in iter_source_files(root, ".py"):
        info = parse_module(path, root)
        model.modules[info.module] = info

    known = set(model.modules)
    graph = nx.DiGraph()
    graph.add_nodes_from(known)
    seen: set[tuple[str, str]] = set()
    for name, info in model.modules.items():
        for raw in info.imports:
            resolved = _resolve_import(raw, name, known)
            if resolved and (name, resolved) not in seen:
                seen.add((name, resolved))
                graph.add_edge(name, resolved)
                model.edges.append(ImportEdge(src=name, dst=resolved))

    for name, info in model.modules.items():
        if info.is_test:
            reachable = nx.descendants(graph, name)
            model.test_map[name] = sorted(
                m for m in reachable if not model.modules[m].is_test
            )
    return model

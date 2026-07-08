"""Repository intelligence model: files, modules, imports, functions, classes."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FunctionInfo(BaseModel):
    """A function or method discovered by static analysis."""

    name: str
    qualname: str
    lineno: int
    end_lineno: int
    args: list[str] = Field(default_factory=list)
    # Argument names whose default value is a mutable literal ([] / {} / set()).
    mutable_default_args: list[str] = Field(default_factory=list)
    is_method: bool = False
    decorators: list[str] = Field(default_factory=list)


class ClassInfo(BaseModel):
    """A class discovered by static analysis."""

    name: str
    lineno: int
    end_lineno: int
    methods: list[FunctionInfo] = Field(default_factory=list)
    # Class-level attribute names assigned a mutable literal, e.g. `cache = {}`.
    mutable_class_attrs: dict[str, int] = Field(default_factory=dict)  # name -> lineno
    bases: list[str] = Field(default_factory=list)


class ModuleInfo(BaseModel):
    """One Python module (file) in the repository."""

    path: str  # repo-relative POSIX path
    module: str  # dotted module name, e.g. "src.cache"
    imports: list[str] = Field(default_factory=list)  # dotted names as written
    functions: list[FunctionInfo] = Field(default_factory=list)
    classes: list[ClassInfo] = Field(default_factory=list)
    is_test: bool = False
    loc: int = 0
    parse_error: str | None = None

    def all_functions(self) -> list[FunctionInfo]:
        out = list(self.functions)
        for cls in self.classes:
            out.extend(cls.methods)
        return out


class ImportEdge(BaseModel):
    """A resolved intra-repository import: src module imports dst module."""

    src: str
    dst: str


class RepoModel(BaseModel):
    """Everything RepoMedic knows about a repository after ingest + graph."""

    root: str
    language: str = "unknown"
    package_manager: str = "unknown"
    test_framework: str = "unknown"
    test_command: list[str] = Field(default_factory=list)
    modules: dict[str, ModuleInfo] = Field(default_factory=dict)  # module name -> info
    edges: list[ImportEdge] = Field(default_factory=list)
    # test module name -> source module names it (transitively) touches
    test_map: dict[str, list[str]] = Field(default_factory=dict)

    @property
    def module_count(self) -> int:
        return len(self.modules)

    def module_for_path(self, rel_path: str) -> ModuleInfo | None:
        rel = rel_path.replace("\\", "/")
        for info in self.modules.values():
            if info.path == rel:
                return info
        return None

"""Ordinary branch copies, explicit file merges, and safe artifact lookup.

All workspaces are directories within a project. No content hashing or hidden
history is used. Fork bases are ordinary editable directories, deletable by the
owner; a missing base downgrades merges to an explicit two-way comparison.
"""
from __future__ import annotations

import difflib
import os
import shutil
from pathlib import Path
from uuid import uuid4

from .errors import GraphError

IGNORED = {".git", ".venv", "node_modules", "__pycache__", ".forest-bases", "branches", ".merge-staging", ".forest-interventions"}
DATA_DIRS = {"data", "datasets"}
RESULT_DIRS = {"results", "runs", "outputs", "figures"}


def safe_path(root: Path, value: str | Path) -> Path:
    root = Path(root).resolve()
    candidate = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    if candidate != root and root not in candidate.parents:
        raise GraphError("unsafe_path", "The path escapes the project or branch workspace.")
    return candidate


class ArtifactResolver:
    def __init__(self, project_dir, graph=None):
        self.project_dir = Path(project_dir).resolve()
        self.graph = graph or {}

    def branch_path(self, branch_id):
        branch = next((b for b in self.graph.get("branches", []) if b["id"] == branch_id), None)
        if branch is None:
            raise GraphError("missing_branch", f"Branch {branch_id!r} does not exist.", status_code=404)
        return safe_path(self.project_dir, branch.get("workspace") or f"branches/{branch_id}")

    def resolve(self, reference, branch_id):
        ref = {"path": reference} if isinstance(reference, str) else dict(reference)
        if ref.get("project_id") not in (None, self.graph.get("project_id")):
            raise GraphError("cross_project_reference", "Artifacts must belong to the current project.")
        source_branch = ref.get("branch_id", branch_id)
        result = {"reference": ref, "source_branch_id": source_branch, "imported": source_branch != branch_id,
                  "source": ref.get("source", "file"), "available": False}
        node_id = ref.get("node_id")
        if node_id:
            node = next((n for n in self.graph.get("nodes", []) if n["id"] == node_id), None)
            if not node:
                return {**result, "error": "missing_node", "message": "The referenced node was deleted."}
            if ref.get("branch_id") and ref["branch_id"] != node.get("branch_id"):
                return {**result, "error": "branch_mismatch", "message": "The referenced node belongs to another branch."}
            source_branch = node.get("branch_id", source_branch)
            result.update(source_branch_id=source_branch, imported=source_branch != branch_id)
            result["node_revision"] = node.get("revision", 0)
            result["stale"] = ref.get("revision", node.get("revision")) != node.get("revision")
            if not ref.get("path"):
                return {**result, "available": True, "node": node}
        if not ref.get("path"):
            return {**result, "error": "missing_path", "message": "No artifact path was provided."}
        try:
            # project_scope is explicit; default paths are relative to the source branch.
            root = self.project_dir if ref.get("project_scope") else self.branch_path(source_branch)
            path = safe_path(root, ref["path"])
            # Main workspaces can be the project root. A nested branch is still
            # a separate scope even though ordinary path containment permits it.
            for candidate in self.graph.get("branches", []):
                if candidate["id"] == source_branch:
                    continue
                candidate_root = self.branch_path(candidate["id"])
                if candidate_root != self.project_dir and (path == candidate_root or candidate_root in path.parents):
                    return {**result, "error": "branch_not_visible", "message": "Import this artifact using its actual source branch."}
        except GraphError as exc:
            return {**result, "error": exc.code, "message": exc.message}
        result.update(path=str(path), relative_path=str(path.relative_to(self.project_dir)))
        if not path.is_file():
            return {**result, "error": "missing_file", "message": "The referenced file is no longer available."}
        return {**result, "available": True, "size": path.stat().st_size}


def _files(root: Path):
    if not root.is_dir():
        return {}
    result = {}
    for directory, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = sorted(d for d in directories if d not in IGNORED and not (Path(directory) / d).is_symlink())
        for name in sorted(filenames):
            path = Path(directory) / name
            if path.is_symlink():
                # A branch copy is independent; never copy links into another workspace.
                continue
            result[path.relative_to(root).as_posix()] = path
    return result


def _same(left, right):
    if left is None or right is None:
        return left is right
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as a, right.open("rb") as b:
        while True:
            x, y = a.read(65536), b.read(65536)
            if x != y:
                return False
            if not x:
                return True


def _text(path):
    if path is None:
        return ""
    if path.stat().st_size > 128_000:
        return None
    try:
        return path.read_text("utf-8")
    except UnicodeDecodeError:
        return None


class BranchWorkspace:
    def __init__(self, project_dir, graph):
        self.project_dir = Path(project_dir).resolve()
        self.graph = graph
        self.resolver = ArtifactResolver(project_dir, graph)

    def _branch(self, branch):
        if isinstance(branch, dict):
            return branch
        found = next((b for b in self.graph.get("branches", []) if b["id"] == branch), None)
        if found is None:
            raise GraphError("missing_branch", f"Branch {branch!r} does not exist.", status_code=404)
        return found

    def _selection(self, root, policy):
        if not isinstance(policy,dict): raise GraphError('copy_policy','Copy policy must be an object.')
        maximum=policy.get('max_copy_bytes',100*1024*1024)
        if type(maximum) is not int or maximum<0: raise GraphError('copy_budget','max_copy_bytes must be a nonnegative integer.')
        selected, refs = {}, []
        for rel, path in _files(root).items():
            top = Path(rel).parts[0]
            kind = "data" if top in DATA_DIRS else "results" if top in RESULT_DIRS else "code"
            strategy = policy.get(kind, "reference" if kind == "data" else "exclude" if kind == "results" else "copy")
            if isinstance(strategy, bool):
                strategy = "copy" if strategy else "exclude"
            if strategy not in {"copy", "reference", "exclude"}:
                raise GraphError("copy_policy", f"Unknown {kind} copy policy: {strategy}")
            if strategy == "reference":
                refs.append({"path": str(path.relative_to(self.project_dir)), "project_scope": True, "source": kind})
            elif strategy == "copy":
                selected[rel] = path
        total = sum(p.stat().st_size for p in selected.values())
        if total > maximum:
            raise GraphError("copy_budget", f"The selected files total {total} bytes; use references or increase max_copy_bytes.")
        return selected, refs

    def fork(self, node_id, copy_policy=None, *, branch_id=None, dry_run=False, capture_sources=False):
        policy = copy_policy or {}
        node = next((n for n in self.graph["nodes"] if n["id"] == node_id), None)
        if node is None:
            raise GraphError("missing_node", "The fork origin does not exist.")
        source = self._branch(node["branch_id"])
        origin = self.resolver.branch_path(source["id"])
        selected, refs = self._selection(origin, policy)
        branch_id = branch_id or str(uuid4())
        dest = safe_path(self.project_dir, f"branches/{branch_id}")
        base = safe_path(self.project_dir, f".forest-bases/{branch_id}")
        if not dry_run:
            if dest.exists() or base.exists():
                raise GraphError("workspace_exists", "This branch workspace already exists.", status_code=409)
            try:
                dest.mkdir(parents=True)
                base.mkdir(parents=True)
                for rel, path in selected.items():
                    for target in (dest / rel, base / rel):
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, target)
            except Exception:
                shutil.rmtree(dest, ignore_errors=True)
                shutil.rmtree(base, ignore_errors=True)
                raise
        return {"branch_id": branch_id, "workspace": str(dest.relative_to(self.project_dir)),
                "base_workspace": str(base.relative_to(self.project_dir)), "parent_branch_id": source["id"],
                "base_config": source.get("config", {}), "files": sorted(selected), "input_mapping": refs,
                "copy_policy": policy, **({'_materialization': {
                    rel: {'kind': 'copy', 'source': str(path.relative_to(self.project_dir))}
                    for rel, path in selected.items()}} if capture_sources else {})}

    def _base(self, left, right):
        base = None
        if right.get("parent_branch_id") == left["id"]:
            base = right.get("base_workspace")
        elif left.get("parent_branch_id") == right["id"]:
            base = left.get("base_workspace")
        elif left.get("parent_branch_id") and left.get("parent_branch_id") == right.get("parent_branch_id"):
            lp = safe_path(self.project_dir, left["base_workspace"]) if left.get("base_workspace") else None
            rp = safe_path(self.project_dir, right["base_workspace"]) if right.get("base_workspace") else None
            if lp and rp and lp.is_dir() and rp.is_dir() and left.get("base_config", {}) == right.get("base_config", {}):
                lf, rf = _files(lp), _files(rp)
                if lf.keys() == rf.keys() and all(_same(lf[p], rf[p]) for p in lf):
                    base = left["base_workspace"]
        if base:
            root = safe_path(self.project_dir, base)
            if root.is_dir():
                return root
        return None

    def compare(self, left, right):
        left, right = self._branch(left), self._branch(right)
        lf = _files(self.resolver.branch_path(left["id"]))
        rf = _files(self.resolver.branch_path(right["id"]))
        base = self._base(left, right)
        bf = _files(base) if base else {}
        rows = []
        for rel in sorted(set(lf) | set(rf) | set(bf)):
            l, r, b = lf.get(rel), rf.get(rel), bf.get(rel)
            if _same(l, r):
                status, choice = "identical", "left"
            elif base and _same(l, b):
                status, choice = "right_changed", "right"
            elif base and _same(r, b):
                status, choice = "left_changed", "left"
            else:
                status, choice = "conflict", None
            lt, rt = _text(l), _text(r)
            row = {"path": rel, "status": status, "choice": choice, "left_exists": l is not None,
                   "right_exists": r is not None, "base_exists": b is not None}
            if lt is not None and rt is not None:
                row["diff"] = "".join(difflib.unified_diff(lt.splitlines(True), rt.splitlines(True), fromfile="left/" + rel, tofile="right/" + rel))[:16000]
            rows.append(row)
        return {"left": left["id"], "right": right["id"], "mode": "three_way" if base else "two_way",
                "base_workspace": str(base.relative_to(self.project_dir)) if base else None, "files": rows,
                "config": {"left": left.get("config", {}), "right": right.get("config", {})},
                "results": {b["id"]: [{"node_id": n["id"], "outputs": n.get("outputs", []), "research_status": n.get("research_status")}
                                     for n in self.graph["nodes"] if n["branch_id"] == b["id"]] for b in (left, right)}}

    def merge(self, left, right, resolution=None, *, branch_id=None, dry_run=False, capture_sources=False):
        left, right = self._branch(left), self._branch(right)
        resolution = resolution or {}
        comparison = self.compare(left, right)
        choices = resolution.get("files", resolution)
        conflicts, selected = [], {}
        roots = {"left": self.resolver.branch_path(left["id"]), "right": self.resolver.branch_path(right["id"])}
        if comparison["base_workspace"]:
            roots["base"] = safe_path(self.project_dir, comparison["base_workspace"])
        for item in comparison["files"]:
            rel = item["path"]
            choice = choices.get(rel, item["choice"])
            if choice is None:
                conflicts.append(item)
            elif isinstance(choice, dict) and isinstance(choice.get("content"), str):
                selected[rel] = ("content", choice["content"])
            elif choice == "delete":
                selected[rel] = ("delete", None)
            elif isinstance(choice, str) and choice in roots:
                path = safe_path(roots[choice], rel)
                selected[rel] = ("copy", path) if path.is_file() else ("delete", None)
            else:
                raise GraphError("invalid_resolution", f"Choose left, right, base, delete, or content for {rel}.")
        lcfg, rcfg = left.get("config", {}), right.get("config", {})
        config = resolution.get("config")
        if config is None:
            if lcfg == rcfg:
                config = lcfg
            elif comparison["mode"] == "three_way":
                basecfg = right.get("base_config", {}) if right.get("parent_branch_id") == left["id"] else left.get("base_config", {})
                if lcfg == basecfg:
                    config = rcfg
                elif rcfg == basecfg:
                    config = lcfg
            if config is None:
                conflicts.append({"path": "@config", "status": "conflict", "left": lcfg, "right": rcfg})
        if isinstance(config, str):
            if config not in {"left", "right"}:
                raise GraphError("invalid_resolution", "Configuration resolution must be left, right, or an object.")
            config = lcfg if config == "left" else rcfg
        if config is not None and not isinstance(config, dict):
            raise GraphError("invalid_resolution", "The merged configuration must be an object.")
        if conflicts and not dry_run:
            raise GraphError("merge_conflict", "Resolve the listed file and configuration conflicts before merging.", status_code=409, detail={"conflicts": conflicts})
        branch_id = branch_id or str(uuid4())
        dest = safe_path(self.project_dir, f"branches/{branch_id}")
        if not dry_run and not conflicts:
            if dest.exists():
                raise GraphError("workspace_exists", "The merged workspace already exists.", status_code=409)
            staging = safe_path(self.project_dir, f".merge-staging/{branch_id}")
            staging.mkdir(parents=True, exist_ok=False)
            try:
                for rel, (kind, value) in selected.items():
                    target = safe_path(staging, rel)
                    if kind != "delete":
                        target.parent.mkdir(parents=True, exist_ok=True)
                    if kind == "copy":
                        shutil.copy2(value, target)
                    elif kind == "content":
                        target.write_text(value, encoding="utf-8")
                dest.parent.mkdir(parents=True, exist_ok=True)
                staging.rename(dest)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        return {**comparison, "branch_id": branch_id, "workspace": str(dest.relative_to(self.project_dir)),
                "config": config or {}, "conflicts": conflicts, "requires_revalidation": True,
                "reevaluate_inputs": [n["id"] for n in self.graph["nodes"] if n["branch_id"] in (left["id"], right["id"])],
                **({'_materialization': {rel: {'kind': kind, **({'source': str(value.relative_to(self.project_dir))}
                    if kind == 'copy' else {'content': value} if kind == 'content' else {})}
                    for rel, (kind, value) in selected.items()}} if capture_sources else {})}

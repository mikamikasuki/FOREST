"""Editable research DAG commands, selective impact, and ordinary undo history.

The service copies its input. Persist the returned graph, including `_history`,
inside the API transaction. Request deduplication and scheduling belong to the
API. Preview never writes files; apply writes independent branch workspaces.
Undo restores editable graph content; it never reverses processes or file edits.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from .artifacts import BranchWorkspace
from .errors import GraphError

EXECUTION = {"depends_on", "consumes"}
PROPAGATION = EXECUTION | {"derived_from", "evidence", "cites"}
VISUAL_FIELDS = {"position", "color", "collapsed", "selected", "width", "height", "group_id"}
DISPLAY_FIELDS = {"title", "comments", "description"}
RUNTIME_FIELDS = {"execution_status", "research_status", "last_run_id", "last_run_revision", "outputs", "last_result", "run_ids"}
OPERATIONS = {"add_node", "edit_node", "delete_node", "add_dependency", "remove_dependency", "fork_branch", "clone_subtree",
              "insert_before", "insert_after", "reparent_subtree", "merge_branches", "split_node", "group_nodes", "prune_branch",
              "restore_branch", "set_main_branch", "apply_instruction_patch", "undo", "redo"}
ANALYSIS_TYPES = {"analysis", "statistics", "statistical_analysis", "evaluation"}
FIGURE_TYPES = {"figure", "chart"}
PAPER_TYPES = {"paper", "writing", "review"}


def _uid():
    return str(uuid4())


def _index(graph):
    return {n["id"]: n for n in graph.get("nodes", [])}


def execution_edges(graph):
    """Include file and verification bindings in the editable execution DAG."""
    nodes = _index(graph)
    edges = [e for e in graph.get("edges", []) if e.get("relation", "depends_on") in EXECUTION]
    pairs = {(e["source"], e["target"]) for e in edges}
    for node in nodes.values():
        config = node.get("config") or {}
        sources = []
        for ref in node.get("inputs", []):
            if isinstance(ref, dict):
                sources.extend([ref.get("node_id"), ref.get("verification_node_id")])
        verification = config.get("verification") or {}
        if isinstance(verification, dict):
            sources.append(verification.get("producer_node_id"))
        required = config.get("required_verification") or []
        if isinstance(required, list):
            sources.extend(required)
        for source in sources:
            if isinstance(source, str) and source in nodes and (source, node["id"]) not in pairs:
                edges.append({"source": source, "target": node["id"], "relation": "consumes", "implicit": True})
                pairs.add((source, node["id"]))
    return edges


def _closure(graph, ids, *, reverse=False, relations=EXECUTION):
    adjacency = {}
    edges = graph.get("edges", [])
    if relations & EXECUTION:
        edges = [e for e in edges if e.get("relation", "depends_on") not in EXECUTION] + execution_edges(graph)
    for edge in edges:
        if edge.get("relation", "depends_on") in relations:
            source, target = (edge["target"], edge["source"]) if reverse else (edge["source"], edge["target"])
            adjacency.setdefault(source, []).append(target)
    result, pending = set(ids), deque(ids)
    while pending:
        for child in adjacency.get(pending.popleft(), []):
            if child not in result:
                result.add(child)
                pending.append(child)
    return result


def topological_order(graph, node_ids=None):
    """Return execution order, ignoring evidence/history/grouping relationships."""
    ids = set(node_ids) if node_ids is not None else set(_index(graph))
    degree = {n: 0 for n in ids}
    children = {n: [] for n in ids}
    for e in execution_edges(graph):
        if e.get("relation", "depends_on") in EXECUTION and e["source"] in ids and e["target"] in ids:
            children[e["source"]].append(e["target"])
            degree[e["target"]] += 1
    queue = deque(sorted(n for n in ids if degree[n] == 0))
    ordered = []
    while queue:
        node = queue.popleft()
        ordered.append(node)
        for child in sorted(children[node]):
            degree[child] -= 1
            if degree[child] == 0:
                queue.append(child)
    if len(ordered) != len(ids):
        raise GraphError("dependency_cycle", "Execution dependencies must form a directed acyclic graph.", detail={"nodes": sorted(n for n, d in degree.items() if d)})
    return ordered


def validate_graph(graph):
    nodes = _index(graph)
    branches = {b["id"] for b in graph.get("branches", [])}
    if len(nodes) != len(graph.get("nodes", [])):
        raise GraphError("duplicate_node", "Node IDs must be unique.")
    for node in nodes.values():
        if node.get("project_id") not in (None, graph.get("project_id")):
            raise GraphError("cross_project_reference", "A node belongs to another project.")
        if node.get("branch_id") not in branches:
            raise GraphError("missing_branch", f"Node {node['id']} references a missing branch.")
        for ref in node.get("inputs", []):
            if isinstance(ref, dict) and ref.get("project_id") not in (None, graph.get("project_id")):
                raise GraphError("cross_project_reference", "An input belongs to another project.")
            # Missing references remain visible and block execution at scheduling time.
    for edge in graph.get("edges", []):
        if edge["source"] not in nodes or edge["target"] not in nodes:
            raise GraphError("missing_endpoint", "Both edge endpoints must be existing project nodes.")
    topological_order(graph)


def _deep_merge(original, patch):
    result = deepcopy(original)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


class ImpactAnalyzer:
    def __init__(self, graph):
        self.graph = graph

    def resolve(self, changed_objects):
        """Accept IDs or {id,category,reason} changes; return explainable effects."""
        changes = [{"id": x, "category": "semantic"} if isinstance(x, str) else x for x in changed_objects]
        nodes = _index(self.graph)
        changed, affected, rerun, refresh, reasons = set(), set(), set(), set(), {}
        categories = {}
        for change in changes:
            node_id, category = change["id"], change.get("category", "semantic")
            changed.add(node_id)
            categories[node_id] = category
            reason = change.get("reason", f"{category} edit")
            reasons.setdefault(node_id, []).append(reason)
            if category in {"layout", "display", "archive"}:
                refresh.add(node_id)
                continue
            related = _closure(self.graph, [node_id], relations=PROPAGATION)
            # Explicit input references participate even when no canvas edge exists.
            while True:
                expanded = {n["id"] for n in nodes.values() if any(isinstance(r, dict) and r.get("node_id") in related for r in n.get("inputs", []))}
                expanded |= _closure(self.graph, expanded, relations=PROPAGATION)
                if expanded <= related:
                    break
                related |= expanded
            for target in related:
                affected.add(target)
                typ = nodes.get(target, {}).get("type", "")
                if category in {"goal", "context", "archive"}:
                    refresh.add(target)
                elif category == "wording":
                    if target == node_id or typ in PAPER_TYPES:
                        refresh.add(target)
                elif category == "style":
                    if target == node_id or typ in FIGURE_TYPES | PAPER_TYPES:
                        refresh.add(target)
                elif category == "statistics":
                    if target == node_id or typ in ANALYSIS_TYPES | FIGURE_TYPES | PAPER_TYPES:
                        (refresh if typ in FIGURE_TYPES | PAPER_TYPES else rerun).add(target)
                elif typ in FIGURE_TYPES | PAPER_TYPES:
                    refresh.add(target)
                else:
                    rerun.add(target)
                if target != node_id:
                    reasons.setdefault(target, []).append(f"Consumes material affected by {node_id}: {reason}")
        return {"changed_nodes": sorted(changed), "affected_nodes": sorted(affected), "rerun_nodes": sorted(rerun & nodes.keys()),
                "refresh_nodes": sorted(refresh & nodes.keys()), "reasons": reasons, "categories": categories, "files": [], "conflicts": []}


class GraphCommandService:
    def __init__(self, graph, project_dir):
        self.graph = deepcopy(graph)
        self.graph.setdefault("revision", 0)
        self.graph.setdefault("nodes", [])
        self.graph.setdefault("edges", [])
        self.graph.setdefault("branches", [])
        self.project_dir = Path(project_dir).resolve()

    def preview(self, command):
        return self._execute(command, dry_run=True)["impact"]

    def apply(self, command):
        return self._execute(command, dry_run=False)

    def _check(self, command):
        if command.get("project_id") not in (None, self.graph.get("project_id")):
            raise GraphError("cross_project_reference", "The command belongs to another project.")
        if command.get("expected_revision") is None:
            raise GraphError("expected_revision_required", "Supply the graph revision read before this edit.")
        if command["expected_revision"] != self.graph["revision"]:
            raise GraphError("revision_conflict", "The graph changed in another window. Reload and reapply this edit.", status_code=409,
                             detail={"expected_revision": command["expected_revision"], "current_revision": self.graph["revision"]})
        if command.get("operation") not in OPERATIONS:
            raise GraphError("unknown_operation", "Unknown graph command.")

    def _execute(self, command, dry_run):
        self._check(command)
        graph = deepcopy(self.graph)
        before = self._snapshot(graph)
        operation, params = command["operation"], deepcopy(command.get("params") or {})
        targets = command.get("targets") or ([command["node_id"]] if command.get("node_id") else [])
        changes, files, conflicts, actions, missing = [], [], [], [], []
        nodes = _index(graph)

        def require(node_id=None):
            node_id = node_id or (targets[0] if targets else None)
            if node_id not in nodes:
                raise GraphError("missing_node", f"Node {node_id!r} does not exist.", status_code=404)
            return nodes[node_id]

        def branch(branch_id=None):
            branch_id = branch_id or params.get("branch_id") or (require()["branch_id"] if targets else None)
            found = next((b for b in graph["branches"] if b["id"] == branch_id), None)
            if not found:
                raise GraphError("missing_branch", f"Branch {branch_id!r} does not exist.")
            return found

        def changed(node, category="semantic", reason=None):
            node_id = node if isinstance(node, str) else node["id"]
            changes.append({"id": node_id, "category": category, "reason": reason or operation})
            if node_id in nodes and category not in {"layout", "display", "archive"}:
                nodes[node_id]["revision"] = nodes[node_id].get("revision", 0) + 1

        def add(fields=None, branch_id=None):
            fields = deepcopy(fields or {})
            nid = fields.pop("id", None) or _uid()
            if nid in nodes:
                raise GraphError("duplicate_node", f"Node {nid} already exists.")
            if branch_id is None:
                branch_id = fields.pop("branch_id", None) or params.get("branch_id")
            if branch_id is None:
                main = next((b for b in graph["branches"] if b.get("is_main")), None)
                if main is None and graph["branches"]:
                    main = graph["branches"][0]
                if main is None:
                    main = {"id": _uid(), "name": "Main", "status": "active", "workspace": ".", "is_main": True, "config": {}}
                    graph["branches"].append(main)
                branch_id = main["id"]
            node = {"id": nid, "project_id": graph["project_id"], "branch_id": branch_id, "type": "idea", "title": "New step",
                    "instructions": "", "revision": 0, "config": {}, "position": {"x": 0, "y": 0}, "execution_status": "idle",
                    "research_status": "not_evaluated", "deliverable_status": "draft", "archived": False,
                    "inputs": [], "outputs": [], "context_overrides": {}, "comments": [], **fields}
            if node["project_id"] != graph["project_id"]:
                raise GraphError("cross_project_reference", "Cannot create a node in another project.")
            graph["nodes"].append(node)
            nodes[nid] = node
            b = branch(branch_id)
            if not b.get("root_node_id"):
                b["root_node_id"] = nid
            changes.append({"id": nid, "category": "semantic", "reason": "New editable research step"})
            return node

        def edge(source, target, relation="depends_on", **extra):
            require(source)
            require(target)
            if not any(e["source"] == source and e["target"] == target and e.get("relation") == relation for e in graph["edges"]):
                graph["edges"].append({"id": _uid(), "source": source, "target": target, "relation": relation, **extra})

        def remap_inputs(value, mapping):
            if isinstance(value, list):
                return [remap_inputs(v, mapping) for v in value]
            if isinstance(value, dict):
                return {k: ([mapping.get(item, item) for item in v] if k == "required_verification" and isinstance(v, list)
                            else mapping.get(v, v) if k in {"node_id", "source_node_id", "producer_node_id", "verification_node_id"} and isinstance(v, str)
                            else remap_inputs(v, mapping)) for k, v in value.items()}
            return value

        def clone(ids, branch_id, copy_results="reference"):
            if isinstance(copy_results, bool):
                copy_results = "copy" if copy_results else "exclude"
            mapping = {nid: _uid() for nid in ids}
            for nid in sorted(ids):
                source = require(nid)
                fields = deepcopy(source)
                fields.update(id=mapping[nid], branch_id=branch_id, revision=0, execution_status="idle", research_status="not_evaluated", deliverable_status="draft")
                fields["inputs"] = remap_inputs(fields.get("inputs", []), mapping)
                fields["config"] = remap_inputs(fields.get("config", {}), mapping)
                # Execution receipts belong to the original route. A clone
                # retains its editable checks and obtains its own verdict.
                for key in ("verification", "verification_status", "latest_verification_run_id"):
                    fields.pop(key, None)
                fields["results_current"] = False
                for ref in fields["inputs"]:
                    if isinstance(ref, dict) and ref.get("node_id") in mapping.values() and "branch_id" in ref:
                        ref["branch_id"] = branch_id
                if copy_results == "exclude":
                    fields["outputs"] = []
                else:
                    fields["outputs"] = [{**r, "source_node_id": nid, "branch_id": source["branch_id"], "inherited": True} if isinstance(r, dict)
                                         else {"path": r, "source_node_id": nid, "branch_id": source["branch_id"], "inherited": True} for r in fields.get("outputs", [])]
                    fields["inherited_results"] = bool(fields["outputs"])
                    inherited_materials = [{k: v for k, v in ref.items() if k != "node_id"} for ref in fields["outputs"] if ref.get("path")]
                    fields.setdefault("context_overrides", {}).setdefault("imports", []).extend(inherited_materials)
                fields.pop("last_run_id", None)
                fields.pop("latest_run_id", None)
                fields.pop("last_result", None)
                fields["position"] = {"x": source.get("position", {}).get("x", 0) + 100, "y": source.get("position", {}).get("y", 0) + 80}
                add(fields, branch_id)
            for old in list(graph["edges"]):
                if old["source"] in ids and old["target"] in ids:
                    edge(mapping[old["source"]], mapping[old["target"]], old.get("relation", "depends_on"), input_mapping=remap_inputs(old.get("input_mapping", {}), mapping))
            return mapping

        if operation == "add_node":
            fields = params.get("node", params)
            node = add(fields)
            if params.get("parent_id"):
                edge(params["parent_id"], node["id"])
        elif operation in {"edit_node", "apply_instruction_patch"}:
            patch = params.get("patch", params)
            if operation == "apply_instruction_patch":
                original = require().get("instructions", "")
                if "instructions" in params:
                    patch = {"instructions": params["instructions"]}
                elif "old_text" in params:
                    old = params["old_text"]
                    if not old or original.count(old) != 1:
                        raise GraphError("patch_conflict", "The instruction patch must match one exact passage.", status_code=409)
                    patch = {"instructions": original.replace(old, params.get("new_text", ""), 1)}
                else:
                    raise GraphError("invalid_patch", "Supply instructions or old_text/new_text.")
            immutable = {"id", "project_id", "revision", "execution_status", "last_run_id", "last_run_revision"}
            if immutable & patch.keys():
                raise GraphError("invalid_patch", "Use runtime endpoints to change execution state; identifiers and revision are managed by the kernel.")
            for target in targets:
                node = require(target)
                changed_fields = {k for k in patch if patch[k] != node.get(k)}
                if not changed_fields:
                    continue
                category = self._category(node, patch, changed_fields)
                node.update(_deep_merge(node, patch))
                changed(node, category)
                if node.get("execution_status") == "running" and category not in {"layout", "display"}:
                    node["edited_during_run"] = True
                    if params.get("stop_current_run"):
                        actions.append({"action": "cancel_current_run", "node_id": target, "run_id": node.get("last_run_id")})
        elif operation == "delete_node":
            for nid in targets:
                require(nid)
            strategy = params.get("strategy")
            selected = set(targets)
            descendants = _closure(graph, selected) - selected
            if descendants and strategy not in {"subtree", "reconnect", "visual_only"}:
                raise GraphError("delete_strategy_required", "Choose subtree, reconnect, or visual_only when deleting a parent.", detail={"descendants": sorted(descendants)})
            if strategy == "visual_only":
                graph["edges"] = [e for e in graph["edges"] if not (e.get("relation") in {"group", "visual"} and (e["source"] in selected or e["target"] in selected))]
                for nid in selected:
                    require(nid).pop("group_id", None)
                    changed(nid, "layout")
            else:
                if strategy == "subtree":
                    selected |= descendants
                if strategy == "reconnect":
                    # Preserve reachability through a chain of multiple selected nodes.
                    for outside in set(nodes) - selected:
                        reachable = {outside}
                        frontier = [outside]
                        while frontier:
                            current = frontier.pop()
                            for e in graph["edges"]:
                                if e["source"] == current and e.get("relation") in EXECUTION and e["target"] not in reachable:
                                    reachable.add(e["target"])
                                    if e["target"] in selected:
                                        frontier.append(e["target"])
                                    elif current in selected:
                                        edge(outside, e["target"])
                for nid in selected:
                    changed(nid, "semantic", "Deleted material; file and run side effects are retained")
                for n in graph["nodes"]:
                    if n["id"] not in selected:
                        for ref in n.get("inputs", []):
                            if isinstance(ref, dict) and ref.get("node_id") in selected:
                                ref.update(available=False, missing=True)
                                missing.append({"node_id": n["id"], "reference": ref})
                # Include original descendants in impact before removing their edges.
                for nid in descendants - selected:
                    changes.append({"id": nid, "category": "semantic", "reason": "An upstream node was deleted"})
                graph["nodes"] = [n for n in graph["nodes"] if n["id"] not in selected]
                graph["edges"] = [e for e in graph["edges"] if e["source"] not in selected and e["target"] not in selected]
                for b in graph["branches"]:
                    if b.get("root_node_id") in selected:
                        b["root_node_id"] = next((n["id"] for n in graph["nodes"] if n["branch_id"] == b["id"]), None)
        elif operation in {"add_dependency", "remove_dependency"}:
            source = params.get("source") or (targets[0] if targets else None)
            target = params.get("target") or (targets[1] if len(targets) > 1 else None)
            relation = params.get("relation", "depends_on")
            require(source)
            require(target)
            if operation == "add_dependency":
                edge(source, target, relation, input_mapping=params.get("input_mapping", {}))
            else:
                graph["edges"] = [e for e in graph["edges"] if not (e["source"] == source and e["target"] == target and e.get("relation") == relation)]
            changed(target, "semantic" if relation in PROPAGATION else "layout")
        elif operation in {"fork_branch", "clone_subtree"}:
            origin = require()
            ids = _closure(graph, [origin["id"]]) if operation == "clone_subtree" or params.get("include_descendants") else {origin["id"]}
            ids = {i for i in ids if nodes[i]["branch_id"] == origin["branch_id"]}
            if params.get("node_ids"):
                ids = set(params["node_ids"])
                for nid in ids:
                    require(nid)
            destination = params.get("destination_branch_id")
            if destination:
                branch(destination)
                mapping = clone(ids, destination, params.get("copy_policy", {}).get("results", "reference"))
            else:
                destination = _uid()
                workspace = BranchWorkspace(self.project_dir, self.graph).fork(origin["id"], params.get("copy_policy"), branch_id=destination, dry_run=True)
                b = {"id": destination, "name": params.get("name", f"Fork of {origin['title']}"), "status": "active", "is_main": False,
                     "config": deepcopy(branch(origin["branch_id"]).get("config", {})), **{k: v for k, v in workspace.items() if k not in {"branch_id", "files"}}}
                graph["branches"].append(b)
                mapping = clone(ids, destination, params.get("copy_policy", {}).get("results", "reference"))
                b["root_node_id"] = mapping.get(origin["id"], next(iter(mapping.values())))
                files.extend({"action": "copy", "path": p, "branch_id": destination} for p in workspace["files"])
                actions.append({"action": "fork", "node_id": origin["id"], "branch_id": destination, "copy_policy": params.get("copy_policy")})
            edge(origin["id"], mapping.get(origin["id"], next(iter(mapping.values()))), "history")
            for old, new in mapping.items():
                nodes[new]["forked_from"] = old
        elif operation in {"insert_before", "insert_after"}:
            pivot = require()
            inserted_fields = params.get("node") or {k: v for k, v in params.items() if k in {"type", "title", "instructions", "config", "inputs", "position"}}
            inserted_fields.setdefault("title", "Inserted step")
            fresh = add(inserted_fields, pivot["branch_id"])
            before_insert = operation == "insert_before"
            rewired = [e for e in graph["edges"] if e.get("relation") in EXECUTION and e["target" if before_insert else "source"] == pivot["id"]]
            graph["edges"] = [e for e in graph["edges"] if e not in rewired]
            for old in rewired:
                edge(old["source"] if before_insert else fresh["id"], fresh["id"] if before_insert else old["target"], old.get("relation", "depends_on"), input_mapping=old.get("input_mapping", {}))
            edge(fresh["id"], pivot["id"]) if before_insert else edge(pivot["id"], fresh["id"])
            changed(pivot)
        elif operation == "reparent_subtree":
            pivot = require()
            parent = require(params.get("parent_id") or params.get("new_parent_id"))
            subtree = _closure(graph, [pivot["id"]])
            if parent["id"] in subtree:
                raise GraphError("dependency_cycle", "A subtree cannot be moved beneath one of its descendants.")
            previous = {e["source"] for e in graph["edges"] if e["target"] == pivot["id"] and e.get("relation") in EXECUTION}
            graph["edges"] = [e for e in graph["edges"] if not (e["target"] == pivot["id"] and e.get("relation") in EXECUTION)]
            edge(parent["id"], pivot["id"])
            input_map = params.get("input_map", {})
            for nid in subtree:
                node = require(nid)
                if params.get("move_to_branch"):
                    node["branch_id"] = parent["branch_id"]
                for ref in node.get("inputs", []):
                    if isinstance(ref, dict) and ref.get("node_id") in previous:
                        old = ref["node_id"]
                        if old in input_map:
                            replacement = require(input_map[old])
                            ref["node_id"] = input_map[old]
                            if "branch_id" in ref:
                                ref["branch_id"] = replacement["branch_id"]
                            ref.pop("missing", None)
                        else:
                            ref["needs_rebinding"] = True
                            missing.append({"node_id": nid, "reference": deepcopy(ref), "suggested_node_id": parent["id"]})
            changed(pivot, "semantic", "Execution parent changed; explicit input bindings require review")
        elif operation == "merge_branches":
            left = branch(params.get("left") or params.get("left_branch_id"))
            right = branch(params.get("right") or params.get("right_branch_id"))
            if left["id"] == right["id"]:
                raise GraphError("invalid_merge", "Choose two different branches.")
            bid = _uid()
            merge = BranchWorkspace(self.project_dir, self.graph).merge(left, right, params.get("resolution"), branch_id=bid, dry_run=True)
            conflicts.extend(merge["conflicts"])
            files.extend(merge["files"])
            if not conflicts:
                b = {"id": bid, "name": params.get("name", f"{left['name']} + {right['name']}"), "status": "active", "workspace": merge["workspace"],
                     "is_main": False, "config": merge["config"], "merged_from": [left["id"], right["id"]], "requires_revalidation": True}
                graph["branches"].append(b)
                ids = {n["id"] for n in graph["nodes"] if n["branch_id"] in {left["id"], right["id"]}}
                mapping = clone(ids, bid, "reference")
                b["root_node_id"] = mapping.get(left.get("root_node_id")) or next(iter(mapping.values()), None)
                for nid in mapping.values():
                    nodes[nid]["requires_revalidation"] = True
                for dep in params.get("dependencies", []):
                    edge(mapping.get(dep["source"], dep["source"]), mapping.get(dep["target"], dep["target"]))
                actions.append({"action": "merge", "left": left["id"], "right": right["id"], "resolution": params.get("resolution"), "branch_id": bid})
        elif operation == "split_node":
            original = require()
            parts = params.get("parts", [])
            if len(parts) < 2:
                raise GraphError("invalid_split", "Supply at least two editable steps.")
            previous = [e for e in graph["edges"] if e["target"] == original["id"] and e.get("relation") in EXECUTION]
            following = [e for e in graph["edges"] if e["source"] == original["id"] and e.get("relation") in EXECUTION]
            graph["edges"] = [e for e in graph["edges"] if e not in previous + following]
            fresh = [add(part, original["branch_id"]) for part in parts]
            fresh[0]["inputs"] = deepcopy(original.get("inputs", []))
            for old in previous:
                edge(old["source"], fresh[0]["id"])
            for a, b in zip(fresh, fresh[1:]):
                edge(a["id"], b["id"])
            for old in following:
                edge(fresh[-1]["id"], old["target"])
                consumer = require(old["target"])
                for ref in consumer.get("inputs", []):
                    if isinstance(ref, dict) and ref.get("node_id") == original["id"]:
                        ref["previous_node_id"] = original["id"]
                        ref["node_id"] = fresh[-1]["id"]
                        ref["needs_rebinding"] = True
                        missing.append({"node_id": consumer["id"], "reference": deepcopy(ref), "reason": "Confirm the final split step's output binding"})
            original["archived"] = True
            original["split_into"] = [n["id"] for n in fresh]
            for node in fresh:
                edge(original["id"], node["id"], "history")
            changed(original, "context", "Original result retained; split steps have not executed")
        elif operation == "group_nodes":
            if not targets:
                raise GraphError("empty_selection", "Select at least one node to group.")
            group_id = params.get("group_id") or _uid()
            for nid in targets:
                node = require(nid)
                node["group_id"] = group_id
                changed(node, "layout")
            graph.setdefault("groups", []).append({"id": group_id, "title": params.get("title", "Subworkflow"), "node_ids": list(targets), "collapsed": params.get("collapsed", False)})
        elif operation in {"prune_branch", "restore_branch", "set_main_branch"}:
            selected_branch = branch()
            if operation == "set_main_branch":
                for b in graph["branches"]:
                    b["is_main"] = b["id"] == selected_branch["id"]
                selected_branch["status"] = "active"
            else:
                selected_branch["status"] = "pruned" if operation == "prune_branch" else "active"
            for n in graph["nodes"]:
                if n["branch_id"] == selected_branch["id"]:
                    changed(n, "archive")
            if operation == "prune_branch":
                actions.append({"action": "stop_scheduling_branch", "branch_id": selected_branch["id"]})
        elif operation in {"undo", "redo"}:
            history = graph.setdefault("_history", {"undo": [], "redo": []})
            source, destination = ("undo", "redo") if operation == "undo" else ("redo", "undo")
            if not history.get(source):
                raise GraphError("history_empty", f"There is no edit to {operation}.")
            entry = history[source].pop()
            history.setdefault(destination, []).append(before)
            current_nodes = _index(graph)
            graph = deepcopy(entry)
            graph["_history"] = history
            changes = []
            ignored = RUNTIME_FIELDS | {"revision", "created_at", "updated_at", "edited_during_run", "context_stale", "needs_rerun", "results_current", "deliverable_status"}
            for n in graph["nodes"]:
                if n["id"] in current_nodes:
                    current = current_nodes[n["id"]]
                    fields = {key for key in set(n) | set(current) if key not in ignored and n.get(key) != current.get(key)}
                    category = self._category(current, {key: n.get(key) for key in fields}, fields) if fields else "display"
                    if fields:
                        changes.append({"id": n["id"], "category": category, "reason": "Restored editable content; external effects remain unchanged"})
                    for field in RUNTIME_FIELDS:
                        if field in current:
                            n[field] = deepcopy(current[field])
                    n["revision"] = current.get("revision", 0) if category in {"layout", "display", "archive"} else max(n.get("revision", 0), current.get("revision", 0)) + 1
            for nid in set(_index(graph)) ^ set(current_nodes):
                changes.append({"id": nid, "category": "semantic", "reason": "Restored graph structure; files and run records are retained"})
            previous_edges = {(e["source"], e["target"], e.get("relation", "depends_on")) for e in before["edges"]}
            restored_edges = {(e["source"], e["target"], e.get("relation", "depends_on")) for e in graph["edges"]}
            for source, target, relation in previous_edges ^ restored_edges:
                changes.append({"id": target, "category": "semantic" if relation in PROPAGATION else "layout", "reason": "Restored edge relationship"})
        validate_graph(graph)
        impact = ImpactAnalyzer(graph).resolve(changes)
        impact.update(files=files, conflicts=conflicts, input_bindings_required=missing, actions=[a for a in actions if a["action"] not in {"fork", "merge"}],
                      undo_scope="Graph structure and editable content only; existing files, runs, and external effects are retained.")
        if conflicts and not dry_run:
            raise GraphError("merge_conflict", "Resolve the listed conflicts before applying this merge.", status_code=409, detail={"impact": impact, "conflicts": conflicts})
        for node in graph["nodes"]:
            if node["id"] in impact["affected_nodes"]:
                node["context_stale"] = True
                if node["id"] in impact["rerun_nodes"] or node["id"] in impact["refresh_nodes"]:
                    if node.get("outputs"):
                        node["deliverable_status"] = "needs_update"
                        node["results_current"] = False
                    node["needs_rerun"] = node["id"] in impact["rerun_nodes"]
        if not dry_run:
            # Every structural and DAG check precedes the first filesystem write.
            workspaces = BranchWorkspace(self.project_dir, self.graph)
            for action in actions:
                if action["action"] == "fork":
                    workspaces.fork(action["node_id"], action.get("copy_policy"), branch_id=action["branch_id"])
                elif action["action"] == "merge":
                    workspaces.merge(action["left"], action["right"], action.get("resolution"), branch_id=action["branch_id"])
            if operation not in {"undo", "redo"}:
                history = graph.setdefault("_history", {"undo": [], "redo": []})
                history.setdefault("undo", []).append(before)
                limit = graph.get("history_limit")
                if limit is not None:
                    history["undo"] = history["undo"][-int(limit):] if int(limit)>0 else []
                history["redo"] = []
        graph["revision"] = self.graph["revision"] + 1
        executable_edits = [c for c in changes if c.get("category") not in {"layout", "display", "goal", "context", "archive"}]
        execution_impact = ImpactAnalyzer(graph).resolve(executable_edits)
        eligible = set(execution_impact["rerun_nodes"]) | set(execution_impact["refresh_nodes"])
        run_nodes = topological_order(graph, eligible) if command.get("run") else []
        return {"revision": graph["revision"], "graph": graph, "impact": impact, "run_nodes": run_nodes, "run_ids": []}

    @staticmethod
    def _snapshot(graph):
        return deepcopy({k: v for k, v in graph.items() if k != "_history"})

    @staticmethod
    def _category(node, patch, fields):
        if fields <= VISUAL_FIELDS:
            return "layout"
        if fields <= DISPLAY_FIELDS | VISUAL_FIELDS:
            return "display"
        if node.get("type") in {"goal", "research_goal"}:
            return "goal"
        if fields <= {"context_overrides"}:
            return "context"
        if fields <= {"config"}:
            keys = set(patch.get("config", {}))
            if keys <= {"style", "color", "font_size", "layout", "caption"}:
                return "style"
            if keys <= {"aggregation", "statistics", "filters", "confidence_interval"}:
                return "statistics"
        if node.get("type") in PAPER_TYPES and fields <= {"instructions", "content", "source", "config"}:
            return "wording"
        return "semantic"

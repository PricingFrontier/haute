"""Structural contracts for the ENG-CX09 service decompositions."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

ROOT = Path(__file__).parents[1]

GIT_MODULES = (
    "haute._git_core",
    "haute._git_setup",
    "haute._git_transactions",
    "haute._git_read_models",
    "haute._git_history",
    "haute._git_remote",
    "haute._git_archive",
)
TRAINING_LEAF_MODULES = (
    "haute.routes._training_preparation",
    "haute.routes._training_evaluation",
    "haute.routes._training_worker",
    "haute.routes._training_artifacts",
)
TRAINING_MODULES = (*TRAINING_LEAF_MODULES, "haute.routes._training_lifecycle")
GIT_SERIALIZED_MUTATORS = {
    "haute._git_setup": {
        "resolve_ledger",
        "set_identity",
        "set_prefs",
        "set_working_branch",
    },
    "haute._git_transactions": {
        "commit_milestone",
        "commit_save",
        "create_working_branch",
        "merge_to_working",
    },
    "haute._git_remote": {
        "branch_away",
        "bundle_create",
        "fast_forward_pair",
        "fast_forward_pair_from_tracking",
        "fetch_pair",
        "push_working_pair",
    },
    "haute._git_archive": {
        "archive_working_pair",
        "delete_working_pair",
        "move_to_commit",
        "restore_working_pair",
        "undelete_working_pair",
    },
}


def _path_for(module_name: str) -> Path:
    return ROOT / "src" / Path(*module_name.split(".")).with_suffix(".py")


def _tree(module_name: str) -> ast.Module:
    return ast.parse(_path_for(module_name).read_text(encoding="utf-8"))


def _internal_imports(module_name: str, candidates: set[str]) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(_tree(module_name)):
        if isinstance(node, ast.ImportFrom) and node.module in candidates:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names if alias.name in candidates)
    return imports


def _imports_module(module_name: str, imported_module: str) -> bool:
    for node in ast.walk(_tree(module_name)):
        if isinstance(node, ast.Import) and any(
            alias.name == imported_module for alias in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == imported_module:
            return True
    return False


def _top_level_definitions(module_name: str) -> set[str]:
    return {
        node.name
        for node in _tree(module_name).body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }


def _has_star_import(module_name: str) -> bool:
    return any(
        isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
        for node in ast.walk(_tree(module_name))
    )


def _mutates_dynamic_globals(module_name: str) -> bool:
    return any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "globals"
        for node in ast.walk(_tree(module_name))
    )


def _assert_single_implementation_owner(modules: tuple[str, ...]) -> None:
    owners: dict[str, list[str]] = {}
    for module in modules:
        for name in _top_level_definitions(module):
            owners.setdefault(name, []).append(module)
    duplicates = {name: found for name, found in owners.items() if len(found) > 1}
    assert not duplicates


def _assert_acyclic(modules: tuple[str, ...]) -> None:
    candidates = set(modules)
    edges = {module: _internal_imports(module, candidates) for module in modules}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str) -> None:
        if module in visiting:
            raise AssertionError(f"circular domain import through {module}: {edges}")
        if module in visited:
            return
        visiting.add(module)
        for dependency in edges[module]:
            visit(dependency)
        visiting.remove(module)
        visited.add(module)

    for module in modules:
        visit(module)


def test_git_facade_is_declaration_only_and_public_imports_stay_stable() -> None:
    facade_tree = _tree("haute._git")
    assert not any(
        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        for node in facade_tree.body
    )

    facade = importlib.import_module("haute._git")
    expected = {
        "GitDomainError",
        "GitError",
        "GitGuardrailError",
        "GitHistoryReadError",
        "GitMilestoneForkError",
        "GitPrefs",
        "GitPushRejectedError",
        "GitTransactionError",
        "adopt_cloned_lineage",
        "archive_commit",
        "archive_working_pair",
        "branch_away",
        "bundle_create",
        "bundle_verify",
        "clone_project",
        "commit_context",
        "commit_exists",
        "commit_milestone",
        "commit_save",
        "create_working_branch",
        "delete_working_pair",
        "ensure_remote",
        "fast_forward_pair",
        "fast_forward_pair_from_tracking",
        "fetch_bundle_refs",
        "get_identity",
        "get_prefs",
        "graph_topology",
        "list_remotes",
        "milestone_saves",
        "move_to_commit",
        "pair_divergence",
        "pending_ledger_saves",
        "push_working_pair",
        "remote_has_content",
        "remote_url",
        "restore_working_pair",
        "_run_git",
        "set_identity",
        "set_prefs",
        "set_working_branch",
        "undelete_working_pair",
        "working_branch_status",
        "working_branches",
        "working_milestones",
    }
    assert not sorted(name for name in expected if not hasattr(facade, name))
    assert facade.GitError.__module__ == "haute._git_core"
    assert facade.set_working_branch.__module__ == "haute._git_setup"
    assert facade.commit_save.__module__ == "haute._git_transactions"
    assert facade.working_branch_status.__module__ == "haute._git_read_models"
    assert facade.graph_topology.__module__ == "haute._git_history"
    assert facade.push_working_pair.__module__ == "haute._git_remote"
    assert facade.archive_working_pair.__module__ == "haute._git_archive"


def test_git_domain_imports_are_acyclic_and_only_core_owns_subprocesses() -> None:
    _assert_acyclic(GIT_MODULES)
    _assert_single_implementation_owner(GIT_MODULES)
    for module_name in ("haute._git", *GIT_MODULES):
        assert not _has_star_import(module_name)
        assert not _mutates_dynamic_globals(module_name)
    assert not _internal_imports("haute._git_core", set(GIT_MODULES) - {"haute._git_core"})
    for module_name in GIT_MODULES:
        if module_name == "haute._git_core":
            assert _imports_module(module_name, "subprocess")
        else:
            assert not _imports_module(module_name, "subprocess")
            assert "haute._git" not in _internal_imports(module_name, {"haute._git"})


def test_git_mutators_preserve_the_shared_repository_lock_boundary() -> None:
    for module_name, expected_names in GIT_SERIALIZED_MUTATORS.items():
        functions = {
            node.name: node
            for node in _tree(module_name).body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        assert expected_names <= functions.keys()
        for name in expected_names:
            decorators = functions[name].decorator_list
            assert any(
                isinstance(decorator, ast.Name) and decorator.id == "_serialized_mutation"
                for decorator in decorators
            ), f"{module_name}.{name} lost the repository mutation lock"


def test_training_facade_is_declaration_only_and_helper_imports_stay_stable() -> None:
    facade_tree = _tree("haute.routes._train_service")
    assert not any(
        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        for node in facade_tree.body
    )

    facade = importlib.import_module("haute.routes._train_service")
    expected = {
        "TrainService",
        "TrainingArtifactPublicationError",
        "_VramCheck",
        "_assert_json_finite",
        "_check_gpu_vram",
        "_clamp_row_limit",
        "_default_train_timeout",
        "_evaluation_preview_payload",
        "_find_modelling_node",
        "_friendly_error",
        "_known_training_worker_failure",
        "_publish_training_artifacts",
        "_run_dispersion_process_job",
        "_run_training_process_job",
        "_seeded_training_sample",
        "_training_required_columns_by_node",
        "_validate_glm_family_link",
        "_worker_failure_payload",
    }
    assert not sorted(name for name in expected if not hasattr(facade, name))
    assert facade.TrainService.__module__ == "haute.routes._training_lifecycle"
    assert facade._seeded_training_sample.__module__ == "haute.routes._training_preparation"
    assert facade._check_gpu_vram.__module__ == "haute.routes._training_preparation"
    assert facade._training_required_columns_by_node.__module__ == (
        "haute.routes._training_preparation"
    )
    assert facade._validate_glm_family_link.__module__ == ("haute.routes._training_evaluation")
    assert facade._evaluation_preview_payload.__module__ == ("haute.routes._training_evaluation")
    assert facade._run_training_process_job.__module__ == "haute.routes._training_worker"
    assert facade._publish_training_artifacts.__module__ == "haute.routes._training_artifacts"
    assert facade.TrainingArtifactPublicationError.__module__ == (
        "haute.routes._training_artifacts"
    )


def test_training_domain_imports_are_acyclic_and_leaves_do_not_own_job_state() -> None:
    _assert_acyclic(TRAINING_MODULES)
    _assert_single_implementation_owner(TRAINING_MODULES)
    for module_name in ("haute.routes._train_service", *TRAINING_MODULES):
        assert not _has_star_import(module_name)
        assert not _mutates_dynamic_globals(module_name)
    for module_name in TRAINING_LEAF_MODULES:
        forbidden = {
            "haute.routes._train_service",
            "haute.routes._training_lifecycle",
        }
        assert not _internal_imports(module_name, forbidden)
        assert not _imports_module(module_name, "haute.routes._job_lifecycle")
        assert not _imports_module(module_name, "haute.routes._job_store")
        tree = _tree(module_name)
        assert not any(
            isinstance(node, ast.ClassDef) and node.name == "TrainService" for node in tree.body
        )

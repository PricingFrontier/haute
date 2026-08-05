"""Structural backstops: secrets must never reach the API surface.

Two checks, neither of which pins API shape (see
specs/hosted-databricks-app/high-level.md §Secret-surface backstop):

1. Every response-model schema reachable from a registered route is
   walked recursively; any field whose NAME looks secret-bearing fails
   unless allowlisted here with a justification. New routes and models
   are covered automatically.
2. An AST scan of ``src/haute`` for string literals naming
   secret-bearing environment variables; each (file, name) reference
   must be reviewed and allowlisted here. A new module touching a
   secret env name fails until a human confirms it does not surface the
   value.

These guard NAMES, not values — the cheap structural end of the defence.
Value handling is covered by the env-accessor inventory
(test_env_lazy_accessors) and the local-security tests.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

_SRC = Path(__file__).parent.parent / "src" / "haute"

# ---------------------------------------------------------------------------
# 1. Response-model field names
# ---------------------------------------------------------------------------

_ALWAYS_SECRET_RE = re.compile(r"secret|password|credential|private_key|api_?key", re.IGNORECASE)


def _name_looks_secret(name: str) -> bool:
    lowered = name.lower()
    if _ALWAYS_SECRET_RE.search(lowered):
        return True
    # "token" is secret-shaped except in LLM token-count fields
    # (max_output_tokens, total_tokens, …).
    return "token" in lowered and not lowered.endswith("tokens")


# (component schema name or inline path context, field name) — every entry
# needs a justification for why the field is not a secret.
_REVIEWED_RESPONSE_FIELDS: set[tuple[str, str]] = set()


def _walk_schema(
    schema: Any,
    context: str,
    components: dict[str, Any],
    findings: set[tuple[str, str]],
    seen_refs: set[str],
) -> None:
    if not isinstance(schema, dict):
        return
    ref = schema.get("$ref")
    if isinstance(ref, str):
        name = ref.rsplit("/", 1)[-1]
        if name in seen_refs:
            return
        seen_refs.add(name)
        _walk_schema(components.get(name, {}), name, components, findings, seen_refs)
        return
    for prop_name, prop_schema in (schema.get("properties") or {}).items():
        if _name_looks_secret(prop_name):
            findings.add((context, prop_name))
        _walk_schema(prop_schema, context, components, findings, seen_refs)
    for key in ("items", "additionalProperties"):
        _walk_schema(schema.get(key), context, components, findings, seen_refs)
    for key in ("anyOf", "oneOf", "allOf"):
        for member in schema.get(key) or []:
            _walk_schema(member, context, components, findings, seen_refs)


def test_no_secret_shaped_fields_in_any_response_model() -> None:
    from haute.server import app

    spec = app.openapi()
    components = (spec.get("components") or {}).get("schemas") or {}
    findings: set[tuple[str, str]] = set()
    seen_refs: set[str] = set()

    for path, methods in (spec.get("paths") or {}).items():
        for method, operation in methods.items():
            if not isinstance(operation, dict):
                continue
            for status, response in (operation.get("responses") or {}).items():
                for media in (response.get("content") or {}).values():
                    _walk_schema(
                        media.get("schema"),
                        f"{method.upper()} {path} -> {status}",
                        components,
                        findings,
                        seen_refs,
                    )

    unexpected = findings - _REVIEWED_RESPONSE_FIELDS
    assert not unexpected, (
        "Secret-shaped field names on the API response surface (add to "
        f"_REVIEWED_RESPONSE_FIELDS only with a justification): {sorted(unexpected)}"
    )
    stale = _REVIEWED_RESPONSE_FIELDS - findings
    assert not stale, f"Stale allowlist entries (field no longer exists): {sorted(stale)}"


# ---------------------------------------------------------------------------
# 2. Secret env-name references in src/haute
# ---------------------------------------------------------------------------

_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Reviewed references. Every entry asserts: the module reads/writes/mentions
# the NAME but never surfaces the VALUE through the API or logs.
_REVIEWED_SECRET_ENV_REFERENCES: set[tuple[str, str]] = {
    # Credential resolution seams (values flow only into client connections).
    ("_databricks_io.py", "DATABRICKS_TOKEN"),
    ("_databricks_io.py", "DATABRICKS_CLIENT_SECRET"),
    ("_input_providers.py", "DATABRICKS_TOKEN"),
    ("modelling/_mlflow_log.py", "DATABRICKS_TOKEN"),
    ("deploy/_mlflow.py", "DATABRICKS_RATING_TOKEN"),
    ("assistant/_config.py", "ANTHROPIC_API_KEY"),
    ("assistant/_config.py", "OPENAI_API_KEY"),
    # Same provider→env-var-NAME table as the two above; the value is read only
    # to construct the provider client.
    ("assistant/_config.py", "DATABRICKS_TOKEN"),
    # Redaction denylist: the assistant reads these values precisely to scrub
    # them out of message content before it leaves the process.
    ("assistant/_session.py", "ANTHROPIC_API_KEY"),
    ("assistant/_session.py", "OPENAI_API_KEY"),
    ("assistant/_session.py", "DATABRICKS_TOKEN"),
    # Hosted durable storage: the value reaches git ONLY through a generated
    # GIT_ASKPASS helper that reads it from the environment at call time — it is
    # never written into the helper file, a git config, a URL, or a log line
    # (pinned by tests/test_project_storage.py::TestCredentialHandling).
    ("_project_storage.py", "HAUTE_GIT_TOKEN"),
    # Local session-token machinery (owns the token by design).
    ("_local_security.py", "HAUTE_LOCAL_SESSION_TOKEN"),
    ("cli/_serve.py", "VITE_HAUTE_SESSION_TOKEN"),
    # Routes that report whether credentials are CONFIGURED (boolean), by name.
    ("routes/databricks.py", "DATABRICKS_TOKEN"),
    ("routes/databricks.py", "DATABRICKS_CLIENT_SECRET"),
    ("routes/input_cache.py", "DATABRICKS_TOKEN"),
    # Redaction denylist: the value is consulted precisely to scrub it.
    ("routes/input_cache.py", "DATABRICKS_CLIENT_SECRET"),
    # Scaffold templates: CI secret PLACEHOLDER names written into generated
    # workflow files, never values.
    ("_scaffold.py", "DATABRICKS_RATING_TOKEN"),
    ("_scaffold.py", "DOCKER_PASSWORD"),
    ("_scaffold.py", "AWS_SECRET_ACCESS_KEY"),
    ("_scaffold.py", "AZURE_CLIENT_SECRET"),
}


def _is_secret_env_name(value: str) -> bool:
    if not _ENV_NAME_RE.fullmatch(value):
        return False
    return _name_looks_secret(value)


def test_secret_env_name_references_are_reviewed() -> None:
    findings: set[tuple[str, str]] = set()
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = path.relative_to(_SRC).as_posix()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _is_secret_env_name(node.value)
            ):
                findings.add((relative, node.value))

    unexpected = findings - _REVIEWED_SECRET_ENV_REFERENCES
    assert not unexpected, (
        "Unreviewed secret env-name references in src/haute (add to "
        "_REVIEWED_SECRET_ENV_REFERENCES only after confirming the value "
        f"is never surfaced): {sorted(unexpected)}"
    )
    stale = _REVIEWED_SECRET_ENV_REFERENCES - findings
    assert not stale, f"Stale allowlist entries: {sorted(stale)}"

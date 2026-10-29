"""The Files panels' routes: /api/project-files (specs/server-api, Project files)."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from haute._git_state import write_working_branch
from haute._sandbox import set_project_root
from tests._git_helpers import git_run, init_repo

WORKING = "pricing-dev"


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git project on a working branch, its pipeline in rating/ and the guard's .gitignore."""
    root = tmp_path / "project"
    root.mkdir()
    init_repo(root)
    (root / "haute.toml").write_text('[project]\npipeline = "rating/main.py"\n')
    (root / ".gitignore").write_text("/data/\n/outputs/\n__pycache__/\n")
    (root / "rating").mkdir()
    (root / "rating" / "main.py").write_text("# pipeline\n")
    git_run(root, "add", ".")
    git_run(root, "commit", "-m", "project")
    git_run(root, "checkout", "-b", WORKING)
    write_working_branch(root, WORKING)
    monkeypatch.chdir(root)
    set_project_root(root)
    return root


@pytest.fixture()
def client(project: Path) -> TestClient:
    from haute.server import app

    return TestClient(app, raise_server_exceptions=False)


def _put(client: TestClient, place: str, path: str, body: bytes, **params: str):
    return client.put(
        "/api/project-files/content",
        params={"place": place, "path": path, **params},
        content=body,
        headers={"content-type": "application/octet-stream"},
    )


def _list(client: TestClient, place: str, path: str = "") -> dict:
    response = client.get("/api/project-files", params={"place": place, "path": path})
    assert response.status_code == 200, response.text
    return response.json()


def _entries(listing: dict) -> dict[str, dict]:
    return {entry["name"]: entry for entry in listing["entries"]}


def _refused(response) -> str:
    """The refusal's message: a plain-string detail, the flat error contract (#76)."""
    detail = response.json()["detail"]
    assert isinstance(detail, str) and detail
    return detail


def _ledger_files(project: Path) -> list[str]:
    return git_run(project, "show", "--name-only", "--format=", f"{WORKING}-save").splitlines()


def _committed_message(project: Path) -> str:
    return git_run(project, "log", "-1", "--format=%s", f"{WORKING}-save")


# ── listing ────────────────────────────────────────────────────────────────


def test_the_pipeline_place_lists_its_known_folders_before_they_exist(
    client: TestClient,
) -> None:
    listing = _list(client, "pipeline")

    assert listing["root"] == "rating/files"
    assert listing["writable"] is True
    assert [(e["name"], e["kind"]) for e in listing["entries"]] == [
        ("data", "folder"),
        ("models", "folder"),
        ("params", "folder"),
    ]


def test_the_local_place_lists_exactly_data_and_outputs(client: TestClient, project: Path) -> None:
    (project / "data").mkdir()
    (project / "notes.txt").write_text("not a local folder")

    listing = _list(client, "local")

    assert listing["root"] == ""
    assert listing["writable"] is False
    assert [e["name"] for e in listing["entries"]] == ["data", "outputs"]
    assert _list(client, "local", "data")["writable"] is True
    assert _list(client, "local", "outputs")["writable"] is False


def test_a_listing_hides_dot_names_and_bytecode_and_sorts_folders_first(
    client: TestClient, project: Path
) -> None:
    data = project / "rating" / "files" / "data"
    (data / "regions").mkdir(parents=True)
    (data / "b.csv").write_bytes(b"b\n")
    (data / "A.csv").write_bytes(b"a\n")
    (data / ".secret").write_text("hidden\n")
    (data / "__pycache__").mkdir()

    listing = _list(client, "pipeline", "data")

    assert [e["name"] for e in listing["entries"]] == ["regions", "A.csv", "b.csv"]
    entry = _entries(listing)["b.csv"]
    assert entry["path"] == "data/b.csv"
    assert entry["size"] == 2
    assert entry["revision"]


def test_a_pipeline_file_not_yet_in_history_is_new_and_an_ignored_one_says_so(
    client: TestClient, project: Path
) -> None:
    data = project / "rating" / "files" / "data"
    data.mkdir(parents=True)
    (data / "copied.csv").write_text("a\n")
    (data / "scratch.tmp").write_text("t\n")
    (project / ".gitignore").write_text("/data/\n*.tmp\n")

    entries = _entries(_list(client, "pipeline", "data"))

    assert entries["copied.csv"]["status"] == "new"
    assert entries["scratch.tmp"]["status"] == "ignored"


def test_a_pipeline_folder_gitignore_ignores_takes_no_files(
    client: TestClient, project: Path
) -> None:
    (project / "rating" / "files" / "data").mkdir(parents=True)
    (project / "rating" / "files" / "data" / "kept.csv").write_bytes(b"a\n")
    (project / ".gitignore").write_text("data/\n")  # unanchored: ignores files/data/ too

    listing = _list(client, "pipeline", "data")

    assert listing["writable"] is False
    assert _entries(listing)["kept.csv"]["status"] == "ignored"
    assert _list(client, "pipeline", "models")["writable"] is True


def test_a_staged_file_is_new_and_an_edited_one_changed_until_committed(
    client: TestClient, project: Path
) -> None:
    """Only a file committed as it is has no marker: the panel promises recovery for it alone."""
    data = project / "rating" / "files" / "data"
    data.mkdir(parents=True)
    for name in ("committed.csv", "edited.csv"):
        (data / name).write_bytes(b"v1\n")
    git_run(project, "add", "rating/files/data")
    git_run(project, "commit", "-m", "two files")
    (data / "edited.csv").write_bytes(b"v2\n")
    (data / "staged.csv").write_bytes(b"s\n")
    git_run(project, "add", "rating/files/data/staged.csv")

    entries = _entries(_list(client, "pipeline", "data"))

    assert entries["committed.csv"]["status"] is None
    assert entries["edited.csv"]["status"] == "changed"
    assert entries["staged.csv"]["status"] == "new"


def test_a_pipeline_at_the_project_root_has_no_pipeline_place(
    client: TestClient, project: Path
) -> None:
    (project / "haute.toml").write_text('[project]\npipeline = "main.py"\n')

    response = client.get("/api/project-files", params={"place": "pipeline"})

    assert response.status_code == 409
    assert "has no pipeline folder" in _refused(response)


def test_pipeline_files_refuse_without_git_naming_what_is_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("haute.routes.project_files.git_binary_available", lambda: False)

    listing = client.get("/api/project-files", params={"place": "pipeline"})
    written = _put(client, "pipeline", "data/rates.csv", b"a,b\n")

    for refused in (listing, written):
        assert refused.status_code == 409
        assert "Git is not available in this environment" in _refused(refused)
    # Local files need no git.
    assert client.get("/api/project-files", params={"place": "local"}).status_code == 200


def test_pipeline_files_refuse_outside_a_repository(client: TestClient, project: Path) -> None:
    shutil.rmtree(project / ".git", onerror=_writable)

    listing = client.get("/api/project-files", params={"place": "pipeline"})

    assert listing.status_code == 409
    assert "has no git repository: run git init" in _refused(listing)
    assert client.get("/api/project-files", params={"place": "local"}).status_code == 200


def _writable(remove: Any, path: str, _: Any) -> None:
    """Git's read-only object files, made writable so the repository can be removed."""
    Path(path).chmod(stat.S_IWRITE)
    remove(path)


def test_a_folder_that_is_not_there_is_404(client: TestClient) -> None:
    response = client.get("/api/project-files", params={"place": "pipeline", "path": "nope"})

    assert response.status_code == 404
    assert "doesn't exist" in _refused(response)


# ── writing ────────────────────────────────────────────────────────────────


def test_a_pipeline_file_is_written_whole_and_saved_into_version_history(
    client: TestClient, project: Path
) -> None:
    response = _put(client, "pipeline", "data/postcodes.csv", b"postcode,region\n")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["path"] == "data/postcodes.csv"
    assert body["size"] == 16
    assert body["replaced"] is False
    assert body["git_sha"] and body["status"] is None
    assert (project / "rating/files/data/postcodes.csv").read_bytes() == b"postcode,region\n"
    assert _ledger_files(project) == ["rating/files/data/postcodes.csv"]
    assert _committed_message(project) == "Added rating/files/data/postcodes.csv"
    # No staging file is left beside it.
    assert os.listdir(project / "rating/files/data") == ["postcodes.csv"]


def test_a_name_with_glob_characters_is_saved_alone(client: TestClient, project: Path) -> None:
    data = project / "rating" / "files" / "data"
    data.mkdir(parents=True)
    (data / "a.csv").write_text("not part of this save\n")

    response = _put(client, "pipeline", "data/[ab].csv", b"literal\n")

    assert response.status_code == 200, response.text
    assert _ledger_files(project) == ["rating/files/data/[ab].csv"]


def test_an_existing_file_is_replaced_only_at_the_revision_confirmed(
    client: TestClient, project: Path
) -> None:
    first = _put(client, "pipeline", "models/freq.cbm", b"v1").json()

    exists = _put(client, "pipeline", "models/freq.cbm", b"v2")
    assert exists.status_code == 409 and "already exists" in _refused(exists)

    stale = _put(client, "pipeline", "models/freq.cbm", b"v2", replace_revision="0-0")
    assert stale.status_code == 412 and "changed since you chose" in _refused(stale)

    replaced = _put(
        client, "pipeline", "models/freq.cbm", b"v2", replace_revision=first["revision"]
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["replaced"] is True
    assert (project / "rating/files/models/freq.cbm").read_bytes() == b"v2"
    assert _committed_message(project) == "Replaced rating/files/models/freq.cbm"


def test_a_local_file_is_written_but_never_versioned(client: TestClient, project: Path) -> None:
    response = _put(client, "local", "data/portfolio.parquet", b"rows")

    assert response.status_code == 200, response.text
    assert response.json()["git_sha"] is None
    assert (project / "data" / "portfolio.parquet").read_bytes() == b"rows"
    assert git_run(project, "rev-parse", "--verify", "-q", f"{WORKING}-save", check=False) == ""


@pytest.mark.parametrize(
    ("place", "path", "status", "reason"),
    [
        ("pipeline", "../escape.csv", 400, "isn't a path the Files panels can use"),
        ("pipeline", "data/.env", 400, "isn't a path the Files panels can use"),
        ("pipeline", "data/CON.csv", 400, "isn't a name a project file can have"),
        ("pipeline", "data/a:b.csv", 400, "isn't a name a project file can have"),
        ("pipeline", "/etc/passwd", 400, "isn't a path the Files panels can use"),
        ("local", "outputs/scored.parquet", 403, "only download from"),
        ("local", "notes.txt", 404, "Local files holds only data and outputs"),
        ("local", "data", 400, "names a folder, not a file"),
    ],
)
def test_a_write_outside_an_ordinary_writable_name_is_refused(
    client: TestClient, project: Path, place: str, path: str, status: int, reason: str
) -> None:
    response = _put(client, place, path, b"x")

    assert response.status_code == status, response.text
    assert reason in _refused(response)


def test_a_pipeline_file_over_the_limit_is_refused_before_it_is_read(
    client: TestClient, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("haute.routes.project_files.VERSIONED_FILE_LIMIT_BYTES", 4)

    response = _put(client, "pipeline", "data/big.csv", b"12345")

    assert response.status_code == 413
    assert "version history takes files up to" in _refused(response)
    assert not (project / "rating/files/data/big.csv").exists()


def test_a_pipeline_file_gitignore_would_ignore_is_refused_naming_the_rule(
    client: TestClient, project: Path
) -> None:
    (project / ".gitignore").write_text("data/\n")  # unanchored: also ignores files/data/

    response = _put(client, "pipeline", "data/postcodes.csv", b"x")

    assert response.status_code == 409
    assert "would be ignored by .gitignore (.gitignore:1:data/)" in _refused(response)


def test_a_hosted_app_refuses_local_files(
    client: TestClient, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("haute.routes.project_files.databricks_app_environment", lambda: object())

    response = _put(client, "local", "data/portfolio.parquet", b"rows")

    assert response.status_code == 403
    assert "This hosted app keeps only what version history keeps" in _refused(response)
    assert _put(client, "pipeline", "data/table.csv", b"a\n").status_code == 200


def test_a_branch_change_while_uploading_writes_nothing(
    client: TestClient, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    branches = iter([WORKING, "another-branch"])
    monkeypatch.setattr(
        "haute.routes.project_files.read_working_branch", lambda _root: next(branches)
    )

    response = _put(client, "pipeline", "data/table.csv", b"a\n")

    assert response.status_code == 409
    assert "The branch changed while the file was uploading" in _refused(response)
    assert not (project / "rating/files/data/table.csv").exists()
    assert os.listdir(project / "rating/files/data") == []


def test_a_body_shorter_than_its_length_leaves_nothing_behind(
    client: TestClient, project: Path
) -> None:
    (project / "data").mkdir()
    response = client.put(
        "/api/project-files/content",
        params={"place": "local", "path": "data/cut.csv"},
        content=b"abc",
        headers={"content-length": "10"},
    )

    assert response.status_code == 400
    assert "ended before it was complete" in _refused(response)
    assert os.listdir(project / "data") == []
    assert os.listdir(project / ".haute" / "uploads") == []


def test_an_upload_stages_outside_the_pipeline_folder(
    client: TestClient, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Commit while a file streams must find no partial file in rating/ to version."""
    from haute._file_ops import publish_staged_file

    staged_at: list[Path] = []

    def publish_spy(staged: Path, target: Path, *, replace: bool) -> None:
        staged_at.append(staged)
        # The moment before publishing: the bytes are all there, none of them in rating/.
        assert [
            p for p in (project / "rating").rglob("*") if p.is_file() and p.name != "main.py"
        ] == []
        publish_staged_file(staged, target, replace=replace)

    monkeypatch.setattr("haute.routes.project_files.publish_staged_file", publish_spy)

    assert _put(client, "pipeline", "data/table.csv", b"a\n").status_code == 200
    assert staged_at[0].parent == (project / ".haute" / "uploads").resolve()
    assert list((project / ".haute" / "uploads").iterdir()) == []


def test_a_write_through_a_junction_is_refused(
    client: TestClient, project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside")
    target = outside / "elsewhere"
    target.mkdir()
    link = project / "data"
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)

    response = _put(client, "local", "data/escape.csv", b"x")

    assert response.status_code == 400
    assert "is reached through a link or junction" in _refused(response)
    assert list(target.iterdir()) == []


def test_a_staging_folder_reached_through_a_junction_is_refused(
    client: TestClient, project: Path
) -> None:
    """A link at .haute/uploads could lead a partial upload back into rating/ for a Commit."""
    inside = project / "rating" / "files" / "data"
    inside.mkdir(parents=True)
    (project / ".haute").mkdir(exist_ok=True)
    link = project / ".haute" / "uploads"
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(inside), str(link))
    else:
        os.symlink(inside, link, target_is_directory=True)

    response = _put(client, "local", "data/portfolio.parquet", b"rows")

    assert response.status_code == 409
    assert "Uploads are staged in the project's .haute/uploads/ folder" in _refused(response)
    assert list(inside.iterdir()) == []


def test_a_write_needs_the_session_token_and_a_trusted_origin(client: TestClient) -> None:
    no_cookie = client.put(
        "/api/project-files/content",
        params={"place": "pipeline", "path": "data/x.csv"},
        content=b"x",
        headers={"cookie": ""},
    )
    foreign = client.put(
        "/api/project-files/content",
        params={"place": "pipeline", "path": "data/x.csv"},
        content=b"x",
        headers={"origin": "https://elsewhere.example"},
    )

    assert no_cookie.status_code == 403
    assert foreign.status_code == 403


# ── a capture taken later ─────────────────────────────────────────────────


def test_files_left_uncaptured_are_captured_once_an_identity_is_set(
    client: TestClient, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute import _git

    kept = _put(client, "pipeline", "data/old.csv", b"old\n")
    assert kept.status_code == 200, kept.text
    tip = git_run(project, "rev-parse", f"{WORKING}-save")

    # No commit identity (a restored hosted container): an upload and a delete land, and
    # each names the file its skipped capture left off the ledger.
    identity = _git.get_identity
    monkeypatch.setattr(_git, "get_identity", lambda cwd=None: (None, None))
    added = _put(client, "pipeline", "data/rates.csv", b"rate\n1\n")
    removed = client.delete(
        "/api/project-files/content",
        params={"place": "pipeline", "path": "data/old.csv", "revision": kept.json()["revision"]},
    )
    assert added.status_code == 200, added.text
    assert removed.status_code == 200, removed.text
    assert added.json()["identity_required"] is True
    assert added.json()["uncaptured"] == ["rating/files/data/rates.csv"]
    assert removed.json()["uncaptured"] == ["rating/files/data/old.csv"]
    assert git_run(project, "rev-parse", f"{WORKING}-save") == tip
    monkeypatch.setattr(_git, "get_identity", identity)

    captured = client.post(
        "/api/git/capture",
        json={"paths": added.json()["uncaptured"] + removed.json()["uncaptured"]},
    )

    assert captured.status_code == 200, captured.text
    assert captured.json()["git_sha"] and captured.json()["uncaptured"] == []
    assert sorted(_ledger_files(project)) == [
        "rating/files/data/old.csv",
        "rating/files/data/rates.csv",
    ]
    assert (
        "rating/files/data/old.csv"
        not in git_run(project, "ls-tree", "-r", "--name-only", f"{WORKING}-save").splitlines()
    )


def test_a_capture_still_skipped_names_the_files_again(
    client: TestClient, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute import _git

    monkeypatch.setattr(_git, "get_identity", lambda cwd=None: (None, None))
    added = _put(client, "pipeline", "data/rates.csv", b"rate\n")

    again = client.post("/api/git/capture", json={"paths": added.json()["uncaptured"]})

    assert again.status_code == 200, again.text
    assert again.json()["identity_required"] is True
    assert again.json()["uncaptured"] == ["rating/files/data/rates.csv"]


@pytest.mark.parametrize(
    "path", ["../elsewhere.csv", "/etc/passwd", "C:/x.csv", ".git/config", ".haute/uploads/a"]
)
def test_a_capture_takes_only_files_in_the_project(client: TestClient, path: str) -> None:
    response = client.post("/api/git/capture", json={"paths": ["rating/main.py", path]})

    assert response.status_code == 400
    assert _refused(response) == f"{path} is not a file in the project."


def test_a_capture_names_at_least_one_file_and_needs_the_session(client: TestClient) -> None:
    assert client.post("/api/git/capture", json={"paths": []}).status_code == 422
    no_cookie = client.post(
        "/api/git/capture", json={"paths": ["rating/main.py"]}, headers={"cookie": ""}
    )
    assert no_cookie.status_code == 403


def test_a_staging_file_a_stopped_server_left_is_removed_at_the_next_upload(
    client: TestClient, project: Path
) -> None:
    from haute.routes import project_files

    staging = project / ".haute" / "uploads"
    staging.mkdir(parents=True)
    stale = staging / "stale.upload"
    stale.write_bytes(b"half a file")
    before = project_files._SERVER_STARTED - 60
    os.utime(stale, (before, before))
    # One this server is still writing, and a file that is no staging file, stay.
    streaming = staging / "streaming.upload"
    streaming.write_bytes(b"still coming")
    other = staging / "notes.txt"
    other.write_bytes(b"not an upload")
    os.utime(other, (before, before))

    response = _put(client, "pipeline", "data/rates.csv", b"rate\n")

    assert response.status_code == 200, response.text
    assert sorted(os.listdir(staging)) == ["notes.txt", "streaming.upload"]


# ── downloading and deleting ───────────────────────────────────────────────


def test_a_download_is_an_attachment_never_rendered_inline(
    client: TestClient, project: Path
) -> None:
    outputs = project / "outputs"
    outputs.mkdir()
    (outputs / "report.html").write_text("<script>alert(1)</script>")

    response = client.get(
        "/api/project-files/content", params={"place": "local", "path": "outputs/report.html"}
    )

    assert response.status_code == 200
    assert response.content == b"<script>alert(1)</script>"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_a_dot_file_cannot_be_downloaded(client: TestClient, project: Path) -> None:
    (project / "data").mkdir()
    (project / "data" / ".env").write_text("SECRET=1")

    response = client.get(
        "/api/project-files/content", params={"place": "local", "path": "data/.env"}
    )

    assert response.status_code == 400


def test_a_pipeline_file_is_deleted_at_its_revision_and_the_removal_saved(
    client: TestClient, project: Path
) -> None:
    written = _put(client, "pipeline", "data/old.csv", b"a\n").json()

    stale = client.delete(
        "/api/project-files/content",
        params={"place": "pipeline", "path": "data/old.csv", "revision": "0-0"},
    )
    assert stale.status_code == 412

    deleted = client.delete(
        "/api/project-files/content",
        params={"place": "pipeline", "path": "data/old.csv", "revision": written["revision"]},
    )

    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["git_sha"]
    assert not (project / "rating/files/data/old.csv").exists()
    assert _committed_message(project) == "Deleted rating/files/data/old.csv"
    tree = git_run(project, "ls-tree", "-r", "--name-only", f"{WORKING}-save")
    assert "rating/files/data/old.csv" not in tree


def test_outputs_cannot_be_deleted(client: TestClient, project: Path) -> None:
    (project / "outputs").mkdir()
    (project / "outputs" / "scored.parquet").write_bytes(b"x")
    revision = _entries(_list(client, "local", "outputs"))["scored.parquet"]["revision"]

    response = client.delete(
        "/api/project-files/content",
        params={"place": "local", "path": "outputs/scored.parquet", "revision": revision},
    )

    assert response.status_code == 403
    assert (project / "outputs" / "scored.parquet").exists()


# ── which names are versioned ─────────────────────────────────────────────

#: Names in the pipeline folder's files/ that the project versions through Haute, and names
#: it never does. The Files panels and a milestone's sweep both read this one rule.
NAMES = [
    ("data/rates.csv", True),
    ("models/freq.cbm", True),
    (".env.local", False),
    ("data/.DS_Store", False),
    (".ipynb_checkpoints/model-checkpoint.ipynb", False),
    ("data/.~lock.rates.csv#", False),
    ("__pycache__/main.cpython-311.pyc", False),
    ("helpers.pyc", False),
]


@pytest.mark.parametrize(("name", "versioned"), NAMES, ids=[name for name, _ in NAMES])
def test_the_panels_and_the_sweep_take_the_same_names(
    project: Path, name: str, versioned: bool
) -> None:
    from fastapi import HTTPException

    from haute._git_transactions import _pipeline_folder_new_files
    from haute.routes.project_files import _target

    path = project / "rating" / "files" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")

    swept = f"rating/files/{name}" in _pipeline_folder_new_files("rating", cwd=project)
    try:
        _target(project, "pipeline", name)
        taken = True
    except HTTPException:
        taken = False

    assert (swept, taken) == (versioned, versioned)

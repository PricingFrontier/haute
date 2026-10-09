"""Tests for haute._extensions — installed packages that add a view to ``haute serve``."""

from __future__ import annotations

import itertools
import sys
import textwrap
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from starlette.routing import Route

from haute import _extensions
from haute._extensions import (
    EXTENSIONS_GROUP,
    ExtensionError,
    discover_extensions,
    extension_package_dirs,
    mount_extensions,
)

_module_names = (f"haute_test_extension_{n}" for n in itertools.count())

# A well-formed extension module. ``{assets}`` is its asset directory; the router records
# the project directory it was created for and answers one GET route.
_EXTENSION = """
from pathlib import Path

from fastapi import APIRouter

label = "Forms"
assets_dir = Path({assets!r})
entry = "forms-embed.js"
created_for = []


def create_router(project_dir):
    created_for.append(project_dir)
    router = APIRouter()

    @router.get("/hello")
    def hello():
        return {{"hello": "from forms", "project": str(project_dir)}}

    return router
"""


# An extension that supplies the Quote Input's tables: ``{body}`` is quote_tables's body.
_SUPPLIER = """
from pathlib import Path

from fastapi import APIRouter

label = "Workbench"
assets_dir = Path("unbuilt")
entry = "workbench.js"
asked_for = []


def create_router(project_dir):
    return APIRouter()


def quote_tables(project_dir):
    asked_for.append(project_dir)
    {body}
"""

_TABLE = {
    "path": "$[:]",
    "label": "policy_details",
    "emit": True,
    "row_id_column": None,
    "columns": [
        {
            "name": "state",
            "path": "$[:].policy_details.state",
            "type": "str",
            "status": "Confirmed",
            "selected": True,
            "levels": ["CA", "NY"],
        }
    ],
}


def _supplier(body: str) -> str:
    return _SUPPLIER.format(body=body)


@pytest.fixture()
def write_extension(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Write an importable extension package and return its module name."""
    root = tmp_path / "site"
    root.mkdir()
    monkeypatch.syspath_prepend(str(root))
    written: list[str] = []

    def write(source: str | None = None) -> str:
        name = next(_module_names)
        package = root / name
        package.mkdir()
        assets = tmp_path / f"{name}_assets"
        body = _EXTENSION.format(assets=str(assets)) if source is None else source
        (package / "__init__.py").write_text(textwrap.dedent(body), encoding="utf-8")
        written.append(name)
        return name

    yield write
    for name in written:
        sys.modules.pop(name, None)


@pytest.fixture()
def installed(monkeypatch: pytest.MonkeyPatch):
    """Install entry points: ``installed(("forms", "module"), ...)``."""

    def install(*declared: tuple[str, str]) -> None:
        points = [EntryPoint(name, value, EXTENSIONS_GROUP) for name, value in declared]
        monkeypatch.setattr(
            _extensions,
            "entry_points",
            lambda group: [p for p in points if p.group == group],
        )

    return install


def _app_like_haute_server(project: Path) -> FastAPI:
    """An app registered in haute.server's order: extensions, then the catch-alls."""
    app = FastAPI()
    mount_extensions(app, discover_extensions(project))

    async def not_found(_request):  # pragma: no cover - must never be reached here
        return HTMLResponse("guard", status_code=404)

    app.router.routes.append(Route("/api/{rest:path}", not_found, methods=["GET"]))

    @app.get("/{full_path:path}", response_model=None)
    async def spa(full_path: str) -> HTMLResponse:
        return HTMLResponse("<!doctype html>")

    return app


def test_discovery_loads_extensions_in_name_order_for_the_project(
    tmp_path: Path, write_extension, installed
) -> None:
    zeta, alpha = write_extension(), write_extension()
    installed(("zeta", zeta), ("alpha", alpha))

    extensions = discover_extensions(tmp_path / "project")

    assert [e.name for e in extensions] == ["alpha", "zeta"]
    assert sys.modules[alpha].created_for == [tmp_path / "project"]
    forms = extensions[0]
    assert (forms.label, forms.entry) == ("Forms", "forms-embed.js")
    assert forms.api_base == "/api/extensions/alpha"
    assert forms.entry_url == "/extensions/alpha/forms-embed.js"


def test_extension_routes_and_assets_are_reached_before_the_catch_alls(
    tmp_path: Path, write_extension, installed
) -> None:
    module = write_extension()
    installed(("forms", module))
    client = TestClient(_app_like_haute_server(tmp_path))
    assets = Path(sys.modules[module].assets_dir)
    assets.mkdir()
    (assets / "forms-embed.js").write_text("export function mount() {}", encoding="utf-8")

    api = client.get("/api/extensions/forms/hello")
    asset = client.get("/extensions/forms/forms-embed.js")

    assert api.json() == {"hello": "from forms", "project": str(tmp_path)}
    assert asset.status_code == 200
    assert asset.text == "export function mount() {}"
    assert asset.headers["cache-control"] == "no-cache"
    assert client.get("/extensions/forms/missing.js").status_code == 404
    (tmp_path / "secret.txt").write_text("not an asset", encoding="utf-8")
    assert client.get("/extensions/forms/..%2Fsecret.txt").status_code == 404


def test_listing_reports_whether_the_browser_module_is_built(
    tmp_path: Path, write_extension, installed
) -> None:
    module = write_extension()
    installed(("forms", module))
    client = TestClient(_app_like_haute_server(tmp_path))
    assets = Path(sys.modules[module].assets_dir)

    (before,) = client.get("/api/extensions").json()["extensions"]
    assets.mkdir()
    (assets / "forms-embed.js").write_text("", encoding="utf-8")
    (after,) = client.get("/api/extensions").json()["extensions"]

    assert before["ready"] is False
    assert str(assets / "forms-embed.js") in before["detail"]
    assert "Build Forms's front end" in before["detail"]
    assert after == {
        "name": "forms",
        "label": "Forms",
        "api_base": "/api/extensions/forms",
        "entry_url": "/extensions/forms/forms-embed.js",
        "ready": True,
        "detail": None,
        "quote_tables": False,
    }


def test_no_installed_extensions_lists_none(installed) -> None:
    installed()
    app = FastAPI()
    mount_extensions(app, discover_extensions(Path.cwd()))

    assert TestClient(app).get("/api/extensions").json() == {"extensions": []}


def test_haute_server_lists_extensions_ahead_of_its_404_guard_behind_the_session(
    client: TestClient,
) -> None:
    # The route clients in this suite carry the session cookie unless one is given.
    rejected = client.get("/api/extensions", headers={"cookie": "haute_session=wrong"})
    listed = client.get("/api/extensions")

    assert rejected.status_code == 403
    # The /api 404 guard would answer {"detail": "No such route: ..."} instead.
    assert listed.status_code == 200
    assert isinstance(listed.json()["extensions"], list)


_BAD_ATTRIBUTES = [
    ("label = 3\n", "label must be a str, not int"),
    ("label = '  '\n", "label is empty"),
    ("label = 'Forms'\n", "does not define assets_dir"),
    ("label = 'Forms'\nassets_dir = 'static'\n", "assets_dir must be a Path, not str"),
    (
        "from pathlib import Path\nlabel = 'Forms'\nassets_dir = Path('s')\nentry = 'js/x.js'\n",
        "entry must be a file name in assets_dir",
    ),
    (
        "from pathlib import Path\nlabel = 'Forms'\nassets_dir = Path('s')\nentry = 'x.js'\n",
        "does not define create_router",
    ),
    (
        "from pathlib import Path\nlabel = 'Forms'\nassets_dir = Path('s')\nentry = 'x.js'\n"
        "def create_router(project_dir):\n    raise OSError('no forms folder')\n",
        "create_router failed: no forms folder",
    ),
    (
        "from pathlib import Path\nlabel = 'Forms'\nassets_dir = Path('s')\nentry = 'x.js'\n"
        "def create_router(project_dir):\n    return object()\n",
        "create_router returned object, not a FastAPI APIRouter",
    ),
    ("raise ImportError('broken')\n", "could not be imported: broken"),
]


@pytest.mark.parametrize(("source", "message"), _BAD_ATTRIBUTES)
def test_a_malformed_extension_fails_loudly_naming_it(
    tmp_path: Path, write_extension, installed, source: str, message: str
) -> None:
    module = write_extension(source)
    installed(("forms", module))

    with pytest.raises(ExtensionError, match="Haute extension 'forms'") as raised:
        discover_extensions(tmp_path)

    assert message in str(raised.value)


@pytest.mark.parametrize("name", ["Forms", "1forms", "forms_x", "pipeline"])
def test_an_invalid_or_reserved_name_is_rejected(
    tmp_path: Path, write_extension, installed, name: str
) -> None:
    installed((name, write_extension()))

    with pytest.raises(ExtensionError, match="an extension name is lower-case"):
        discover_extensions(tmp_path)


def test_two_packages_declaring_one_name_are_rejected(
    tmp_path: Path, write_extension, installed
) -> None:
    first, second = write_extension(), write_extension()
    installed(("forms", first), ("forms", second))

    with pytest.raises(ExtensionError, match="declared by more than one installed package"):
        discover_extensions(tmp_path)


def test_package_dirs_are_found_without_importing_the_extension(
    tmp_path: Path, write_extension, installed
) -> None:
    module = write_extension()
    installed(("forms", f"{module}.sub:thing"))

    dirs = extension_package_dirs()

    assert [d.resolve() for d in dirs] == [(tmp_path / "site" / module).resolve()]
    assert module not in sys.modules


def test_quote_tables_come_from_the_extension_that_supplies_them(
    tmp_path: Path, write_extension, installed
) -> None:
    plain, supplier = write_extension(), write_extension(_supplier(f"return [{_TABLE!r}]"))
    installed(("forms", plain), ("workbench", supplier))
    client = TestClient(_app_like_haute_server(tmp_path))

    listing = client.get("/api/extensions").json()["extensions"]
    served = client.get("/api/quote-tables")

    assert {e["name"]: e["quote_tables"] for e in listing} == {"forms": False, "workbench": True}
    assert served.json() == {"extension": "workbench", "tables": [_TABLE], "sample": {}}
    assert sys.modules[supplier].asked_for == [tmp_path]

    installed(("forms", plain))
    without = TestClient(_app_like_haute_server(tmp_path))
    assert [e["quote_tables"] for e in without.get("/api/extensions").json()["extensions"]] == [
        False
    ]
    assert without.get("/api/quote-tables").status_code == 404


@pytest.mark.parametrize(
    ("body", "status", "says"),
    [
        ("raise RuntimeError('form.json is unreadable')", 500, "form.json is unreadable"),
        ("return {}", 500, "dict"),
        (f"return [{{**{_TABLE!r}, 'label': 'class'}}]", 422, "'class'"),
    ],
)
def test_quote_tables_fail_loudly(
    tmp_path: Path, write_extension, installed, body: str, status: int, says: str
) -> None:
    installed(("workbench", write_extension(_supplier(body))))
    response = TestClient(_app_like_haute_server(tmp_path)).get("/api/quote-tables")

    assert response.status_code == status
    assert says in response.json()["detail"]
    if status == 500:
        assert "'workbench'" in response.json()["detail"]
    else:
        assert response.json()["type"] == "ApiInputSchemaError"

    installed(
        ("one", write_extension(_supplier("return []"))),
        ("two", write_extension(_supplier("return []"))),
    )
    with pytest.raises(ExtensionError, match="'one'.*'two'"):
        discover_extensions(tmp_path)
    not_a_function = write_extension(
        _supplier("return []") + "\nquote_tables = ['not', 'a', 'function']\n"
    )
    installed(("workbench", not_a_function))
    with pytest.raises(ExtensionError, match="quote_tables"):
        discover_extensions(tmp_path)


def _sampler(sample_body: str) -> str:
    """A supplier of one table that also supplies a sample quote: ``sample_body`` is its body."""
    return _supplier(f"return [{_TABLE!r}]") + (
        f"\n\ndef quote_sample(project_dir):\n    {sample_body}\n"
    )


def test_the_supplier_serves_its_sample_beside_its_tables(
    tmp_path: Path, write_extension, installed
) -> None:
    sample = {"policy_details": {"state": "NY"}}
    installed(("workbench", write_extension(_sampler(f"return {sample!r}"))))

    served = TestClient(_app_like_haute_server(tmp_path)).get("/api/quote-tables")

    assert served.json() == {"extension": "workbench", "tables": [_TABLE], "sample": sample}

    installed(("workbench", write_extension(_sampler("return None"))))
    none = TestClient(_app_like_haute_server(tmp_path)).get("/api/quote-tables")
    assert none.json()["sample"] == {}


@pytest.mark.parametrize(
    ("body", "says"),
    [
        ("raise RuntimeError('form.json is unreadable')", "form.json is unreadable"),
        ("return ['not', 'a', 'quote']", "not an object"),
    ],
)
def test_a_failing_or_misshapen_sample_answers_500_naming_the_extension(
    tmp_path: Path, write_extension, installed, body: str, says: str
) -> None:
    installed(("workbench", write_extension(_sampler(body))))

    response = TestClient(_app_like_haute_server(tmp_path)).get("/api/quote-tables")

    assert response.status_code == 500
    assert says in response.json()["detail"]
    assert "'workbench'" in response.json()["detail"]


def test_quote_sample_needs_quote_tables_and_must_be_a_function(
    tmp_path: Path, write_extension, installed
) -> None:
    without_tables = write_extension(
        textwrap.dedent(_EXTENSION.format(assets=str(tmp_path / "assets")))
        + "\n\ndef quote_sample(project_dir):\n    return {}\n"
    )
    installed(("forms", without_tables))
    with pytest.raises(ExtensionError, match="quote_sample without quote_tables"):
        discover_extensions(tmp_path)

    not_a_function = write_extension(_sampler("return {}") + "\nquote_sample = {'a': 1}\n")
    installed(("workbench", not_a_function))
    with pytest.raises(ExtensionError, match="quote_sample must be a function"):
        discover_extensions(tmp_path)

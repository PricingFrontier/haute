"""``haute init`` command."""

from pathlib import Path

import click

from haute._io import read_user_text

_DEV_DEPS_BLOCK = """
[dependency-groups]
dev = [
    "ruff>=0.8",
    "mypy>=1.13",
    "pytest>=8.3",
]
"""

_MYPY_BLOCK = """
[tool.mypy]
ignore_missing_imports = false

[[tool.mypy.overrides]]
module = ["haute.*", "catboost.*", "xgboost.*", "lightgbm.*", "sklearn.*"]
ignore_missing_imports = true
"""


def _ensure_haute_dependency(pyproject_path: Path, name: str) -> None:
    """Add ``haute`` to pyproject.toml dependencies.

    If pyproject.toml exists, insert ``"haute"`` into the dependencies
    list (if not already present).  If it doesn't exist, create a
    minimal pyproject.toml.

    Also ensures a ``[dependency-groups]`` dev section exists with
    ruff, mypy, and pytest so that the generated CI workflows work.
    """
    if pyproject_path.exists():
        text = read_user_text(pyproject_path)
        if "haute" not in text:
            # Insert into existing dependencies list
            if "dependencies = [" in text:
                text = text.replace(
                    "dependencies = [",
                    'dependencies = [\n    "haute",',
                    1,
                )
            else:
                # No dependencies key - append a section
                text += '\n[project]\ndependencies = [\n    "haute",\n]\n'
        if "[dependency-groups]" not in text:
            text += _DEV_DEPS_BLOCK
        if "[tool.mypy]" not in text:
            text += _MYPY_BLOCK
        pyproject_path.write_text(text, encoding="utf-8")
    else:
        pyproject_path.write_text(
            f'[project]\nname = "{name}"\nversion = "0.1.0"\n'
            f'requires-python = ">=3.11"\n'
            f'dependencies = [\n    "haute",\n]\n' + _DEV_DEPS_BLOCK + _MYPY_BLOCK,
            encoding="utf-8",
        )


@click.command()
@click.option(
    "--target",
    type=click.Choice(
        [
            "databricks",
            "container",
            "azure-container-apps",
            "aws-ecs",
            "gcp-run",
            "sagemaker",
            "azure-ml",
        ]
    ),
    default="databricks",
    help="Deploy target (default: databricks).",
)
@click.option(
    "--ci",
    type=click.Choice(["github", "gitlab", "azure-devops", "none"]),
    default="github",
    help="CI/CD provider (default: github).",
)
def init(target: str, ci: str) -> None:
    """Scaffold a Haute pricing project in the current directory.

    Generates haute.toml, CI/CD workflows, credentials template, and a
    starter pipeline - all configured for the chosen deploy target and
    CI provider.

    \b
    Examples:
      haute init                                  # databricks + github
      haute init --target container --ci none      # container, no CI
      haute init --target sagemaker --ci github   # AWS + github
    """
    import tomllib

    from haute._scaffold import (
        azure_devops_yml,
        env_example,
        github_ci_yml,
        github_deploy_prod_yml,
        github_deploy_yml,
        gitlab_ci_yml,
        haute_toml,
        pre_commit_hook,
        starter_pipeline,
        starter_test,
        starter_test_quote,
        starter_utility_features,
        starter_utility_init,
    )

    # Solo mode is configured in haute.toml, not via a CLI flag.
    # Default is team mode; user sets min_approvers = 0 for solo.

    project_dir = Path.cwd()

    if (project_dir / "haute.toml").exists():
        click.echo("Error: haute.toml already exists - project already initialised.", err=True)
        raise SystemExit(1)

    # -- Resolve project name --------------------------------------------------
    pyproject_path = project_dir / "pyproject.toml"
    name = project_dir.name.replace("-", "_").replace(" ", "_").lower()

    if pyproject_path.exists():
        with open(pyproject_path, "rb") as fh:
            pyproject = tomllib.load(fh)
        if "project" in pyproject and "name" in pyproject["project"]:
            name = pyproject["project"]["name"]

    # -- pyproject.toml - ensure haute is a dependency -------------------------
    _ensure_haute_dependency(pyproject_path, name)

    # -- Directories -----------------------------------------------------------
    (project_dir / "data").mkdir(exist_ok=True)
    (project_dir / "prompts").mkdir(exist_ok=True)

    # -- Remove root main.py left over from `uv init` -------------------------
    root_main = project_dir / "main.py"
    if root_main.exists():
        root_main.unlink()

    # -- rating/ - user pipeline files -----------------------------------------
    rating_dir = project_dir / "rating"
    rating_dir.mkdir(exist_ok=True)
    (rating_dir / "__init__.py").write_text("", encoding="utf-8")

    # -- rating/utility/ - project-level utility functions ----------------------
    utility_dir = rating_dir / "utility"
    utility_dir.mkdir(exist_ok=True)
    (utility_dir / "__init__.py").write_text(starter_utility_init(), encoding="utf-8")
    (utility_dir / "features.py").write_text(starter_utility_features(), encoding="utf-8")

    # -- rating/main.py - starter pipeline -------------------------------------
    (rating_dir / "main.py").write_text(starter_pipeline(name), encoding="utf-8")

    # -- rating/ placeholder directories (used once the pipeline grows) --------
    for sub in ("config", "models", "outputs"):
        (rating_dir / sub).mkdir(exist_ok=True)

    # -- haute.toml - project + deploy + safety + CI config --------------------
    (project_dir / "haute.toml").write_text(
        haute_toml(name, target, ci),
        encoding="utf-8",
    )

    # -- .env.example - target-specific credentials ----------------------------
    (project_dir / ".env.example").write_text(env_example(target), encoding="utf-8")

    # -- Starter test file + test quotes ---------------------------------------
    tests_dir = project_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    quotes_dir = tests_dir / "quotes"
    quotes_dir.mkdir(exist_ok=True)
    (quotes_dir / "example.json").write_text(
        starter_test_quote(),
        encoding="utf-8",
    )
    (tests_dir / "test_pipeline.py").write_text(
        starter_test(name),
        encoding="utf-8",
    )

    # -- CI/CD workflow files --------------------------------------------------
    ci_files: list[str] = []
    if ci == "github":
        workflows_dir = project_dir / ".github" / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)
        (workflows_dir / "ci.yml").write_text(github_ci_yml(), encoding="utf-8")
        (workflows_dir / "deploy-staging.yml").write_text(
            github_deploy_yml(target),
            encoding="utf-8",
        )
        (workflows_dir / "deploy-production.yml").write_text(
            github_deploy_prod_yml(target),
            encoding="utf-8",
        )
        ci_files = [
            ".github/workflows/ci.yml",
            ".github/workflows/deploy-staging.yml",
            ".github/workflows/deploy-production.yml",
        ]
    elif ci == "gitlab":
        (project_dir / ".gitlab-ci.yml").write_text(
            gitlab_ci_yml(target),
            encoding="utf-8",
        )
        ci_files = [".gitlab-ci.yml"]
    elif ci == "azure-devops":
        (project_dir / "azure-pipelines.yml").write_text(
            azure_devops_yml(target),
            encoding="utf-8",
        )
        ci_files = ["azure-pipelines.yml"]

    # -- Pre-commit hook -------------------------------------------------------
    hooks_dir = project_dir / ".githooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text(pre_commit_hook(), encoding="utf-8")
    hook_path.chmod(0o755)

    # Install into .git/hooks if inside a git repo
    git_hooks_dir = project_dir / ".git" / "hooks"
    if git_hooks_dir.is_dir():
        installed = git_hooks_dir / "pre-commit"
        installed.write_text(pre_commit_hook(), encoding="utf-8")
        installed.chmod(0o755)

    # -- .gitignore - append if exists, create if not --------------------------
    gitignore_path = project_dir / ".gitignore"
    haute_entries = ".env\n*.haute.json\nimpact_report.md\n.haute_cache/\nmlruns/\ndata/\n"
    if gitignore_path.exists():
        existing = read_user_text(gitignore_path)
        missing = [line for line in haute_entries.splitlines() if line and line not in existing]
        if missing:
            with open(gitignore_path, "a", encoding="utf-8") as fh:
                fh.write("\n# Haute\n" + "\n".join(missing) + "\n")
    else:
        gitignore_path.write_text(
            "__pycache__/\n*.pyc\n.venv/\n.env\n*.haute.json\n.haute_cache/\nmlruns/\ndata/\n",
            encoding="utf-8",
        )

    # -- Summary ---------------------------------------------------------------
    click.echo(f"Initialised Haute project '{name}' ({target} + {ci})\n")
    click.echo("  pyproject.toml        - haute added as dependency")
    click.echo("  haute.toml            - project, deploy, safety & CI config")
    click.echo(f"  .env.example         - {target} credentials template")
    click.echo("  rating/main.py       - starter pipeline")
    click.echo("  rating/utility/      - project-level utility functions")
    click.echo("  data/                - put your data files here")
    click.echo("  prompts/             - reusable AI prompts for pipeline tasks")
    click.echo("  tests/               - starter test + example quote payloads")
    click.echo("  .githooks/pre-commit - auto-format on commit (ruff)")
    for f in ci_files:  # noqa: F841
        click.echo(f"  {f}")
    if git_hooks_dir.is_dir():
        click.echo("  .git/hooks/pre-commit  (installed)")
    click.echo("\nNext steps:")
    click.echo("  uv sync                # install dependencies")
    click.echo("  cp .env.example .env   # fill in credentials")
    click.echo("  haute serve")

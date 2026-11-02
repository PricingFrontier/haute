"""Databricks model smoke runner: install a logged model's locked environment and score.

Builds the ``minimal_live_quote`` example's pyfunc model as ``haute deploy`` logs it for
Databricks (its environment locked for Linux x86_64 and the project's Python by
``haute.deploy._mlflow.served_lock``, haute shipped as the given wheel under ``code/``),
saves it locally, then installs its ``requirements.txt`` with pip into a fresh virtual
environment on that Python, as Model Serving builds one, and in that environment:

* ``pip check`` passes: the full pyfunc profile carries haute's whole closure;
* every installed distribution of the lock is at its locked version;
* the model loads and scores the example's golden request to its golden output.

Run on Linux x86_64 (the weekly container-smoke lane):
``uv run python scripts/pyfunc_smoke.py --wheel dist/haute-*.whl --work-dir .pyfunc-smoke``.
"""

from __future__ import annotations

import argparse
import glob
import json
import shutil
import subprocess
import sys
from pathlib import Path

_SCORE = """
import json, sys
import mlflow.pyfunc
import pandas as pd

model = mlflow.pyfunc.load_model(sys.argv[1])
request = json.loads(open(sys.argv[2], encoding="utf-8").read())
frame = pd.DataFrame(request if isinstance(request, list) else [request])
print(json.dumps(model.predict(frame).to_dict(orient="list")))
"""


def run_smoke(wheel: Path, work_dir: Path) -> int:
    import mlflow.pyfunc

    from haute.deploy import DeployConfig, resolve_config
    from haute.deploy._mlflow import _pyfunc_model_arguments, _stage_artifacts, served_lock
    from haute.deploy._served_haute import ServedHaute
    from haute.deploy._utils import build_manifest

    repo_root = Path(__file__).resolve().parents[1]
    example = (
        repo_root / "src" / "haute" / "assistant" / "assets" / "examples" / ("minimal_live_quote")
    )
    if work_dir.exists():
        shutil.rmtree(work_dir)
    project, model_dir, staging, venv = (
        work_dir / "project",
        work_dir / "model",
        work_dir / "staging",
        work_dir / "venv",
    )
    shutil.copytree(example, project)
    staging.mkdir(parents=True)
    served = ServedHaute(f"code/{wheel.name}", wheel)

    with resolve_config(DeployConfig.from_toml(project / "haute.toml")) as resolved:
        lock = served_lock(resolved, served)
        manifest = {**build_manifest(resolved), "dependency_lock": lock.provenance()}
        manifest_path = work_dir / "deploy_manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        mlflow.pyfunc.save_model(
            path=str(model_dir),
            **_pyfunc_model_arguments(
                resolved, manifest_path, _stage_artifacts(resolved, staging), served, lock
            ),
        )
    print(f"Model saved to {model_dir}, its environment locked for CPython {lock.python_version}")

    _run(["uv", "venv", "--seed", "--python", lock.python_version, str(venv)])
    python = str(venv / "bin" / "python")
    # As Model Serving installs it: pip, from the model's own directory (code/ is there).
    _run([python, "-m", "pip", "install", "-r", "requirements.txt"], cwd=model_dir)
    _run([python, "-m", "pip", "check"])

    listed = json.loads(_run([python, "-m", "pip", "list", "--format=json"]))
    installed = {_canonical(item["name"]): item["version"] for item in listed}
    wrong = {
        name: (version, installed.get(name))
        for name, version in lock.pins.items()
        if installed.get(name) != version
    }
    if wrong:
        print(f"Installed versions differ from the lock: {wrong}", file=sys.stderr)
        return 1

    answer = json.loads(
        _run([python, "-c", _SCORE, str(model_dir), str(project / "golden_request.json")])
    )
    golden = json.loads((project / "golden_output.json").read_text(encoding="utf-8"))
    expected = {key: value for key, value in golden.items() if key != "row_count"}
    if answer != expected:
        print(f"Scored {answer}, expected {expected}", file=sys.stderr)
        return 1
    print(f"The installed model scores the golden request: {answer}")
    return 0


def _canonical(name: str) -> str:
    from packaging.utils import canonicalize_name

    return str(canonicalize_name(name))


def _run(command: list[str], cwd: Path | None = None) -> str:
    print("+", " ".join(command), flush=True)
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout, result.stderr, sep="\n", file=sys.stderr)
        raise SystemExit(result.returncode)
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wheel", required=True, help="The haute wheel the model ships.")
    parser.add_argument("--work-dir", type=Path, default=Path(".pyfunc-smoke"))
    args = parser.parse_args()
    matches = glob.glob(args.wheel)
    if len(matches) != 1:
        print(f"Error: expected one wheel matching {args.wheel}, found {matches}", file=sys.stderr)
        sys.exit(1)
    sys.exit(run_smoke(Path(matches[0]).resolve(), args.work_dir.resolve()))


if __name__ == "__main__":
    main()

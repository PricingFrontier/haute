"""Container deployment smoke runner - local build preparation and serve check."""

from __future__ import annotations

import argparse
import glob
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from haute.deploy import DeployConfig, resolve_config
from haute.deploy._container import prepare_build_directory


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def run_smoke(
    build_dir: Path,
    example: str = "minimal_live_quote",
    wheel: str | None = None,
    serve_check: bool = False,
    port: int = 8080,
) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    example_dir = repo_root / "src" / "haute" / "assistant" / "assets" / "examples" / example
    if not example_dir.is_dir():
        print(f"Error: Example directory not found: {example_dir}", file=sys.stderr)
        return 1

    wheel_path: Path | None = None
    if wheel:
        matches = glob.glob(wheel)
        if matches:
            wheel_path = Path(matches[0]).resolve()
        else:
            candidate = Path(wheel)
            if candidate.is_file():
                wheel_path = candidate.resolve()
            else:
                print(f"Error: Specified wheel not found: {wheel}", file=sys.stderr)
                return 1

    build_dir = build_dir.resolve()
    project_dir = build_dir / "project"
    image_dir = build_dir / "image"

    if project_dir.exists():
        shutil.rmtree(project_dir)
    shutil.copytree(example_dir, project_dir)

    toml_path = project_dir / "haute.toml"
    original_toml = toml_path.read_text(encoding="utf-8")
    container_deploy_section = """
[deploy]
target = "container"

[deploy.container]
base_image = "python:3.11.9-slim"
"""
    toml_path.write_text(original_toml + container_deploy_section, encoding="utf-8")

    deploy_config = DeployConfig.from_toml(toml_path)
    resolved = resolve_config(deploy_config)

    image_dir.mkdir(parents=True, exist_ok=True)
    try:
        prepare_build_directory(
            resolved,
            image_dir,
            haute_requirement=str(wheel_path) if wheel_path else None,
        )
    finally:
        resolved.close()

    print(f"Prepared container build directory in {image_dir}:")
    for file_path in sorted(image_dir.rglob("*")):
        if file_path.is_file():
            rel = file_path.relative_to(image_dir)
            print(f"  {rel} ({file_path.stat().st_size} bytes)")

    if not serve_check:
        return 0

    effective_port = _find_free_port() if port == 0 else port
    log_file_path = build_dir / "uvicorn.log"
    log_file = log_file_path.open("w+", encoding="utf-8")

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(effective_port),
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=image_dir,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        health_url = f"http://127.0.0.1:{effective_port}/health"
        health_data: dict | None = None
        deadline = time.time() + 60.0
        while time.time() < deadline:
            if proc.poll() is not None:
                log_file.flush()
                log_content = log_file_path.read_text(encoding="utf-8")
                print(
                    f"Error: uvicorn exited prematurely ({proc.returncode}):\n{log_content}",
                    file=sys.stderr,
                )
                return 1
            try:
                req = urllib.request.Request(health_url)
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    if resp.status == 200:
                        body = json.loads(resp.read().decode("utf-8"))
                        if body.get("status") == "ok":
                            health_data = body
                            break
            except Exception:
                pass
            time.sleep(0.25)

        if health_data is None:
            log_file.flush()
            log_content = log_file_path.read_text(encoding="utf-8")
            print(
                f"Error: GET /health failed to return 200 ok within 60s:\n{log_content}",
                file=sys.stderr,
            )
            return 1

        golden_req_path = project_dir / "golden_request.json"
        golden_out_path = project_dir / "golden_output.json"
        if not golden_req_path.is_file() or not golden_out_path.is_file():
            print(
                f"Error: Golden files missing from project ({golden_req_path}, {golden_out_path})",
                file=sys.stderr,
            )
            return 1

        expected_out = json.loads(golden_out_path.read_text(encoding="utf-8"))
        expected_row_count = expected_out.get("row_count")
        expected_columns = {key: value for key, value in expected_out.items() if key != "row_count"}
        if not isinstance(expected_row_count, int) or not expected_columns:
            print(
                f"Error: {golden_out_path} must carry row_count and at least one column",
                file=sys.stderr,
            )
            return 1

        quote_url = f"http://127.0.0.1:{effective_port}/quote"
        req_data = golden_req_path.read_bytes()
        post_req = urllib.request.Request(
            quote_url,
            data=req_data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(post_req, timeout=10.0) as resp:
                quote_status = resp.status
                quote_data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            print(f"Error: POST /quote failed with HTTP {exc.code}: {err_body}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"Error: POST /quote failed: {exc}", file=sys.stderr)
            return 1

        if quote_status != 200:
            print(f"Error: POST /quote returned status {quote_status}", file=sys.stderr)
            return 1

        actual_row_count = quote_data.get("row_count")
        if actual_row_count != expected_row_count:
            print(
                f"Error: POST /quote row_count mismatch "
                f"(expected {expected_row_count}, got {actual_row_count})",
                file=sys.stderr,
            )
            return 1

        rows = quote_data.get("rows", [])
        if not rows:
            print("Error: POST /quote returned empty rows", file=sys.stderr)
            return 1

        for column, expected_values in expected_columns.items():
            expected_list = (
                expected_values if isinstance(expected_values, list) else [expected_values]
            )
            actual_values = [row.get(column) for row in rows]
            if actual_values != expected_list:
                print(
                    f"Error: POST /quote column {column!r} mismatch "
                    f"(expected {expected_list}, got {actual_values})",
                    file=sys.stderr,
                )
                return 1

        print("GET /health response:")
        print(json.dumps(health_data, indent=2))
        print("POST /quote response:")
        print(json.dumps(quote_data, indent=2))
        return 0

    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5.0)
        log_file.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Container deployment smoke verification")
    parser.add_argument(
        "--build-dir",
        required=True,
        type=Path,
        help="Path to directory where project and container image files will be prepared",
    )
    parser.add_argument(
        "--example",
        default="minimal_live_quote",
        help="Example name under assets/examples/ (default: minimal_live_quote)",
    )
    parser.add_argument(
        "--wheel",
        default=None,
        help="Path or glob to a built haute wheel to bundle into the container",
    )
    parser.add_argument(
        "--serve-check",
        action="store_true",
        help="Start uvicorn server in subprocess and verify /health and /quote endpoints",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to serve on for --serve-check (default: 8080, 0 selects a free port)",
    )

    args = parser.parse_args()
    code = run_smoke(
        build_dir=args.build_dir,
        example=args.example,
        wheel=args.wheel,
        serve_check=args.serve_check,
        port=args.port,
    )
    sys.exit(code)


if __name__ == "__main__":
    main()

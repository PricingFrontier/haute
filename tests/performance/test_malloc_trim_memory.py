"""Fresh-process evidence for whether ``_malloc_trim`` returns memory a workload freed.

Each kind of allocation a Haute process makes (a Polars frame, model-sized NumPy
buffers, Python objects) is built, freed and collected in a fresh process, which reads
its resident set before and after the trim (``_malloc_trim_probe.py``). The test
asserts only that every probe completes: the figures, recorded as evidence, decide
whether the trim stays on each platform (PL2-17, specs/execution-engine).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.performance._malloc_trim_probe import KINDS

pytestmark = [
    pytest.mark.perf,
    pytest.mark.perf_evidence,
    pytest.mark.usefixtures("_widen_sandbox_root"),
]

_PROBE = Path(__file__).with_name("_malloc_trim_probe.py")


def test_malloc_trim_is_measured_after_each_kind_of_release(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    inherited_paths = [path for path in sys.path if path]
    measurements = []
    for kind in KINDS:
        output = tmp_path / f"trim-{kind}.json"
        bootstrap = (
            "import runpy,sys;"
            f"sys.path[:0]={inherited_paths!r};"
            f"sys.argv=['probe','--kind',{kind!r},'--output',{str(output)!r}];"
            f"runpy.run_path({str(_PROBE)!r},run_name='__main__')"
        )
        completed = subprocess.run(
            [sys.executable, "-c", bootstrap], capture_output=True, text=True, check=False
        )
        assert completed.returncode == 0, completed.stderr
        measurements.append(json.loads(output.read_text(encoding="utf-8")))

    request.node.user_properties.append(
        (
            "haute_perf_evidence",
            {
                "scenario": "malloc_trim_release",
                "platform": sys.platform,
                "measurements": measurements,
            },
        )
    )

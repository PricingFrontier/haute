"""Copy a saved demo into a new disposable project without its training history."""

import argparse
import shutil
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", type=Path)
parser.add_argument("destination", type=Path)
args = parser.parse_args()
args.destination.mkdir(parents=True, exist_ok=False)
for name in ("rating", "data", "haute.toml"):
    source = args.source / name
    target = args.destination / name
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)
subprocess.run(["git", "init", "-q", str(args.destination)], check=True)
subprocess.run(["git", "-C", str(args.destination), "add", "."], check=True)
subprocess.run(
    [
        "git",
        "-C",
        str(args.destination),
        "-c",
        "user.name=Modelling audit",
        "-c",
        "user.email=audit@localhost",
        "commit",
        "-qm",
        "Isolated workflow input",
    ],
    check=True,
)
print(args.destination.resolve())

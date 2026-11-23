"""List every difference between stored and fresh goldens, grouped by normalised path."""
import gzip, json, re, sys
from collections import Counter
old_dir, new_dir = sys.argv[1], sys.argv[2]

def diffs(a, b, where=""):
    if type(a) is not type(b):
        yield where, f"type {type(a).__name__} -> {type(b).__name__}", a, b
    elif isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in b: yield f"{where}/{k}", "removed", a[k], None
            elif k not in a: yield f"{where}/{k}", "added", None, b[k]
            else: yield from diffs(a[k], b[k], f"{where}/{k}")
    elif isinstance(a, list):
        if len(a) != len(b): yield where, f"length {len(a)} -> {len(b)}", a, b
        else:
            for i, (x, y) in enumerate(zip(a, b)): yield from diffs(x, y, f"{where}[{i}]")
    elif a != b:
        yield where, "value", a, b

for name in ("holdout", "cv3"):
    old = json.load(gzip.open(f"{old_dir}/{name}.json.gz", "rt"))
    new = json.load(gzip.open(f"{new_dir}/{name}.json.gz", "rt"))
    groups = Counter()
    examples = {}
    for where, kind, a, b in diffs(old, new):
        key = re.sub(r"\[\d+\]", "[]", where)
        key = re.sub(r"^/[^/]+", "/<response>", key)
        tail = "/".join(key.split("/")[-3:])
        groups[(tail, kind)] += 1
        examples.setdefault((tail, kind), (str(a)[:90], str(b)[:90]))
    print(f"== {name}: {sum(groups.values())} differences, {len(groups)} kinds")
    for (tail, kind), count in sorted(groups.items()):
        a, b = examples[(tail, kind)]
        print(f"  {count:4d}  {tail}  [{kind}]  {a!s:.70} -> {b!s:.70}")

import gzip, json, sys
old_dir, new_dir = sys.argv[1], sys.argv[2]
def ops(recipe):  # new Recipe plain data -> old list form, plus each pruning path
    return ([{k: op[k] for k in ("stage", "parent", "settings")} for op in recipe["operations"]],
            [op["path"] for op in recipe["operations"] if op["stage"] == "pruned"])
def walk(a, b, checks):
    if isinstance(a, dict) and isinstance(b, dict):
        if "applied_recipe" in a and isinstance(a["applied_recipe"], list):
            listed, paths = ops(b["applied_recipe"])
            assert listed == a["applied_recipe"], (listed, a["applied_recipe"])
            if paths: assert paths == [a["applied_path"]], (paths, a.get("applied_path"))
            checks["configs"] += 1
        if "recipe" in a and isinstance(a["recipe"], list) and "revision" in a:
            listed, paths = ops(b["recipe"])
            assert listed == a["recipe"]
            op = b["operation"]
            assert (op["settings"] if op else {}) == a["settings"], (op, a["settings"])
            if "path_settings" in a: assert op["path"] == a["path_settings"]
            assert b["revision"] == a["revision"] and b["parent"] == a["parent"]
            checks["snapshots"] += 1
        for k in set(a) & set(b): walk(a[k], b[k], checks)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for x, y in zip(a, b): walk(x, y, checks)
for name in ("holdout", "cv3"):
    checks = {"configs": 0, "snapshots": 0}
    walk(json.load(gzip.open(f"{old_dir}/{name}.json.gz", "rt")),
         json.load(gzip.open(f"{new_dir}/{name}.json.gz", "rt")), checks)
    print(name, "semantically equal:", checks)

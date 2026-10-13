"""Source-only route inventory; imports no application or private database."""

import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from intelliplan.learning.enforcement import SURFACES, matches  # noqa: E402

paths = subprocess.run(
    ["rg", "--files", "-g", "*.py"],
    cwd=ROOT,
    capture_output=True,
    text=True,
    check=True,
).stdout.splitlines()
routes = []
for name in paths:
    if name.startswith(("tests\\", "tests/", "scripts\\", "scripts/")):
        continue
    try:
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8-sig"))
    except (SyntaxError, UnicodeError):
        continue
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr
                in {"route", "get", "post", "put", "delete", "patch"}
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
                and isinstance(decorator.args[0].value, str)
            ):
                continue
            path = decorator.args[0].value
            methods = (
                [decorator.func.attr.upper()]
                if decorator.func.attr != "route"
                else ["GET"]
            )
            for keyword in decorator.keywords:
                if keyword.arg == "methods":
                    try:
                        methods = ast.literal_eval(keyword.value)
                    except ValueError:
                        methods = ["dynamic"]
            keys = [
                key
                for key, prefixes in SURFACES.items()
                if any(matches(path, prefix) for prefix in prefixes)
            ]
            routes.append(
                dict(
                    file=name.replace("\\", "/"),
                    line=node.lineno,
                    function=node.name,
                    literal_path=path,
                    methods=methods,
                    policy_prefix_matches=keys,
                )
            )
result = dict(
    scope="literal decorators only; blueprint prefixes/dynamic registrations need runtime review",
    routes=sorted(routes, key=lambda r: (r["file"], r["line"])),
)
output = ROOT / "docs" / "school-review" / "evidence" / "route-inventory.json"
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(
    f"Inventoried {len(routes)} literal route decorators; source review, not complete policy coverage."
)

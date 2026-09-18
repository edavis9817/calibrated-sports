"""Every module constant captured in DEFAULT-ARGUMENT position, across the repo.

    python -m research.default_arg_constants            # the sweep
    python -m research.default_arg_constants --all      # same-module ones too

A DEFAULT ARGUMENT IS EVALUATED ONCE, AT IMPORT. So

    def plan_forward(conn, events, now, cap=oddsapi.FORWARD_WEEKLY_CAP):

binds the cap at import time: editing the constant changes what you READ and not
what RUNS, and no test can lower it by setting the attribute. Nothing observable
distinguishes the two - the constant says 75, the function uses 45, and both are
"the cap". Found 2026-09-18 raising that exact constant; this sweep answers how
many siblings it has.

TWO CLASSES, AND THE SPLIT IS ABOUT ODDS OF NOTICING, NOT ABOUT THE FAILURE. Both
bind at import identically. "Same module, so they move in one diff" is weaker than it
sounds: a constant at the top of a long file and a default far below it are in one diff
only if someone happens to edit both.

  CROSS-MODULE  `def f(x=other.CONST)` - the attribute can be monkeypatched or
                reassigned at runtime and the function will never see it. This is
                the defect. The fix is `x=None` and `x = other.CONST if x is None`.
  SAME-MODULE   `def f(x=CONST)` where CONST is defined in the same file. Identical
                semantics; only the chance of spotting it differs.

A SPEND LIMIT IS DANGEROUS WHEREVER IT LIVES, so the categorical rule below applies to
BOTH classes and outranks any allowlist: a budget-shaped constant must not sit in a
default argument in a module that can make requests. "Can make requests" is COMPUTED -
the file imports an HTTP client, or imports a module that does - so nothing has to be
remembered, and a research script that only reads a database is exempt by construction
rather than by a line someone maintains.

`tests/test_default_arg_constants.py` pins the cross-module count at zero.
"""
import argparse
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SKIP_DIRS = {".venv", ".git", "__pycache__", "node_modules", "web", ".pytest_cache"}


def python_files(root):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in sorted(files):
            if f.endswith(".py"):
                yield os.path.join(dirpath, f)


def module_constants(tree):
    """Names assigned at module level in ALL_CAPS."""
    out = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    out.add(t.id)
    return out


BUDGET_WORDS = ("CAP", "CREDIT", "BUDGET", "RESERVE", "LIMIT", "MAX_REQUESTS", "QUOTA",
                "SPEND", "COST")
HTTP_CLIENTS = ("httpx", "requests", "urllib", "aiohttp", "http.client")


def _imports(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.split(".")[0])
            out.update(f"{node.module.split('.')[0]}.{a.name}" for a in node.names)
    return out


def http_capable(root):
    """{module name} for every module that imports an HTTP client, plus every module
    that imports one of those. Two passes is enough for this repo's shape; a third
    would only add modules whose spend is already reachable through a named one."""
    imports, direct = {}, set()
    for path in python_files(root):
        try:
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
        except SyntaxError:
            continue
        mod = os.path.splitext(os.path.relpath(path, root))[0].replace(os.sep, ".")
        imports[mod] = _imports(tree)
        if any(c.split(".")[0] in imports[mod] for c in HTTP_CLIENTS):
            direct.add(mod)
    capable = set(direct)
    for mod, imps in imports.items():
        if any(d == i or d.endswith("." + i) or i.endswith("." + d.split(".")[-1])
               for d in direct for i in imps):
            capable.add(mod)
    return capable


def spend_shaped(root):
    """[(finding, module)] for budget-named constants defaulted inside a module that can
    make requests. This is the rule that survives an allowlist going stale."""
    capable = http_capable(root)
    out = []
    for f in findings(root):
        if not any(w in f[4].upper() for w in BUDGET_WORDS):
            continue
        mod = os.path.splitext(f[0])[0].replace("/", ".")
        owner = f[4].split(".")[0] if f[5] == "cross-module" else None
        if mod in capable or any(c == owner or c.endswith("." + owner) for c in capable
                                 if owner):
            out.append((f, mod))
    return out


def findings(root):
    """[(path, lineno, function, argument, default, 'cross-module'|'same-module')]"""
    out = []
    for path in python_files(root):
        try:
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
        except SyntaxError:
            continue
        consts = module_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            pairs = list(zip(args.posonlyargs + args.args, [None] * (
                len(args.posonlyargs) + len(args.args) - len(args.defaults)) + args.defaults))
            pairs += list(zip(args.kwonlyargs, args.kw_defaults))
            for arg, default in pairs:
                if default is None:
                    continue
                if isinstance(default, ast.Attribute) and default.attr.isupper():
                    src = f"{ast.unparse(default)}"
                    out.append((os.path.relpath(path, root).replace(os.sep, "/"), node.lineno,
                                node.name, arg.arg, src, "cross-module"))
                elif isinstance(default, ast.Name) and default.id in consts:
                    out.append((os.path.relpath(path, root).replace(os.sep, "/"), node.lineno,
                                node.name, arg.arg, default.id, "same-module"))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="include same-module constants")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    a = ap.parse_args(argv)
    found = findings(a.root)
    cross = [f for f in found if f[5] == "cross-module"]
    same = [f for f in found if f[5] == "same-module"]
    print(f"cross-module constants in default-argument position: {len(cross)}")
    for path, line, fn, arg, src, _k in cross:
        print(f"  {path}:{line}  {fn}({arg}={src})")
    spend = spend_shaped(a.root)
    print(f"budget-shaped AND in a module that can make requests: {len(spend)}"
          f"  <- the categorical rule, both classes")
    for f, mod in spend:
        print(f"  {f[0]}:{f[1]}  {f[2]}({f[3]}={f[4]})  [{f[5]}]")
    print(f"same-module constants in default-argument position: {len(same)}"
          f"{'' if a.all else '  (--all to list)'}")
    if a.all:
        for path, line, fn, arg, src, _k in same:
            print(f"  {path}:{line}  {fn}({arg}={src})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

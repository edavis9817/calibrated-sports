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

TWO CLASSES, and only one is dangerous:

  CROSS-MODULE  `def f(x=other.CONST)` - the attribute can be monkeypatched or
                reassigned at runtime and the function will never see it. This is
                the defect. The fix is `x=None` and `x = other.CONST if x is None`.
  SAME-MODULE   `def f(x=CONST)` where CONST is defined in the same file. Editing
                the literal changes both, and the two are visible in one diff, so
                it is a smell rather than a defect - unless a test monkeypatches it.

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
    print(f"same-module constants in default-argument position: {len(same)}"
          f"{'' if a.all else '  (--all to list)'}")
    if a.all:
        for path, line, fn, arg, src, _k in same:
            print(f"  {path}:{line}  {fn}({arg}={src})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""BRIEF 017 ITEM 1 - proving two model fingerprints predict the same thing.

`models.baseline._fingerprint()` hashes `models/baseline.py`,
`models/features.py` and `core/distributions.py`. Brief 016 moved the fee
arithmetic out of `core/distributions.py`, which bumped MODEL_VERSION from
`...679868a8549a` to `...a307952813e6` without changing a single predicted
quantity. The 935 week-1 predictions genuinely belong to BOTH fingerprints.

Re-running `paper_trade` to populate the new version would manufacture a fresh
lineage to paper over that, so instead the two versions are recorded as
equivalent - and the obvious objection to an equivalence table is that its rows
can be written on somebody's say-so, which is a WEAKER mechanism than the
re-run it replaces.

So the evidence is machine-checked, and it is checked at the level of meaning
rather than of text:

    For every top-level construct in the hashed fileset, compare the ABSTRACT
    SYNTAX TREE between the two commits. Every construct that is not a fee
    symbol must be present in both and unparse identically.

Comparing unparsed ASTs rather than raw bytes is deliberate. It ignores
comments, blank lines and formatting - which cannot change a prediction - and
it catches a renamed variable or a flipped sign inside an untouched-looking
function, which a diff of hunk headers would not. A row can only be written
when that check passes.
"""
import ast
import difflib
import subprocess

# The fileset `models.baseline._fingerprint()` hashes. Duplicated here rather
# than imported, because importing a constant INTO baseline.py would change the
# very hash this module exists to reason about. `tests/test_model_equivalence.py`
# asserts this tuple still matches the list inside `_fingerprint`'s source.
FINGERPRINT_FILES = ("models/baseline.py", "models/features.py",
                     "core/distributions.py")

# Symbols that describe trading COSTS, not predicted quantities. A change
# confined to these cannot move a forecast.
FEE_SYMBOLS = frozenset({
    "kalshi_fee", "edge_after_fees", "fee_per_contract", "series_multiplier",
    "series_of", "listed", "QUANTUM", "RATE", "DEFAULT_M", "SERIES_M",
    "SERIES_M_PREFIX",
})


# Symbols that compute the model's NAME rather than its forecasts. A change
# confined to these cannot move a prediction either - `_fingerprint` hashing
# its inputs differently changes what the model is CALLED and nothing else.
# Kept separate from FEE_SYMBOLS because the argument for each is different,
# and because an identity symbol is the one case where a change IN PLACE has
# to be allowed: normalising the hash means editing the hash function.
IDENTITY_SYMBOLS = frozenset({"_fingerprint", "model_version", "MODEL_VERSION"})


class NotEquivalent(Exception):
    """Raised when the fileset differs in a way that could move a prediction."""


def blob(commit: str, path: str) -> bytes:
    r = subprocess.run(["git", "show", f"{commit}:{path}"],
                       capture_output=True)
    return r.stdout if r.returncode == 0 else b""


def _checkout_bytes(commit: str, path: str) -> bytes:
    """A blob as it would sit ON DISK, not as git stores it.

    `_fingerprint()` hashes worktree bytes, and this repo has
    `core.autocrlf=true`, so git keeps LF and the checkout has CRLF - for SOME
    files. `models/baseline.py` happens to match its blob raw while
    `core/distributions.py` does not, so a blanket LF->CRLF transform is wrong
    in one direction and no transform is wrong in the other. Detect the
    convention per file by comparing the HEAD blob to what is actually on disk,
    then apply the same transform to the historical blob.
    """
    b = blob(commit, path)
    if not b:
        return b
    try:
        on_disk = open(path, "rb").read()
    except OSError:
        return b
    head = blob("HEAD", path)
    CRLF, LF = bytes([13, 10]), bytes([10])
    if head and head != on_disk and on_disk.replace(CRLF, LF) == head:
        return b.replace(CRLF, LF).replace(LF, CRLF)
    return b


def fingerprint_at(commit: str) -> str:
    """Reproduce `_fingerprint()` against a commit rather than the worktree."""
    import hashlib
    h = hashlib.sha256()
    for rel in FINGERPRINT_FILES:
        b = _checkout_bytes(commit, rel)
        if b:
            h.update(rel.encode())
            h.update(b)
    return h.hexdigest()[:12]


def fingerprint_normalised(commit: str) -> str:
    """The fingerprint under the CURRENT rule: line endings normalised first.

    This is exact and cheap, and it is what every version from brief 018
    onward is. Use it in preference to the legacy search below.
    """
    import hashlib
    h = hashlib.sha256()
    for rel in FINGERPRINT_FILES:
        b = blob(commit, rel)
        if b:
            h.update(rel.encode())
            h.update(b.replace(b"\r\n", b"\n"))
    return h.hexdigest()[:12]


def fingerprint_matches(commit: str, want: str) -> bool:
    """LEGACY. Could this commit have hashed to `want` on SOME checkout?

    ONLY for verifying versions minted BEFORE brief 018 normalised the hash.
    Those were taken over raw worktree bytes, so under `core.autocrlf=true`
    the answer depended on the checkout and not on the commit - which is why
    this has to search rather than compute.

    IT IS EXPONENTIAL IN THE NUMBER OF HASHED FILES and must not grow. The
    fix was to remove the degree of freedom, not to enumerate it: see
    `fingerprint_normalised` and `models.baseline._fingerprint`. If a future
    fileset makes this slow, that is a signal to stop calling it, not to
    optimise it. The normalised rule is tried first, so a post-018 version
    never reaches the search at all.

    `_fingerprint()` hashes worktree bytes, so the answer depends on line
    endings - and under `core.autocrlf=true` those are not a property of the
    commit. `core/distributions.py` was LF on disk before brief 016 and CRLF
    after, because 016 rewrote it, so no single convention reproduces both
    fingerprints. Rather than guess, try every combination: with three files
    there are eight, and finding one that matches is a real verification that
    the recorded version could have come from this commit.

    That the check has to look like this is itself worth knowing. A fingerprint
    over raw file bytes is not reproducible across machines with different
    autocrlf settings - it identifies a checkout, not a commit.
    """
    import hashlib
    import itertools
    if fingerprint_normalised(commit) == want:
        return True            # post-018: exact, no search needed
    CRLF, LF = bytes([13, 10]), bytes([10])
    raw = [blob(commit, rel) for rel in FINGERPRINT_FILES]
    for combo in itertools.product((False, True), repeat=len(FINGERPRINT_FILES)):
        h = hashlib.sha256()
        for rel, b, to_crlf in zip(FINGERPRINT_FILES, raw, combo):
            if not b:
                continue
            body = b.replace(CRLF, LF)
            if to_crlf:
                body = body.replace(LF, CRLF)
            h.update(rel.encode())
            h.update(body)
        if h.hexdigest()[:12] == want:
            return True
    return False


def _top_level(src: str):
    """{name -> unparsed source} for every top-level construct, plus the set
    of imported names. Comments and formatting are discarded by unparsing."""
    tree = ast.parse(src)
    out, imports = {}, set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                imports.add(a.asname or a.name.split(".")[0])
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = ast.unparse(node)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = ast.unparse(node)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = ast.unparse(node)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue                      # module docstring
    return out, imports


def compare(old_commit: str, new_commit: str) -> dict:
    """Structural comparison of the hashed fileset between two commits."""
    changed, added, removed, diffs = {}, {}, {}, []
    for rel in FINGERPRINT_FILES:
        a, b = blob(old_commit, rel), blob(new_commit, rel)
        if a == b:
            continue
        diffs.append("".join(difflib.unified_diff(
            a.decode("utf-8", "replace").splitlines(keepends=True),
            b.decode("utf-8", "replace").splitlines(keepends=True),
            fromfile=f"{old_commit}:{rel}", tofile=f"{new_commit}:{rel}")))
        oa, ia = _top_level(a.decode("utf-8", "replace"))
        ob, ib = _top_level(b.decode("utf-8", "replace"))
        for name in set(oa) | set(ob):
            if name not in ob:
                removed.setdefault(rel, []).append(name)
            elif name not in oa:
                added.setdefault(rel, []).append(name)
            elif oa[name] != ob[name]:
                changed.setdefault(rel, []).append(name)
        for name in ib - ia:
            added.setdefault(rel, []).append(f"import:{name}")
        for name in ia - ib:
            removed.setdefault(rel, []).append(f"import:{name}")
    return {"old_commit": old_commit, "new_commit": new_commit,
            "old_fingerprint": fingerprint_at(old_commit),
            "new_fingerprint": fingerprint_at(new_commit),
            "changed": changed, "added": added, "removed": removed,
            "diff": "\n".join(diffs)}


def assert_fee_only(cmp: dict) -> dict:
    """THE ASSERTION. Every construct that moved must be a fee symbol.

    A construct that CHANGED (rather than being added or removed) fails
    outright even if it is a fee symbol - a fee function still living in the
    hashed fileset with different behaviour is exactly the case this table
    must not wave through.
    """
    allowed = FEE_SYMBOLS | IDENTITY_SYMBOLS
    offenders = []
    for rel, names in cmp["changed"].items():
        # Only an IDENTITY symbol may change in place. A fee function that
        # changed behaviour while still living in the hashed fileset is
        # exactly what this table must not wave through, and a prediction
        # function obviously is.
        offenders += [f"{rel}: {n} CHANGED in place" for n in names
                      if n not in IDENTITY_SYMBOLS]
    for bucket in ("added", "removed"):
        for rel, names in cmp[bucket].items():
            for n in names:
                bare = n.split(":", 1)[1] if n.startswith("import:") else n
                if bare not in allowed:
                    offenders.append(f"{rel}: {n} {bucket}")
    if offenders:
        raise NotEquivalent(
            "the hashed fileset differs in ways that are not fee-only:\n  "
            + "\n  ".join(offenders))
    if not (cmp["changed"] or cmp["added"] or cmp["removed"]):
        raise NotEquivalent(
            "nothing changed between these commits - an equivalence row would "
            "be recording a fact about nothing")
    return cmp

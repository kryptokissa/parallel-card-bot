"""Fail if the bundle carries anything that should not ship.

PUBLISHING.md §1 tells the agent to read the bundle listing and fail the publish
on a test file, a fixture, a cache or a scratch file. That works right up until
someone is in a hurry. This makes it a gate instead of an instruction.

Tests in the bundle caused five consecutive review rejections on The Marsh: a
reviewer reads the bundle, and a test file full of stub addresses and fake
endpoints reads exactly like the real thing.

It also scans the text files that ship for patterns that fail review. The first
one is there because it actually happened: the `path init` applet scaffold posts
to a wildcard target origin, and Grid 0.1.0 was sent to human review for exactly
that. The scaffold is not publishable as it stands, and an eyeball is a poor
place to keep that fact.

One exemption, and it is not optional. `tests/evals/` and `tests/fixtures/`
inside the path directory are required by the SDK, not leftovers:
`paths/doctor.py` errors with "Pipeline paths must ship at least 3 evals" when
`<path>/tests/evals` is missing, `paths/evaluator.py` raises "Missing
tests/evals", and `path init --template pipeline` scaffolds both. Assay's
published bundle ships two of them. A blanket ban on `tests/` would fail a
publish that is correct — so those two directories are allowed, for everything
except Python files, which never belong in either.
"""
import fnmatch, re, sys, zipfile

# Required by the SDK; allowed to ship. Python is still banned inside them.
ALLOWED_PREFIXES = ("tests/evals/", "tests/fixtures/")

# (glob, why it must not ship)
BANNED = [
    ("test_*.py", "test file"),
    ("*_test.py", "test file"),
    ("*/test_*.py", "test file"),
    ("*/*_test.py", "test file"),
    ("conftest.py", "pytest fixture module"),
    ("*/conftest.py", "pytest fixture module"),
    ("*__pycache__*", "bytecode cache"),
    ("*.pyc", "compiled bytecode"),
    ("*.pyo", "compiled bytecode"),
    ("*fixture*", "test fixture"),
    ("*sample*", "test fixture"),
    ("*.bak", "backup file"),
    ("*.orig", "merge leftover"),
    ("*.rej", "merge leftover"),
    ("*~", "editor backup"),
    ("*.swp", "editor swap file"),
    ("*.DS_Store", "macOS metadata"),
    (".env", "environment file"),
    ("*/.env", "environment file"),
    ("*.pem", "key material"),
    ("*.key", "key material"),
    ("*.sqlite", "local database"),
    ("*.log", "log file"),
    (".build/*", "generated skill exports"),
    ("*/.build/*", "generated skill exports"),
    ("*.wayfinder_runs*", "local run state"),
]
# Directory segments that should never appear, outside the allowed prefixes.
BANNED_DIRS = ("tests", "test", ".pytest_cache", ".venv", "node_modules")

# Content patterns that fail review. Scanned in the text files that ship.
SCANNED_SUFFIXES = (".js", ".mjs", ".ts", ".html", ".htm")
CONTENT_PATTERNS = [
    (
        re.compile(r"""postMessage\s*\([^)]*?['"]\*['"]"""),
        "postMessage to a wildcard target origin — capture the parent origin "
        "from the handshake event and reply to that origin only",
    ),
    (
        re.compile(r"""\bon(?:click|load|error|mouseover)\s*=\s*['"]"""),
        "inline event handler in markup",
    ),
    (
        re.compile(r"""\beval\s*\("""),
        "eval() in shipped code",
    ),
    (
        re.compile(r"""document\.write\s*\("""),
        "document.write() in shipped code",
    ),
]


def offence(name):
    """Why `name` must not ship, or None."""
    if name.startswith(ALLOWED_PREFIXES):
        if name.endswith((".py", ".pyc", ".pyo")):
            return "Python file inside an SDK eval directory"
        return None
    for pattern, why in BANNED:
        if fnmatch.fnmatch(name, pattern):
            return why
    for segment in name.split("/")[:-1]:
        if segment in BANNED_DIRS:
            return f"inside a '{segment}/' directory"
    return None


def strip_comments(text):
    """Blank out comments so the scan reads code, not prose about code.

    Necessary rather than fastidious: The Marsh's applet documents its origin
    lock with a comment naming the wildcard call it avoids, and that bundle is
    published and approved. A scan that fails a correct publish is worse than no
    scan, and a scan fooled by a comment is not one either.

    Tracks quote state as it walks, so a `//` inside a string — every https URL
    has one — does not swallow the rest of the line.
    """
    out = []
    i, n = 0, len(text)
    quote = None
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if quote:
            out.append(ch)
            if ch == "\\":
                if i + 1 < n:
                    out.append(nxt)
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if text.startswith("<!--", i):
            end = text.find("-->", i + 4)
            i = n if end == -1 else end + 3
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def content_offences(archive, name):
    """Review-failing patterns inside a shipped text file, comments excluded."""
    if not name.endswith(SCANNED_SUFFIXES):
        return []
    body = strip_comments(archive.read(name).decode("utf-8", "ignore"))
    return [why for pattern, why in CONTENT_PATTERNS if pattern.search(body)]


def main(argv):
    """0 clean, 1 something must not ship, 2 called wrong."""
    if len(argv) != 2:
        print("usage: check_bundle_contents.py <bundle.zip>")
        return 2
    archive = zipfile.ZipFile(argv[1])
    names = archive.namelist()
    bad = [(n, why) for n in names if (why := offence(n))]
    for n in names:
        for why in content_offences(archive, n):
            bad.append((n, why))

    print(f"{len(names)} entries in {argv[1]}")
    for n in sorted(names):
        print(f"  {n}")
    if bad:
        print("\nMUST NOT SHIP:")
        for n, why in sorted(set(bad)):
            print(f"  {n}: {why}")
        return 1
    print("\nclean: nothing in the bundle that should not ship")
    return 0


# Guarded so the module is importable: without this, importing it to test
# `offence` runs the CLI and exits the interpreter.
if __name__ == "__main__":
    sys.exit(main(sys.argv))

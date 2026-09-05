"""Fail if the bundle carries any origin other than the production API."""
import re, sys, zipfile

ALLOWED = ("https://wayfinder.ai",)
z = zipfile.ZipFile(sys.argv[1])
pat = re.compile(r'(?:https?|ws|wss|ftp)://[^\s"\'<>)\\`]+')
bad, scanned = [], 0
for n in z.namelist():
    if not n.endswith((".py", ".md", ".json", ".yaml", ".yml", ".html",
                       ".js", ".ts", ".css", ".txt", ".toml", ".cfg")):
        continue
    scanned += 1
    for m in pat.findall(z.read(n).decode("utf-8", "ignore")):
        if not m.startswith(ALLOWED):
            bad.append((n, m))
print(f"scanned {scanned} text files in {sys.argv[1]}")
if bad:
    print("NON-ALLOWLISTED ORIGINS:")
    for n, m in sorted(set(bad)):
        print(f"  {n}: {m}")
    sys.exit(1)
print("clean: no origin other than", ALLOWED[0])

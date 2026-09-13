# Publishing a path — runbook

Command-first. Every command and every response field below was run
against the live API while publishing The Marsh `0.2.1`. Read
`BUILDING_PATHS.md` for *why* these steps exist; this file is *what to
run*.

---

## 0. Toolchain

The CLI is **`wayfinder path ...`**, not `path ...`. The published wheel
imports `mcp` at module scope but omits it from its metadata, so a clean
install needs both packages or every entry point fails with
`ModuleNotFoundError: No module named 'mcp'`:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python 'wayfinder-paths==0.11.0' 'mcp>=1.10.1,<2'
export WAYFINDER_API_KEY=wk_...
```

Python must be 3.12 — the runtime pin is `>=3.12,<3.13`.

Confirm before going further:

```bash
.venv/bin/wayfinder path version
```

---

## 1. Pre-publish gate

Run all four. Do not skip one because the last publish passed.

```bash
cd paths/<slug>

# 1. tests live OUTSIDE the path dir, so run them from the parent
(cd .. && ../.venv/bin/python -m pytest <slug>-tests -q)

# 2. normalize metadata and regenerate skill exports
wayfinder path fmt

# 3. validate — must report errors: [] and warnings: []
wayfinder path doctor

# 4. build the bundle
wayfinder path build

# 5. scan the BUILT bundle, never the source tree
python ../tools/check_bundle_origins.py dist/bundle.zip
```

`doctor` returns JSON; the shape you want is:

```json
{"ok": true, "result": {"slug": "...", "version": "...", "errors": [], "warnings": []}}
```

`build` returns `bundle_sha256` and `rendered_hosts`. Keep the sha — it
appears again in the publish response and in the install step, and a
mismatch means you published something other than what you tested.

### Inspect the bundle before you ship it

```bash
python -c "
import zipfile
n = zipfile.ZipFile('dist/bundle.zip').namelist()
print(len(n), 'entries')
[print(' ', x) for x in sorted(n)]
"
```

Fail the publish yourself if you see **any** of:

- a `test_*.py` or a fixture — tests in the bundle caused five
  consecutive review rejections on The Marsh
- `__pycache__/` or a `.pyc`
- a scratch, backup or `.bak` file
- an origin the check flagged (placeholders and `127.0.0.1` count)

The Marsh `0.2.1` shipped 22 entries: `wfpath.yaml`, `strategy.py`,
`README.md`, `.gitignore`, `engine/` (11), `game/` (2), `scripts/` (1),
`skill/instructions.md`, `applet/` (3).

---

## 2. Version

Bump `version:` in `wfpath.yaml`. A version is **immutable once
published** — you cannot re-push the same number, so a bad publish costs
a version bump, not a retraction.

Also check `runtime.version` in `wfpath.yaml` points at a **published**
SDK release. `wayfinder path init` writes whatever is installed in your
shell, which is routinely ahead of what installers can resolve:

```bash
pip index versions wayfinder-paths
```

---

## 3. Publish

```bash
wayfinder path publish \
  --bonded \
  --owner-wallet 0x555a0aaeb4820d2de210cb000f247db882a6613f \
  --risk-tier execution
```

Full flag surface:

| flag | meaning |
|---|---|
| `--path` | path dir, default `.` |
| `--out` | bundle output, default `dist/bundle.zip` |
| `--api-url` | override the Paths API base URL |
| `--source` | optional `source.zip` to upload alongside |
| `--bonded` / `--unbonded` | default is **unbonded** |
| `--owner-wallet` | owner wallet for bonded metadata and contract args |
| `--risk-tier` | `read_only` \| `interactive` \| `execution` |

**`--owner-wallet` is mandatory once a path is bonded.** Bonding
transfers ownership to the bonding wallet, so every later publish must
name it or the version will not attach to the existing path.

`publish` re-builds before uploading, so the bundle it ships is the one
on disk at that moment — which is why the gate in §1 runs first.

### Reading the response

```json
{
  "pathId": "0x29c10075...c820ba6cac",
  "bundleSha256": "225fe1b2...b12d313d",
  "requestedRiskTier": "execution",
  "effectiveRiskTier": "execution",
  "minOwnerBond": "10300000000000000000000",
  "manageUrl": "https://strategies.wayfinder.ai/paths/<slug>/manage?version=<v>",
  "publishState": "manage_ready",
  "trustState": "active",
  "reviewState": "queued",
  "nextAction": "wait_for_review",
  "slugPermanent": true
}
```

Check three things:

1. **`effectiveRiskTier` == `requestedRiskTier`.** If the API downgraded
   your tier, an execution path will not be allowed to execute.
2. **`bundleSha256`** matches what `build` printed.
3. **`reviewState`** is `queued`. Anything else, read `nextAction`.

Bond amounts are wei-scaled: `10300000000000000000000` = **10,300 PROMPT**
for the execution tier.

---

## 4. Wait for review

```bash
wayfinder path info --slug <slug>
```

Two things about this command that will mislead you:

- It lists **only public versions.** A version you just published is
  absent until it passes. Absence is not failure — it is "not yet".
- The useful signal is each version's `modified` timestamp.

Healthy: `modified` advances within roughly 15–30 minutes of `created`.

Stalled: `created` and `modified` are seconds apart and stay frozen. The
Marsh `0.2.0` sat at `created 13:04:05.999634Z` / `modified
13:04:09.716370Z` for over twelve hours and never went public. If a
version looks like that after a few hours, it is stuck — bump the version
and republish rather than waiting, and mention the timestamps if you
raise it with the devs.

Extract the version table without reading 2,000 lines of JSON:

```bash
wayfinder path info --slug <slug> > info.json
python - <<'PY'
import json
d = json.load(open("info.json"))
def dig(o, k):
    if isinstance(o, dict):
        if k in o: return o[k]
        for v in o.values():
            r = dig(v, k)
            if r is not None: return r
for v in dig(d, "versions") or []:
    print(f"{v['version']:8} status={v['status']:10} "
          f"actionable={v['is_actionable']} "
          f"created={v['created']} modified={v['modified']}")
PY
```

---

## 5. The approval gate

Review passing is **not** the end. The sequence is:

```
publish → automated review → human Blockdash approval (is_actionable)
        → upgrade_bond (announce) → upgrade_probation → promote_upgrade
```

`is_actionable: false` means the version exists and is public but is not
yet cleared to act. Bonding and promotion are **not** in the CLI — they
happen at the `manageUrl` from the publish response, signed by the owner
wallet.

**These steps are the repo owner's, always. Real money is bonded. An
agent builds up to the gate, reports the `manageUrl`, and stops.**

---

## 6. Verify what actually shipped

After a version goes public, confirm the published artefact is the code
you think it is — twice on The Marsh I "fixed" a review finding that the
published bundle already contained.

```bash
wayfinder path install --slug <slug> --version <v> --dir ./verify
python ../tools/check_bundle_origins.py ./verify/.../bundle.zip
```

`install` verifies the bundle SHA-256 by default. Never pass
`--no-verify` to work around a download problem — retry instead; the API
throws intermittent 500s and read timeouts on bundle downloads and
metadata reads alike.

To activate into a host after install, `--host` and `--scope` must be
given **together** — `--host` alone silently skips activation:

```bash
wayfinder path install --slug <slug> --host opencode --scope project
```

Hosts: `claude`, `opencode`, `codex`, `openclaw` (plus `portable` as a
render target).

Updating an existing install tracks the **live bonded version**, so an
unpromoted version will not land even though it is public:

```bash
wayfinder path update --dir .wayfinder/paths          # live bonded version
wayfinder path update --version <v>                   # pin a public version
```

---

## 7. Failure playbook

| symptom | cause | fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'mcp'` | wheel omits `mcp` from metadata | `pip install 'mcp>=1.10.1,<2'` |
| review rejects citing test/fixture content | tests are inside the path dir | move them to `<slug>-tests/`; a gitignore will not help |
| review rejects citing an origin | any non-`wayfinder.ai` URL in the bundle | remove it — `example.invalid` and `127.0.0.1` are both rejected |
| installers cannot resolve the runtime | `runtime.version` pins an unpublished SDK | set it to a version `pip index versions` lists |
| version never goes public, `modified` frozen | review stalled server-side | bump version, republish |
| new version does not attach to the path | bonded path published without `--owner-wallet` | republish with the owner wallet |
| bundle download 500 / metadata timeout | flaky API | retry with backoff; never `--no-verify` |
| `wayfinder path info` omits your version | it lists public versions only | wait, or check `manageUrl` |

---

## 8. One-shot checklist

```
[ ] tests green, run from OUTSIDE the path dir
[ ] version bumped in wfpath.yaml
[ ] runtime.version is a published SDK release
[ ] fmt clean
[ ] doctor: errors [] warnings []
[ ] build succeeded; bundle_sha256 recorded
[ ] origin check clean against dist/bundle.zip
[ ] bundle listing eyeballed: no tests, no __pycache__, no scratch
[ ] publish with --owner-wallet if the path is bonded
[ ] effectiveRiskTier == requestedRiskTier
[ ] bundleSha256 matches the build
[ ] reviewState queued
[ ] manageUrl reported to the owner — STOP HERE
```

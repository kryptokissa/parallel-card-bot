# Repo map

Two unrelated things live here.

- `src/`, `resources/` — the original Parallel card bot.
- `paths/` — Wayfinder paths. Everything below concerns these.

## Before starting or changing a Wayfinder path

**Read `paths/BUILDING_PATHS.md` first.** It is the accumulated cost of
shipping The Marsh: packaging traps that caused five consecutive review
rejections, the runtime model (paths decide, agents execute), quote
fields that are named backwards, the Hyperliquid order surface, and the
publish/bond lifecycle. Skipping it means rediscovering all of it.

## Layout rule

Anything inside a path directory ships in the published bundle. So:

```
paths/
  <slug>/          the path — only what ships
  <slug>-tests/    tests and fixtures, deliberately outside the bundle
  tools/           build-time checks, shared across paths
```

Point pytest at the sibling `-tests` directory. Never put tests, fixtures
or scratch files inside `<slug>/`.

## Before every publish

```bash
wayfinder path fmt && wayfinder path doctor && wayfinder path build
python paths/tools/check_bundle_origins.py paths/<slug>/dist/bundle.zip
```

The origin check must run against the **built bundle**, not the source
tree. A publish with a non-`wayfinder.ai` origin in it — placeholders and
loopback addresses included — gets rejected.

## Toolchain

The published SDK omits `mcp` from its metadata while importing it at
module scope, so a clean install needs both:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python 'wayfinder-paths==0.11.0' 'mcp>=1.10.1,<2'
```

The CLI is `wayfinder path ...`, not `path ...`.

## Money

Publishing, bonding and promotion are the repo owner's decisions, always.
Build up to the gate and stop there.

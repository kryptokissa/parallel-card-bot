"""The declared component must actually run, from a clean subprocess.

Arguments are literal lists and the interpreter is ``sys.executable``: no
shell, no string interpolation, nothing assembled from a variable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PACK_ROOT = Path(__file__).resolve().parents[1] / "evm-token-due-diligence"
COMPONENT = PACK_ROOT / "scripts" / "main.py"
VALIDATOR = PACK_ROOT / "skill" / "scripts" / "assay_validate.py"


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        cwd=str(cwd or PACK_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_the_declared_component_exists_where_the_manifest_says():
    """Parsed, not string-matched: `wayfinder path fmt` renormalises quoting."""
    yaml = pytest.importorskip("yaml", reason="manifest parsing needs a YAML reader")

    manifest = yaml.safe_load((PACK_ROOT / "wfpath.yaml").read_text(encoding="utf-8"))
    components = manifest["components"]
    assert len(components) == 1
    declared = PACK_ROOT / components[0]["path"]
    assert declared == COMPONENT
    assert declared.is_file()
    assert manifest["skill"]["runtime"]["component"] == components[0]["id"]


def test_the_component_emits_a_read_only_state_snapshot():
    result = _run(["scripts/main.py"])
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["path"] == "evm-token-due-diligence"
    assert payload["mode"] == "read_only"
    assert payload["declarations"]["no_real_signing"] is True
    assert payload["declarations"]["no_broadcast"] is True
    assert len(payload["dimensions"]) == 11
    assert "eth_sendRawTransaction" not in payload["capabilities"]["forbidden_methods"]
    assert "eth_sign" in payload["capabilities"]["forbidden_methods"]


def test_the_component_lists_and_renders_examples():
    listed = _run(["scripts/main.py", "example"])
    assert listed.returncode == 0, listed.stderr
    names = json.loads(listed.stdout)["examples"]
    assert len(names) == 6

    rendered = _run(["scripts/main.py", "example", "locked-canonical-removable-side-pool"])
    assert rendered.returncode == 0, rendered.stderr
    report = json.loads(rendered.stdout)
    assert report["synthetic"] is True
    assert report["verdict"]["call"] == "NO-GO"


def test_an_unknown_example_fails_loudly():
    result = _run(["scripts/main.py", "example", "no-such-example"])
    assert result.returncode == 2
    assert "unknown example" in result.stderr


def test_offline_subcommands_need_no_endpoint():
    pool = _run(
        [
            "scripts/main.py",
            "pool",
            "v3",
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            "--factory",
            "0x1F98431c8aD98523631AE4a59f267346ea31F984",
            "--fee",
            "500",
        ]
    )
    assert pool.returncode == 0, pool.stderr
    assert json.loads(pool.stdout)["candidate_address"] == (
        "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"
    )

    scan = _run(["scripts/main.py", "scan", "--hex", "0x6340c10f1900"])
    assert scan.returncode == 0, scan.stderr
    assert "supply_change" in json.loads(scan.stdout)["capabilities"]


def test_a_target_without_a_chain_is_refused_before_any_network_call():
    # Non-runtime local test fixture: a closed loopback port, chosen so the
    # test fails if the argument check ever stops short-circuiting and the
    # command tries to dial out.
    result = _run(
        [
            "scripts/main.py",
            "packet",
            "0x1111111111111111111111111111111111111111",
            "--rpc",
            "http://127.0.0.1:1",
        ]
    )
    assert result.returncode == 2
    assert "must name its chain" in result.stderr


def test_the_validator_runs_standalone_from_its_own_location(tmp_path):
    from evm_dd.examples import fixed_supply_with_severe_exit_degradation
    from evm_dd.manifest import Declarations, build_manifest

    report = fixed_supply_with_severe_exit_degradation()
    report.source_id = "entry-test"
    manifest = build_manifest(
        report.packet, declarations=Declarations(), report_id="entry-test"
    )
    manifest_path = tmp_path / "manifest.json"
    report_path = tmp_path / "report.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report_path.write_text(json.dumps(report.to_dict()), encoding="utf-8")

    result = _run(
        [
            str(VALIDATOR),
            str(manifest_path),
            "--report",
            str(report_path),
        ],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("PASS")
    assert "does not validate RPC honesty" in result.stdout


def test_the_tools_resolve_their_own_root_in_the_rendered_export_layout(tmp_path):
    """A rendered export puts the package under ``path/`` and the scripts at
    the export root. Nothing is configured, so the scripts must find the
    package from their own location in both layouts."""
    import json
    import shutil

    export = tmp_path / "export"
    (export / "scripts").mkdir(parents=True)
    (export / "path").mkdir()
    shutil.copytree(PACK_ROOT / "evm_dd", export / "path" / "evm_dd")
    shutil.copytree(PACK_ROOT / "scripts", export / "path" / "scripts")
    shutil.copy2(VALIDATOR, export / "scripts" / "assay_validate.py")

    from evm_dd.examples import fixed_supply_with_severe_exit_degradation
    from evm_dd.manifest import Declarations, build_manifest

    report = fixed_supply_with_severe_exit_degradation()
    report.source_id = "export-layout"
    (tmp_path / "m.json").write_text(
        json.dumps(build_manifest(report.packet, declarations=Declarations(), report_id="export-layout")),
        encoding="utf-8",
    )
    (tmp_path / "r.json").write_text(json.dumps(report.to_dict()), encoding="utf-8")

    validated = _run(
        [str(export / "scripts" / "assay_validate.py"), "m.json", "--report", "r.json"],
        cwd=tmp_path,
    )
    assert validated.returncode == 0, validated.stdout + validated.stderr

    component = _run([str(export / "path" / "scripts" / "main.py"), "state"], cwd=tmp_path)
    assert component.returncode == 0, component.stderr
    assert json.loads(component.stdout)["mode"] == "read_only"

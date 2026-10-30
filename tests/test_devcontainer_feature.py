"""Tests verifying the OCI Dev Container Feature manifest, installer script, test suite, and CI workflow (INV-DEVCONTAINER-FEATURE-CONFORMANCE)."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import orjson

from eidos.core.spec.parser import safe_load_yaml


def test_devcontainer_feature_manifest_and_installer_conformance(tmp_path: Path) -> None:
    """Verify INV-DEVCONTAINER-FEATURE-CONFORMANCE: devcontainer-feature.json, install.sh, test scenarios, pyproject.toml sdist exclusion, and CI workflow publishing."""
    repo_root = Path(__file__).resolve().parent.parent
    feature_dir = repo_root / "devcontainer-features" / "src" / "eidos"
    test_dir = repo_root / "devcontainer-features" / "test" / "eidos"

    # 1. Verify devcontainer-feature.json structure and option definitions
    manifest_path = feature_dir / "devcontainer-feature.json"
    assert manifest_path.is_file(), "devcontainer-feature.json is missing"
    manifest = orjson.loads(manifest_path.read_bytes())

    assert manifest["id"] == "eidos"
    assert manifest["version"] == "1.0.0"
    assert manifest["name"] == "Eidos CLI (eidos-ai)"
    assert "options" in manifest

    options = manifest["options"]
    assert set(options.keys()) == {"version", "semantic", "pythonVersion"}
    assert options["version"]["type"] == "string"
    assert options["version"]["default"] == "latest"
    assert options["semantic"]["type"] == "boolean"
    assert options["semantic"]["default"] is False
    assert options["pythonVersion"]["type"] == "string"
    assert options["pythonVersion"]["default"] == "3.14"

    vscode_exts = manifest.get("customizations", {}).get("vscode", {}).get("extensions", [])
    assert "asciidoctor.asciidoctor-vscode" in vscode_exts
    assert "redhat.vscode-yaml" in vscode_exts

    installs_after = manifest.get("installsAfter", [])
    assert "ghcr.io/devcontainers/features/common-utils" in installs_after
    assert "ghcr.io/devcontainer-community/devcontainer-features/astral.sh-uv" in installs_after

    # 2. Verify README.md and test scenarios exist and are executable
    readme_path = feature_dir / "README.md"
    assert readme_path.is_file(), "Feature README.md is missing"
    assert "ghcr.io/mchawa/eidos/eidos:1" in readme_path.read_text(encoding="utf-8")

    install_sh = feature_dir / "install.sh"
    test_sh = test_dir / "test.sh"
    with_semantic_sh = test_dir / "with_semantic.sh"
    scenarios_json = test_dir / "scenarios.json"

    for script in (install_sh, test_sh, with_semantic_sh):
        assert script.is_file(), f"Missing script: {script}"
        mode = script.stat().st_mode
        assert bool(mode & stat.S_IXUSR), f"Script {script.name} must be executable"
        syntax_check = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert syntax_check.returncode == 0, f"Bash syntax error in {script}: {syntax_check.stderr}"

    scenarios = orjson.loads(scenarios_json.read_bytes())
    assert "with_semantic" in scenarios
    assert scenarios["with_semantic"]["features"]["eidos"]["semantic"] is True

    # 3. Verify install.sh behavioral guardrails (non-root rejection and semantic Python <3.15 clamping)
    install_text = install_sh.read_text(encoding="utf-8")
    assert 'UV_TOOL_BIN_DIR="/usr/local/bin"' in install_text
    assert 'UV_TOOL_DIR="/usr/local/share/uv/tools"' in install_text
    assert 'UV_PYTHON_INSTALL_DIR="/usr/local/share/uv/python"' in install_text
    assert "uv tool install --no-cache" in install_text

    if os.geteuid() != 0:
        non_root_res = subprocess.run(
            ["bash", str(install_sh)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert non_root_res.returncode != 0
        assert "must be run as root" in non_root_res.stderr

    # Simulate root execution with stubbed id/uv/chmod to verify semantic Python 3.15 -> 3.14 clamping and version pinning
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uv_log = tmp_path / "uv.log"

    (fake_bin / "id").write_text("#!/usr/bin/env bash\necho 0\n", encoding="utf-8")
    (fake_bin / "uv").write_text(
        f'#!/usr/bin/env bash\necho "$@" >> "{uv_log}"\n',
        encoding="utf-8",
    )
    (fake_bin / "chmod").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    for stub in ("id", "uv", "chmod"):
        (fake_bin / stub).chmod(0o755)

    patched_install = tmp_path / "install_test.sh"
    patched_text = (
        install_text.replace('"/usr/local/bin"', f'"{tmp_path / "local_bin"}"')
        .replace('"/usr/local/share/uv/tools"', f'"{tmp_path / "uv_tools"}"')
        .replace('"/usr/local/share/uv/python"', f'"{tmp_path / "uv_python"}"')
        .replace("/usr/local/bin/eidos --version", "echo 'eidos 0.5.0'")
        .replace("/usr/local/share/uv /usr/local/bin/eidos", str(tmp_path))
    )
    patched_install.write_text(patched_text, encoding="utf-8")
    patched_install.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["VERSION"] = "v0.5.0"
    env["SEMANTIC"] = "true"
    env["PYTHONVERSION"] = "3.15"

    sim_res = subprocess.run(
        ["bash", str(patched_install)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert sim_res.returncode == 0, f"Simulated install failed: {sim_res.stderr}\n{sim_res.stdout}"
    assert "clamping to 3.14" in sim_res.stdout
    logged_calls = uv_log.read_text(encoding="utf-8").splitlines()
    assert "python install 3.14" in logged_calls
    assert "tool install --no-cache --force --python 3.14 eidos-ai[semantic]==0.5.0" in logged_calls

    # 4. Verify pyproject.toml excludes /devcontainer-features and /.devcontainer from sdist
    pyproject_text = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    assert '"/devcontainer-features"' in pyproject_text
    assert '"/.devcontainer"' in pyproject_text

    # 5. Verify .github/workflows/ci.yml wires publish-devcontainer-feature via devcontainers/action@v1
    ci_workflow = safe_load_yaml((repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    jobs = ci_workflow.get("jobs", {})
    assert "publish-devcontainer-feature" in jobs, "publish-devcontainer-feature job missing from ci.yml"
    pub_job = jobs["publish-devcontainer-feature"]
    assert "publish-to-pypi" in pub_job.get("needs", [])
    assert pub_job.get("permissions", {}).get("packages") == "write"
    assert pub_job.get("permissions", {}).get("contents") == "write"

    steps = pub_job.get("steps", [])
    devcontainer_steps = [s for s in steps if str(s.get("uses", "")).startswith("devcontainers/action@")]
    assert len(devcontainer_steps) == 1
    with_cfg = devcontainer_steps[0].get("with", {})
    assert str(with_cfg.get("publish-features")).lower() == "true"
    assert with_cfg.get("base-path-to-features") == "./devcontainer-features/src"


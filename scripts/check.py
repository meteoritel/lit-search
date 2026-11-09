"""统一执行静态检查、离线回归、构建与分发验收。"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source_version() -> str:
    module = ast.parse((ROOT / "src/litsearch.py").read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        ):
            return str(ast.literal_eval(node.value))
    raise AssertionError("源码缺少 __version__")


def check_distribution() -> None:
    version = source_version()
    wheel = ROOT / "dist" / f"litsearch-{version}-py3-none-any.whl"
    sdist = ROOT / "dist" / f"litsearch-{version}.tar.gz"
    with zipfile.ZipFile(wheel) as archive:
        metadata = archive.read(f"litsearch-{version}.dist-info/METADATA").decode()
        assert f"Version: {version}" in metadata.splitlines()
        assert "License-Expression: MIT" in metadata.splitlines()
        assert not any(line.startswith("Requires-Dist:") and "extra ==" not in line
                       for line in metadata.splitlines())
        assert "litsearch.py" in archive.namelist()
    with tarfile.open(sdist) as archive:
        names = set(archive.getnames())
        for relative in ("tests/conftest.py", "tests/fixtures/openalex_page1.json",
                         "tests/fixtures/retrieval_replay.json", "tests/fixtures/retrieval_gold.json",
                         "scripts/check.py", "docs/python.md", "docs/usage.md", "uv.lock",
                         "examples/marine-timeseries.toml", "examples/marine-lna-hna.toml"):
            assert f"litsearch-{version}/{relative}" in names, relative
        assert not any(name.startswith((f"litsearch-{version}/docs/archive/",
                                        f"litsearch-{version}/docs/plans/")) for name in names)

    with tempfile.TemporaryDirectory(prefix="litsearch-distribution-") as directory:
        temporary = Path(directory)
        environment = temporary / "environment"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(environment)], check=True)
        binaries = environment / ("Scripts" if os.name == "nt" else "bin")
        python = binaries / ("python.exe" if os.name == "nt" else "python")
        command = binaries / ("litsearch.exe" if os.name == "nt" else "litsearch")
        subprocess.run(["uv", "pip", "install", "--offline", "--python", str(python),
                        "--no-deps", str(wheel)], check=True)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("LITSEARCH_", "OPENALEX_", "CROSSREF_", "PYTHON"))}
        env.update(APPDATA=str(temporary / "appdata"), HOME=str(temporary / "home"),
                   USERPROFILE=str(temporary / "home"))
        result = subprocess.run([str(command), "--version"], cwd=temporary, env=env,
                                check=True, capture_output=True, text=True, encoding="utf-8")
        assert result.stdout.strip() == f"litsearch {version}"
        imported = subprocess.run([str(python), "-I", "-c",
                                  "import litsearch; print(litsearch.__file__)"], cwd=temporary,
                                 check=True, capture_output=True, text=True, encoding="utf-8")
        assert Path(imported.stdout.strip()).is_relative_to(environment)
        explanation = subprocess.run([str(command), "q", "--explain"], cwd=temporary, env=env,
                                     check=True, capture_output=True, text=True, encoding="utf-8")
        assert json.loads(explanation.stdout)["plan"]["queries"] == ["q"]
        encoded_env = dict(env, PYTHONIOENCODING="cp936", PYTHONUTF8="0")
        encoded = subprocess.run([str(command), "海洋细菌", "--explain"], cwd=temporary,
                                 env=encoded_env, capture_output=True, check=True)
        assert json.loads(encoded.stdout.decode("utf-8"))["plan"]["queries"] == ["海洋细菌"]
        error = subprocess.run([str(command), "--不存在的参数"], cwd=temporary,
                               env=encoded_env, capture_output=True)
        assert error.returncode == 2
        assert "不存在的参数" in error.stderr.decode("utf-8")

        probe = '''
import io, json, litsearch
body = json.dumps({"message": {"total-results": 1, "items": [
    {"DOI": "10.1000/smoke", "title": ["Smoke test"], "type": "journal-article"}
]}}).encode()
transport = litsearch.Http(opener=lambda request, timeout: io.BytesIO(body), polite=0)
litsearch.Http = lambda: transport
raise SystemExit(litsearch.main(["q", "--source", "crossref", "--limit", "1", "--stdout"]))
'''
        search = subprocess.run([str(python), "-I", "-c", probe], cwd=temporary, env=env,
                                check=True, capture_output=True, text=True, encoding="utf-8")
        payload = json.loads(search.stdout)
        assert payload["results"][0]["doi"] == "10.1000/smoke"
        assert payload["results"][0]["work_type"] == "article"
        assert payload["schema_version"] == 1

        standalone = temporary / "litsearch.py"
        shutil.copyfile(ROOT / "src/litsearch.py", standalone)
        copied = subprocess.run([str(python), "-I", str(standalone), "q", "--explain"],
                                cwd=temporary, env=env, check=True, capture_output=True,
                                text=True, encoding="utf-8")
        assert json.loads(copied.stdout)["version"] == version
    print("wheel、sdist、安装后离线检索与单文件交付检查通过")


def main() -> None:
    commands = [
        [sys.executable, "-m", "ruff", "check", "."],
        [sys.executable, "-m", "mypy"],
        [sys.executable, "-m", "pytest"],
        ["uv", "build"],
    ]
    for command in commands:
        print(f"执行：{' '.join(command)}", flush=True)
        subprocess.run(command, cwd=ROOT, check=True)
    check_distribution()


if __name__ == "__main__":
    main()

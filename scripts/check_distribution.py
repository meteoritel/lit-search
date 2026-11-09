"""验证 wheel、sdist 与独立单文件的可交付契约。"""

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


def main() -> None:
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
                         "scripts/check_distribution.py", "docs/python.md", "uv.lock",
                         "examples/marine-timeseries.toml", "scripts/benchmark_retrieval.py",
                         "tests/fixtures/retrieval_candidates.json"):
            assert f"litsearch-{version}/{relative}" in names, relative

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


if __name__ == "__main__":
    main()

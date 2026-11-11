"""校验发布 tag，构建并验收 Windows x64 便携包及 Release 附件。"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
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


def check_portable(archive: Path, version: str) -> None:
    with tempfile.TemporaryDirectory(prefix="litsearch-portable-check-") as directory:
        temporary = Path(directory)
        extracted = temporary / "解压 测试"
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(extracted)
        root = extracted / "litsearch"
        command = root / "litsearch.exe"
        assert command.is_file()
        assert (root / "_internal/python312.dll").is_file()
        for relative in ("LICENSE", "PYTHON-LICENSE.txt", "PYINSTALLER-LICENSE.txt", "README.md",
                         ".env.example", "docs/usage.md", "docs/python.md"):
            assert (root / relative).is_file(), relative
        assert not (root / ".env").exists()

        working = temporary / "工作 目录"
        working.mkdir()
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("LITSEARCH_", "OPENALEX_", "CROSSREF_", "PYTHON"))}
        env.update(APPDATA=str(temporary / "appdata"), HOME=str(temporary / "home"),
                   USERPROFILE=str(temporary / "home"),
                   PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
                   PYTHONIOENCODING="cp936", PYTHONUTF8="0")

        def run(*arguments: str, code: int = 0) -> subprocess.CompletedProcess:
            result = subprocess.run([str(command), *arguments], cwd=working, env=env,
                                    capture_output=True, text=True, encoding="utf-8", timeout=30)
            assert result.returncode == code, (result.returncode, result.stdout, result.stderr)
            return result

        assert run("--version").stdout.strip() == f"litsearch {version}"
        assert "--source" in run("--help").stdout
        explanation = json.loads(run("海洋细菌", "--source", "crossref", "--explain").stdout)
        assert explanation["version"] == version
        assert explanation["plan"]["queries"] == ["海洋细菌"]
        assert explanation["output"]["directory"] == str(working / "output")
        assert not (working / "output").exists()
        assert "不存在的参数" in run("--不存在的参数", code=2).stderr
        assert "API key" in run("q", code=3).stderr

        config = working / "litsearch.toml"
        config.write_text('default_sources = ["crossref"]\noutput_dir = "检索结果"\n',
                          encoding="utf-8")
        explanation = json.loads(run("海洋细菌", "--explain").stdout)
        assert explanation["plan"]["sources"] == ["crossref"]
        assert explanation["output"]["directory"] == str(working / "检索结果")
        assert explanation["output"]["origin"] == str(config)
        assert not (working / "检索结果").exists()
    print("Extracted Windows x64 portable checks passed.", flush=True)


def build_release() -> None:
    if (sys.platform != "win32" or struct.calcsize("P") != 8
            or platform.machine().lower() not in {"amd64", "x86_64"}):
        raise SystemExit("便携包必须使用 Windows x64 Python 构建。")
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("便携包使用 Python 3.12 构建；请通过 uv run --python 3.12 执行。")

    version = source_version()
    distributions = [ROOT / "dist" / f"litsearch-{version}{suffix}"
                     for suffix in ("-py3-none-any.whl", ".tar.gz")]
    for distribution in distributions:
        if not distribution.is_file():
            raise SystemExit(f"缺少 {distribution.name}；请先运行 scripts/check.py。")
    with zipfile.ZipFile(distributions[0]) as wheel:
        if wheel.read("litsearch.py") != (ROOT / "src/litsearch.py").read_bytes():
            raise SystemExit("wheel 与当前源码不一致；请先运行 scripts/check.py。")

    with tempfile.TemporaryDirectory(prefix="litsearch-release-") as directory:
        temporary = Path(directory)
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
            "--console", "--name", "litsearch", "--distpath", str(temporary / "dist"),
            "--workpath", str(temporary / "build"), "--specpath", str(temporary),
            str(ROOT / "src/litsearch.py"),
        ], cwd=ROOT, check=True)
        bundle = temporary / "dist/litsearch"
        for relative in ("README.md", "LICENSE", ".env.example", "docs/usage.md", "docs/python.md"):
            destination = bundle / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        shutil.copyfile(Path(sys.base_prefix) / "LICENSE.txt", bundle / "PYTHON-LICENSE.txt")
        pyinstaller_license = next(
            path for path in importlib.metadata.files("pyinstaller")
            if str(path).endswith("/licenses/COPYING.txt")
        )
        shutil.copyfile(pyinstaller_license.locate(), bundle / "PYINSTALLER-LICENSE.txt")

        staging = temporary / "release"
        staging.mkdir()
        archive = Path(shutil.make_archive(
            str(staging / f"litsearch-{version}-windows-x64"), "zip",
            root_dir=bundle.parent, base_dir=bundle.name,
        ))
        check_portable(archive, version)
        for distribution in distributions:
            shutil.copyfile(distribution, staging / distribution.name)
        checksums = []
        for artifact in sorted(staging.iterdir()):
            with artifact.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            checksums.append(f"{digest}  {artifact.name}\n")
        (staging / "SHA256SUMS").write_text("".join(checksums), encoding="utf-8", newline="\n")

        output = ROOT / "dist/release"
        if not output.resolve().is_relative_to(ROOT):
            raise SystemExit("Release 输出目录必须位于当前仓库内。")
        if output.exists():
            shutil.rmtree(output)
        shutil.copytree(staging, output)
    print(f"Release artifacts: {output}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    tag = commands.add_parser("check-tag", help="检查 tag 与源码版本一致，不构建产物")
    tag.add_argument("tag")
    commands.add_parser("build", help="构建并验收 Windows x64 便携 ZIP 和 Release 附件")
    args = parser.parse_args()
    if args.command == "check-tag":
        expected = f"v{source_version()}"
        if args.tag != expected:
            parser.error(f"tag 必须为 {expected}，实际为 {args.tag!r}")
        print(f"Release tag verified: {expected}")
    else:
        build_release()


if __name__ == "__main__":
    main()

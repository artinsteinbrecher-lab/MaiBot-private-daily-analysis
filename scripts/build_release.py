#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
LEGACY_SOURCE = ROOT / "editions" / "legacy_silence"
COMMON_FILES = (
    "plugin.py",
    "_manifest.json",
    "CHANGELOG.md",
    "LICENSE",
    "SECURITY.md",
    "SUPPORT.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
)
COMMON_DIRS = ("core", "templates", "fonts", "tests", "scripts", "docs")
TEXT_SUFFIXES = {".py", ".md", ".toml", ".json", ".html", ".txt", ".patch"}
RELEASE_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "*.pyo", "test_build_release.py"
)
SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|secret[_-]?key|password|passwd)"
    r"\s*[:=]\s*[\"']?[A-Za-z0-9_./+\-=]{12,}"
)
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?"
    r"(?:\+[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?$"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def files_under(root: Path) -> list[Path]:
    return sorted((path for path in root.rglob("*") if path.is_file()), key=lambda p: p.as_posix())


def copy_entry(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, target, ignore=RELEASE_IGNORE)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def reset_dist() -> None:
    resolved_root = ROOT.resolve()
    resolved_dist = DIST.resolve()
    if resolved_dist.parent != resolved_root or resolved_dist.name != "dist":
        raise RuntimeError(f"refusing to clean unexpected output path: {resolved_dist}")
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()


def build_variant(name: str) -> Path:
    package = DIST / f"khiqwq_daily_analysis_{name}"
    package.mkdir()
    for relative in COMMON_FILES:
        copy_entry(ROOT / relative, package / relative)
    for relative in COMMON_DIRS:
        copy_entry(ROOT / relative, package / relative)
    overlay = ROOT / "packaging" / name
    copy_entry(overlay / "README.md", package / "README.md")
    copy_entry(overlay / "config.example.toml", package / "config.example.toml")
    # The source README lives under packaging/<variant>/, where ../../docs is
    # correct.  In the standalone ZIP, docs/ is copied into the package root;
    # rewrite only this release-relative link so the extracted package remains
    # self-contained without making the source documentation confusing.
    readme = package / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace("../../docs/", "docs/"),
        encoding="utf-8",
        newline="\n",
    )
    if name == "multimodel":
        copy_entry(ROOT / "docs" / "MODEL_ASSIGNMENT.md", package / "MODEL_ASSIGNMENT.md")
        copy_entry(ROOT / "extras", package / "extras")
    return package


def build_legacy_variant() -> Path:
    package = DIST / "khiqwq_daily_analysis_legacy_silence"
    copy_entry(LEGACY_SOURCE, package)
    return package


def validate_package(package: Path, *, multimodel: bool) -> None:
    unwanted = []
    secret_hits = []
    for path in files_under(package):
        relative = path.relative_to(package).as_posix()
        parts = set(path.relative_to(package).parts)
        if ".git" in parts or "__pycache__" in parts or path.suffix == ".pyc" or relative == "config.toml":
            unwanted.append(relative)
        if path.suffix.lower() in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8")
            if SECRET_RE.search(text):
                secret_hits.append(relative)
    if unwanted:
        raise RuntimeError(f"unwanted release files in {package.name}: {unwanted}")
    if secret_hits:
        raise RuntimeError(f"possible credential assignments in {package.name}: {secret_hits}")
    extras_exists = (package / "extras").exists()
    if extras_exists != multimodel:
        raise RuntimeError(f"unexpected extras state for {package.name}: {extras_exists}")


def compare_common(standard: Path, multimodel: Path) -> list[tuple[str, str]]:
    relative_paths = list(COMMON_FILES)
    for directory in COMMON_DIRS:
        relative_paths.extend(
            path.relative_to(standard).as_posix()
            for path in files_under(standard / directory)
        )
    result = []
    for relative in sorted(set(relative_paths)):
        standard_hash = sha256(standard / relative)
        multimodel_hash = sha256(multimodel / relative)
        if standard_hash != multimodel_hash:
            raise RuntimeError(f"common core mismatch: {relative}")
        result.append((relative, standard_hash))
    return result


def write_core_comparison(rows: list[tuple[str, str]]) -> Path:
    output = DIST / "CORE_COMPARISON.txt"
    lines = [
        "CORE_COMPARISON",
        f"generated_date={date.today().isoformat()}",
        "standard=khiqwq_daily_analysis_standard",
        "multimodel=khiqwq_daily_analysis_multimodel",
        "result=IDENTICAL",
        "algorithm=SHA256",
        "scope=plugin.py,_manifest.json,CHANGELOG.md,LICENSE,SECURITY.md,SUPPORT.md,CONTRIBUTING.md,CODE_OF_CONDUCT.md,core/,templates/,fonts/,tests/,scripts/,docs/",
        "note=README.md and config.example.toml intentionally differ; multimodel-only MODEL_ASSIGNMENT.md and extras/ are excluded.",
        "",
        "PATH|SHA256",
    ]
    lines.extend(f"{relative}|{digest}" for relative, digest in rows)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return output


def write_manifest(packages: tuple[Path, ...]) -> Path:
    output = DIST / "FILE_MANIFEST.txt"
    lines = [
        "FILE_MANIFEST",
        f"generated_date={date.today().isoformat()}",
        "algorithm=SHA256",
        "scope=khiqwq_daily_analysis_legacy_silence/,khiqwq_daily_analysis_standard/,khiqwq_daily_analysis_multimodel/",
        "",
        "PATH|BYTES|SHA256",
    ]
    paths = []
    for package in packages:
        paths.extend(files_under(package))
    for path in sorted(paths, key=lambda p: p.relative_to(DIST).as_posix()):
        relative = path.relative_to(DIST).as_posix()
        lines.append(f"{relative}|{path.stat().st_size}|{sha256(path)}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return output


def zip_package(package: Path, version: str, variant: str) -> Path:
    archive = DIST / f"khiqwq_daily_analysis-v{version}-{variant}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in files_under(package):
            bundle.write(path, path.relative_to(DIST).as_posix())
    return archive


def write_checksums(paths: list[Path]) -> Path:
    output = DIST / "SHA256SUMS.txt"
    rows = [f"{sha256(path)}  {path.relative_to(DIST).as_posix()}" for path in sorted(paths, key=lambda p: p.as_posix())]
    output.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    return output


def validate_version(value: object) -> str:
    version = str(value or "").strip()
    if not SEMVER_RE.fullmatch(version):
        raise RuntimeError(f"manifest version is not valid SemVer: {version!r}")
    return version


def main() -> int:
    manifest = json.loads((ROOT / "_manifest.json").read_text(encoding="utf-8"))
    version = validate_version(manifest.get("version"))
    legacy_manifest = json.loads(
        (LEGACY_SOURCE / "_manifest.json").read_text(encoding="utf-8")
    )
    legacy_version = validate_version(legacy_manifest.get("version"))
    reset_dist()
    legacy = build_legacy_variant()
    standard = build_variant("standard")
    multimodel = build_variant("multimodel")
    validate_package(legacy, multimodel=False)
    validate_package(standard, multimodel=False)
    validate_package(multimodel, multimodel=True)
    comparison = write_core_comparison(compare_common(standard, multimodel))
    file_manifest = write_manifest((legacy, standard, multimodel))
    legacy_zip = zip_package(legacy, legacy_version, "legacy-silence")
    standard_zip = zip_package(standard, version, "standard")
    multimodel_zip = zip_package(multimodel, version, "multimodel")
    checksums = write_checksums(
        [comparison, file_manifest, legacy_zip, standard_zip, multimodel_zip]
    )
    print(f"built {legacy_zip.name}")
    print(f"built {standard_zip.name}")
    print(f"built {multimodel_zip.name}")
    print(f"wrote {checksums.name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"release build failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

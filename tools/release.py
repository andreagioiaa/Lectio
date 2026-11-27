"""Strumenti di release di Lectio (usati dalla CI e da build.ps1).

La versione si scrive solo in ``study_manager/config.py`` (``APP_VERSION`` e
``MIN_SUPPORTED_VERSION``); da li' questo script ricava tutto il resto.

Uso:
    python tools/release.py info [--github-output]
    python tools/release.py version-info
    python tools/release.py check-tag v0.1.5
    python tools/release.py manifest --exe dist/Lectio.exe --out dist/update.json [--notes-file RELEASE_NOTES.md]

Solo libreria standard: gira anche prima di installare le dipendenze.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "study_manager" / "config.py"
VERSION_INFO = ROOT / "version_info.txt"
REPOSITORY = "andreagioiaa/Lectio"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _read_constant(name: str) -> str:
    text = CONFIG.read_text(encoding="utf-8")
    match = re.search(rf'^{name}\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit(f"{name} non trovato in {CONFIG}")
    value = match.group(1).strip()
    if not VERSION_RE.match(value):
        raise SystemExit(f"{name} = {value!r}: serve il formato X.Y.Z")
    return value


def _as_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def versions() -> tuple[str, str]:
    version = _read_constant("APP_VERSION")
    minimum = _read_constant("MIN_SUPPORTED_VERSION")
    if _as_tuple(minimum) > _as_tuple(version):
        raise SystemExit(f"MIN_SUPPORTED_VERSION {minimum} e' piu' alta di APP_VERSION {version}")
    return version, minimum


def cmd_info(args: argparse.Namespace) -> int:
    version, minimum = versions()
    data = {"version": version, "min_version": minimum, "tag": f"v{version}"}
    print(json.dumps(data))
    if args.github_output:
        target = os.environ.get("GITHUB_OUTPUT")
        if not target:
            raise SystemExit("GITHUB_OUTPUT non impostata")
        with open(target, "a", encoding="utf-8") as handle:
            for key, value in data.items():
                handle.write(f"{key}={value}\n")
    return 0


VERSION_INFO_TEMPLATE = """# Generato da tools/release.py a partire da APP_VERSION: non modificare a mano.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({c}),
    prodvers=({c}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '040904B0',
          [
            StringStruct('CompanyName', 'Lectio'),
            StringStruct('FileDescription', 'Lectio - Gestionale Studio & Analytics'),
            StringStruct('FileVersion', '{d}'),
            StringStruct('InternalName', 'Lectio'),
            StringStruct('LegalCopyright', 'Lectio'),
            StringStruct('OriginalFilename', 'Lectio.exe'),
            StringStruct('ProductName', 'Lectio'),
            StringStruct('ProductVersion', '{d}')
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def cmd_version_info(_args: argparse.Namespace) -> int:
    version, _minimum = versions()
    parts = [*version.split("."), "0"]
    text = VERSION_INFO_TEMPLATE.format(c=", ".join(parts), d=".".join(parts))
    VERSION_INFO.write_text(text, encoding="utf-8")
    print(f"version_info.txt -> {'.'.join(parts)}")
    return 0


def cmd_check_tag(args: argparse.Namespace) -> int:
    version, _minimum = versions()
    tag = args.tag.strip()
    if tag != f"v{version}":
        print(
            f"Il tag {tag!r} non corrisponde ad APP_VERSION {version!r} (atteso v{version}). "
            "Aggiorna study_manager/config.py oppure usa il tag giusto.",
            file=sys.stderr,
        )
        return 1
    print(f"Tag {tag} coerente con APP_VERSION")
    return 0


def _notes(args: argparse.Namespace) -> str:
    if args.notes_file:
        path = Path(args.notes_file)
        if path.is_file():
            # I commenti HTML (istruzioni per chi scrive) non finiscono nell'app.
            text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
            return text.strip()
    return (args.notes or "").strip()


def cmd_manifest(args: argparse.Namespace) -> int:
    version, minimum = versions()
    exe = Path(args.exe)
    digest = hashlib.sha256()
    with exe.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    tag = f"v{version}"
    manifest = {
        "latest": version,
        "min_version": minimum,
        "url": f"https://github.com/{REPOSITORY}/releases/download/{tag}/Lectio.exe",
        "page": f"https://github.com/{REPOSITORY}/releases/tag/{tag}",
        "sha256": digest.hexdigest(),
        "size": exe.stat().st_size,
        "notes": _notes(args),
    }
    Path(args.out).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release.py")
    sub = parser.add_subparsers(dest="command", required=True)
    info = sub.add_parser("info", help="Stampa versione, versione minima e tag")
    info.add_argument("--github-output", action="store_true", help="Scrive anche in $GITHUB_OUTPUT")
    info.set_defaults(func=cmd_info)
    sub.add_parser("version-info", help="Rigenera version_info.txt").set_defaults(func=cmd_version_info)
    check = sub.add_parser("check-tag", help="Verifica che il tag corrisponda ad APP_VERSION")
    check.add_argument("tag")
    check.set_defaults(func=cmd_check_tag)
    manifest = sub.add_parser("manifest", help="Scrive update.json per la release")
    manifest.add_argument("--exe", required=True)
    manifest.add_argument("--out", required=True)
    manifest.add_argument("--notes", default="")
    manifest.add_argument("--notes-file", default="")
    manifest.set_defaults(func=cmd_manifest)
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())

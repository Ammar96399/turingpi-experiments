"""Parse repository source without executing applications or contacting hardware."""

import argparse
import ast
import importlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source_paths():
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    for name in sorted(set(result.stdout.decode().split("\0")) - {""}):
        path = ROOT / name
        # Never parse credential artifacts, private inventory, or local envs.
        if name.startswith(
            ("artifacts/", "inventories/", "group_vars/", "host_vars/", ".venv/")
        ):
            continue
        if "kubeconfig" in path.name.lower() or path.name in {
            "inventory.yml",
            "inventory.yaml",
        }:
            continue
        if path.is_file() and path.suffix in {".py", ".yml", ".yaml"}:
            yield path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python-only", action="store_true", help="Skip YAML parsing explicitly"
    )
    args = parser.parse_args()
    yaml = None
    if not args.python_only:
        try:
            yaml = importlib.import_module("yaml")
        except ImportError:
            print(
                "Install requirements-dev.txt for YAML validation, or use --python-only.",
                file=sys.stderr,
            )
            return 2

    counts = {"Python": 0, "YAML": 0}
    errors = []
    for path in source_paths():
        kind = "Python" if path.suffix == ".py" else "YAML"
        if kind == "YAML" and args.python_only:
            continue
        try:
            text = path.read_text(encoding="utf-8")
            if kind == "Python":
                ast.parse(text, filename=str(path.relative_to(ROOT)))
            else:
                assert yaml is not None
                list(yaml.safe_load_all(text))
            counts[kind] += 1
        except (SyntaxError, ValueError, UnicodeError) as exc:
            # Do not echo source text, which may contain deployment information.
            errors.append(f"{path.relative_to(ROOT)}: {type(exc).__name__}")
        except Exception as exc:
            if yaml is None or not isinstance(exc, yaml.YAMLError):
                raise
            errors.append(f"{path.relative_to(ROOT)}: invalid YAML")
    for error in errors:
        print(error, file=sys.stderr)
    print(
        f"Parsed {counts['Python']} Python and {counts['YAML']} YAML files; {len(errors)} errors."
    )
    if args.python_only:
        print("YAML validation explicitly skipped.")
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main())

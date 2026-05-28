#!/usr/bin/env python3
"""Resolve fleet components from manifest. Checkout repos at pinned refs."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

MANIFEST = Path(__file__).parent.parent / "components.lock"
REPO_ROOT = Path(__file__).parent.parent.parent


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text())


def checkout_component(name: str, comp: dict) -> bool:
    """Clone or update a component repo to the pinned ref."""
    target = REPO_ROOT / name
    repo_url = comp["repo"]
    ref = comp["ref"]

    if target.exists():
        # Update existing
        result = subprocess.run(
            ["git", "fetch", "origin"],
            cwd=target,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"WARN: {name} fetch failed: {result.stderr}", file=sys.stderr)
            return False
        result = subprocess.run(
            ["git", "checkout", ref],
            cwd=target,
            capture_output=True,
            text=True,
        )
    else:
        # Clone new
        result = subprocess.run(
            ["git", "clone", repo_url, str(target)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"ERROR: {name} clone failed: {result.stderr}", file=sys.stderr)
            return False
        result = subprocess.run(
            ["git", "checkout", ref],
            cwd=target,
            capture_output=True,
            text=True,
        )

    if result.returncode != 0:
        print(f"ERROR: {name} checkout failed: {result.stderr}", file=sys.stderr)
        return False

    # Verify
    verify = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=target,
        capture_output=True,
        text=True,
    )
    actual = verify.stdout.strip()
    print(f"  {name}: {actual[:7]} (manifest: {ref})")
    return actual.startswith(ref)


def main():
    parser = argparse.ArgumentParser(description="Resolve fleet components")
    parser.add_argument("--checkout", action="store_true", help="Clone/update all repos")
    parser.add_argument("--list", action="store_true", help="List components")
    parser.add_argument("--verify", action="store_true", help="Verify local refs match manifest")
    args = parser.parse_args()

    manifest = load_manifest()
    components = manifest["components"]

    if args.list:
        for name, comp in components.items():
            print(f"{name:30} {comp['version']:10} {comp['ref']}")
        return

    if args.checkout:
        ok = 0
        fail = 0
        for name, comp in components.items():
            if checkout_component(name, comp):
                ok += 1
            else:
                fail += 1
        print(f"\nResolved: {ok} OK, {fail} failed")
        sys.exit(0 if fail == 0 else 1)

    if args.verify:
        fail = 0
        for name, comp in components.items():
            target = REPO_ROOT / name
            if not target.exists():
                print(f"MISSING: {name} not found")
                fail += 1
                continue
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=target,
                capture_output=True,
                text=True,
                check=True,
            )
            actual = result.stdout.strip()
            ref = comp["ref"]
            if actual.startswith(ref):
                print(f"OK: {name} {actual[:7]}")
            else:
                print(f"MISMATCH: {name} local={actual[:7]} manifest={ref}")
                fail += 1
        sys.exit(0 if fail == 0 else 1)

    parser.print_help()


if __name__ == "__main__":
    main()

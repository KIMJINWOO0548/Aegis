"""List the verified product dependency files for the Windows release builder."""

import argparse
import importlib.metadata
import json
from pathlib import Path
import sys
import sysconfig
import tomllib


def runtime_files(project):
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    if sysconfig.get_platform() != "win-amd64" or sys.version_info[:2] != (3, 12):
        raise ValueError("Use a Windows x64 Python 3.12 staging environment.")

    site = Path(sysconfig.get_path("purelib")).resolve()
    pending = list(map(Requirement, project["project"]["dependencies"]))
    resolved = {}
    files = set()

    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        if requirement.url or requirement.extras:
            raise ValueError(f"Unsupported release dependency: {requirement}")

        distribution = importlib.metadata.distribution(requirement.name)
        if distribution.version not in requirement.specifier:
            raise ValueError(f"Installed distribution does not satisfy {requirement}: {distribution.version}")
        if name in resolved:
            continue
        if Path(distribution.locate_file("")).resolve() != site or distribution.files is None:
            raise ValueError(f"Distribution has no regular install under site-packages: {requirement.name}")
        resolved[name] = distribution.version
        for item in distribution.files:
            # Console scripts reside outside site-packages and are not used by Aegis.
            if ".." in item.parts or "__pycache__" in item.parts:
                continue
            if item.is_absolute() or item.suffix in {".pyc", ".pth"}:
                raise ValueError(f"Unsupported distribution file: {item}")
            installed = Path(distribution.locate_file(item))
            if not installed.resolve().is_relative_to(site) or not installed.is_file():
                raise ValueError(f"Missing or misplaced distribution file: {item}")
            files.add(item.as_posix())

        for value in distribution.requires or ():
            dependency = Requirement(value)
            if dependency.marker is None or dependency.marker.evaluate({"extra": ""}):
                pending.append(dependency)

    return {
        "base": sys.base_prefix,
        "site": str(site),
        "pythonVersion": ".".join(map(str, sys.version_info[:3])),
        "distributions": resolved,
        "files": sorted(files),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = runtime_files(tomllib.loads(args.project.read_text(encoding="utf-8")))
    args.output.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()

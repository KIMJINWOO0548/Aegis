"""Read-only primitive preflight for the opt-in storage tools, not certification.

The existing tools retain their own fail-closed checks. This command does not
open databases, import collectors, create files, or test filesystem guarantees.
"""

import json
import os


REQUIREMENTS = (
    ("adapter_fixture", ("O_NOFOLLOW",)),
    ("adapter_store", ("O_NOFOLLOW", "O_DIRECTORY")),
    ("nullable_input_archive", ("O_NOFOLLOW", "O_DIRECTORY")),
    ("archive_backup_restore", ("O_DIRECTORY",)),
    ("storage_diagnostics", ("O_NOFOLLOW",)),
)


def report(platform=os):
    """Report known entry-point gates; flag presence alone is not validation."""
    features = []
    for name, flags in REQUIREMENTS:
        if name == "adapter_fixture" and platform.name == "nt":
            features.append({
                "name": name,
                "status": "NATIVE_READER_AVAILABLE",
                "missing": [],
                "scope": "READ_ONLY_FIXTURE_FINAL_COMPONENT_NOFOLLOW",
            })
            continue
        missing = ([] if platform.name == "posix" else ["posix"])
        missing += [flag for flag in flags if not hasattr(platform, flag)]
        features.append({
            "name": name,
            "status": "PLATFORM_UNVERIFIED" if missing else "PRIMITIVES_PRESENT",
            "missing": missing,
        })
    return {
        "schemaVersion": "platform-preflight-1",
        "platform": platform.name,
        "status": "BLOCKED" if any(row["missing"] for row in features) else "PRIMITIVES_PRESENT",
        "runtimeValidation": "NOT_RUN",
        "features": features,
    }


def main():
    result = report()
    print(json.dumps(result, sort_keys=True))
    return 2 if result["status"] == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())

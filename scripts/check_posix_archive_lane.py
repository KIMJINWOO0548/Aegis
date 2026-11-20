"""Check that the Linux evidence actually includes the platform-gated cases."""

import ast
from collections import defaultdict
from pathlib import Path
import sys
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]


def coverage(xml_path):
    required = set()
    fifo = set()
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        if "from platform_requirements import" not in (source := path.read_text(encoding="utf-8")):
            continue
        for node in ast.parse(source).body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            key = path.stem, node.name
            for decorator in node.decorator_list:
                if any(isinstance(part, ast.Name) and part.id.startswith("requires_posix_")
                       for part in ast.walk(decorator)):
                    required.add(key)
                elif any(isinstance(part, ast.Name) and part.id == "fifo_case" for part in ast.walk(decorator)):
                    fifo.add(key)
    if not required or not fifo:
        raise ValueError("No platform-specific cases identified")
    seen = defaultdict(list)
    seen_fifo = defaultdict(list)
    for node in ElementTree.parse(xml_path).findall(".//testcase"):
        runner_class = node.attrib.get("classname", "").rsplit(".", 1)[-1]
        name = node.attrib["name"]
        key = runner_class, name.split("[", 1)[0]
        if key in required:
            seen[key].append(node)
        if key in fifo and name.partition("[")[2].startswith(("fifo]", "fifo-")):
            seen_fifo[key].append(node)
    for expected, found in ((required, seen), (fifo, seen_fifo)):
        missing = expected - found.keys()
        if missing:
            raise ValueError(f"Cases missing from Linux result: {sorted(missing)}")
        blocked = [node.attrib["name"] for nodes in found.values() for node in nodes
                   if any(status.tag in ("skipped", "failure", "error") for status in node)]
        if blocked:
            raise ValueError(f"Linux cases did not pass: {blocked}")
    scenarios = sum(map(len, seen.values()))
    fifo_scenarios = sum(map(len, seen_fifo.values()))
    # Include all compensating Linux reruns, including 16 no-write cases
    # that also stay enabled on Windows. Do not silently drop parameters.
    if scenarios < 209 or fifo_scenarios < 3:
        raise ValueError(f"Incomplete Linux coverage: {scenarios} POSIX / {fifo_scenarios} FIFO")
    print(f"Linux prerequisites exercised: {scenarios} POSIX scenarios and {fifo_scenarios} FIFO scenarios")


if __name__ == "__main__":
    coverage(sys.argv[1])

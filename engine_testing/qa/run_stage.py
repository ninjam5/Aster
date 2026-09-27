"""Stage QA runner — executes a stage's suites and writes artifacts.

Usage:
    python engine_testing/qa/run_stage.py 1

Exit code 0 = every suite passed; 1 = at least one failed.
Never mutates the repo; only writes under engine_testing/qa/artifacts/.
"""
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
ARTIFACTS = os.path.join(HERE, "artifacts")

STAGES = {
    "0": [
        ("pytest", [sys.executable, "-m", "pytest", "tests/", "-q"]),
    ],
    "1": [
        ("pytest", [sys.executable, "-m", "pytest", "tests/", "-q"]),
        ("dom-motor-test", [sys.executable, "dom-motor-test.py"]),
    ],
    "2": [
        ("pytest", [sys.executable, "-m", "pytest", "tests/", "-q"]),
        ("dom-motor-test", [sys.executable, "dom-motor-test.py"]),
    ],
    "3": [
        ("pytest", [sys.executable, "-m", "pytest", "tests/", "-q"]),
        ("dom-motor-test", [sys.executable, "dom-motor-test.py"]),
        ("system1-test", [sys.executable, "system1-test.py"]),
    ],
    "4": [
        ("pytest", [sys.executable, "-m", "pytest", "tests/", "-q"]),
        ("dom-motor-test", [sys.executable, "dom-motor-test.py"]),
        ("system1-test", [sys.executable, "system1-test.py"]),
    ],
}


def main(argv):
    stage = argv[1] if len(argv) > 1 else "0"
    if stage not in STAGES:
        print(f"Unknown stage {stage!r}. Known: {', '.join(sorted(STAGES))}")
        return 2
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = os.path.join(ARTIFACTS, f"stage{stage}")
    os.makedirs(outdir, exist_ok=True)

    results = []
    for name, cmd in STAGES[stage]:
        print(f"\n=== stage {stage} :: {name} ===")
        try:
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                                  timeout=1800)
            rc = proc.returncode
            output = (proc.stdout or "") + (proc.stderr or "")
        except Exception as e:
            rc = 1
            output = f"runner error: {e}"
        log_path = os.path.join(outdir, f"{name}-{stamp}.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(output)
        tail = "\n".join(output.strip().splitlines()[-4:])
        print(tail)
        results.append({"suite": name, "returncode": rc, "log": log_path})

    ok = all(r["returncode"] == 0 for r in results)
    summary = {"stage": stage, "timestamp": stamp, "passed": ok, "results": results}
    summary_path = os.path.join(outdir, f"summary-{stamp}.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(f"\n{'PASS' if ok else 'FAIL'} — stage {stage} "
          f"({sum(1 for r in results if r['returncode'] == 0)}/{len(results)} suites)")
    print(f"artifacts: {outdir}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
"""source: each mutation breaks one rule; the suite must fail, or the rule is not pinned.

Prints killed NAME, survived NAME, or timed out NAME, then mutations: N, killed: K, survived: S.
A timed out row counts as survived. Exits 1 if any mutation survived.
Exits 2 if a replacement does not change the copy, or if the clean copy does not pass.
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MUTATIONS = [
    (
        "lock-accepts-filled-grader",
        "golden.py",
        "    if filled is not None:\n",
        "    if False and filled is not None:\n",
    ),
    (
        "lock-overwrites-existing",
        "golden.py",
        "    if os.path.exists(lock_path):\n",
        "    if False and os.path.exists(lock_path):\n",
    ),
    (
        "correction-without-note",
        "golden.py",
        "        if not note_is_dated(note):\n",
        "        if False and not note_is_dated(note):\n",
    ),
    (
        "note-date-not-validated",
        "golden.py",
        "    try:\n        datetime.date.fromisoformat(day)\n    except ValueError:\n        return False\n",
        "    if False:\n        datetime.date.fromisoformat(day)\n",
    ),
    (
        "added-case-accepted",
        "golden.py",
        '        if ident not in answers:\n            problems.append("%s: case %s is not in the lock" % (path, ident))\n',
        '        if False and ident not in answers:\n            problems.append("%s: case %s is not in the lock" % (path, ident))\n',
    ),
    (
        "removed-case-accepted",
        "golden.py",
        "        if ident not in seen:\n",
        "        if False and ident not in seen:\n",
    ),
    (
        "bar-is-greater-than",
        "golden.py",
        "    passed = score >= bar\n",
        "    passed = score > bar\n",
    ),
    (
        "blank-grader-counts-as-match",
        "golden.py",
        '        right = ""\n',
        "        right = left\n",
    ),
    (
        "labels-compared-without-norm",
        "golden.py",
        "    left = norm(mine_text)\n    if grader_text.strip() == \"\":\n        right = \"\"\n    else:\n        right = norm(grader_text)\n",
        "    left = mine_text\n    if grader_text.strip() == \"\":\n        right = \"\"\n    else:\n        right = grader_text\n",
    ),
    (
        "kappa-drops-expected-agreement",
        "agreement.py",
        "    return (observed - expected) / (1.0 - expected)\n",
        "    return observed\n",
    ),
    (
        "kappa-guard-removed",
        "agreement.py",
        "    if (1.0 - expected) < 1e-12:\n        return None\n",
        "    if False and (1.0 - expected) < 1e-12:\n        return None\n",
    ),
    (
        "bootstrap-ignores-seed",
        "agreement.py",
        "    rng = random.Random(seed)\n",
        "    rng = random.Random(0)\n",
    ),
    (
        "interval-upper-index-off-by-one",
        "agreement.py",
        "    hi_index = int(m * (1.0 - alpha / 2.0)) - 1\n",
        "    hi_index = int(m * (1.0 - alpha / 2.0))\n",
    ),
    (
        "run-overwrites-filled-cells",
        "golden.py",
        '        if not again and row[grader_at].strip() != "":\n            continue\n',
        '        if False and not again and row[grader_at].strip() != "":\n            continue\n',
    ),
    (
        "run-keeps-stale-cell-on-failed-rerun",
        "golden.py",
        "            row[grader_at] = \"\"\n",
        "            row[grader_at] = row[grader_at]\n",
    ),
    (
        "run-records-label-on-nonzero-exit",
        "golden.py",
        '        if proc.returncode != 0:\n            return None, "exited %d" % proc.returncode\n',
        '        if proc.returncode != 0 and False:\n            return None, "exited %d" % proc.returncode\n',
    ),
    (
        "timeout-ignored",
        "golden.py",
        "            out, _err = proc.communicate(payload, timeout=timeout)\n",
        "            out, _err = proc.communicate(payload)\n",
    ),
    (
        "unused-label-line-dropped",
        "golden.py",
        "    for line in unused_label_lines(header, rows):\n        print(line)\n",
        "    for line in []:\n        print(line)\n",
    ),
    (
        "pass-exit-code-becomes-1",
        "golden.py",
        "    return 0 if passed else 1\n",
        "    return 1\n",
    ),
    (
        "atomic-save-writes-in-place",
        "golden.py",
        "        os.replace(temporary, path)\n        temporary = None\n",
        "        with open(path, \"wb\") as landed:\n            landed.write(text.encode(\"utf-8\"))\n"
        "        if os.path.exists(temporary):\n            os.unlink(temporary)\n        temporary = None\n",
    ),
    (
        "terminate-leaves-the-grader",
        "golden.py",
        "    signal.signal(signal.SIGTERM, on_terminate)\n",
        "    pass\n",
    ),
    (
        "stop-hits-only-the-grader",
        "golden.py",
        "        except BaseException:\n            kill_group(proc)\n",
        "        except BaseException:\n            proc.kill()\n",
    ),
    (
        "group-kill-hits-only-the-grader",
        "golden.py",
        "            os.killpg(proc.pid, signal.SIGKILL)\n",
        "            os.kill(proc.pid, signal.SIGKILL)\n",
    ),
    (
        "grader-shares-our-session",
        "golden.py",
        "start_new_session=True",
        "start_new_session=False",
    ),
    (
        "again-keeps-old-labels",
        "golden.py",
        "    if again:\n        for row in rows:\n            row[grader_at] = \"\"\n        save_table(path, header, rows)\n",
        "    if False and again:\n        for row in rows:\n            row[grader_at] = \"\"\n        save_table(path, header, rows)\n",
    ),
    (
        "save-drops-the-mode",
        "golden.py",
        "        os.chmod(temporary, kept)\n",
        "        os.chmod(temporary, 0o600)\n",
    ),
    (
        "interrupt-exit-code",
        "golden.py",
        "        return finish_stop(130)\n",
        "        return 1\n",
    ),
    (
        "hang-up-not-handled",
        "golden.py",
        "        signal.signal(signal.SIGHUP, on_terminate)\n",
        "        pass\n",
    ),
    (
        "hang-up-overrides-nohup",
        "golden.py",
        "    replaced_hup = previous_hup != signal.SIG_IGN\n",
        "    replaced_hup = True\n",
    ),
    (
        "control-character-label-kept",
        "golden.py",
        "            if any(unicodedata.category(char) == \"Cc\" for char in label):\n",
        "            if False and any(unicodedata.category(char) == \"Cc\" for char in label):\n",
    ),
    (
        "changed-row-hides-the-answers",
        "golden.py",
        "            print(\"  case %s (%s): %s\" % (ident, detail, note))\n",
        "            print(\"  case %s: %s\" % (ident, note))\n",
    ),
    (
        "terminate-restore-skipped",
        "golden.py",
        "        signal.signal(signal.SIGTERM, previous_term)\n",
        "        pass\n",
    ),
    (
        "hang-up-restore-skipped",
        "golden.py",
        "        if replaced_hup: signal.signal(signal.SIGHUP, previous_hup)\n",
        "        if False and replaced_hup: signal.signal(signal.SIGHUP, previous_hup)\n",
    ),
    (
        "again-clear-not-on-disk",
        "golden.py",
        "        for row in rows:\n            row[grader_at] = \"\"\n        save_table(path, header, rows)\n",
        "        for row in rows:\n            row[grader_at] = \"\"\n",
    ),
]


def apply_mutation(text, old, new, name):
    count = text.count(old)
    if count != 1:
        sys.stderr.write("%s: found %d matches, stopped\n" % (name, count))
        sys.exit(2)
    updated = text.replace(old, new, 1)
    if updated == text:
        sys.stderr.write("%s: text did not change, stopped\n" % name)
        sys.exit(2)
    return updated


def copy_repo(dest):
    for name in ("golden.py", "agreement.py"):
        shutil.copy2(os.path.join(ROOT, name), os.path.join(dest, name))
    for folder in ("examples", "tests"):
        shutil.copytree(
            os.path.join(ROOT, folder),
            os.path.join(dest, folder),
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )


def run_suite(dest):
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
        cwd=dest,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        start_new_session=True,
    )
    try:
        output, _err = proc.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass
        return "timeout", ""
    return proc.returncode, output


def report(name, code, output):
    if code == "timeout":
        sys.stdout.write("timed out %s\n" % name)
        return "survived"
    if code == 0:
        sys.stdout.write("survived %s\n" % name)
        sys.stderr.write(output[-2000:])
        return "survived"
    sys.stdout.write("killed %s\n" % name)
    return "killed"


def main():
    clean = tempfile.mkdtemp(prefix="golden-mut-")
    try:
        copy_repo(clean)
        code, _output = run_suite(clean)
    finally:
        shutil.rmtree(clean)
    if code != 0:
        sys.stderr.write("the clean copy does not pass\n")
        return 2
    killed = 0
    survived = 0
    for name, filename, old, new in MUTATIONS:
        dest = tempfile.mkdtemp(prefix="golden-mut-")
        try:
            copy_repo(dest)
            target = os.path.join(dest, filename)
            with open(target, encoding="utf-8") as handle:
                original = handle.read()
            updated = apply_mutation(original, old, new, name)
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(updated)
            code, output = run_suite(dest)
        finally:
            shutil.rmtree(dest)
        if report(name, code, output) == "killed":
            killed += 1
        else:
            survived += 1
    total = killed + survived
    sys.stdout.write("mutations: %d, killed: %d, survived: %d\n" % (total, killed, survived))
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())

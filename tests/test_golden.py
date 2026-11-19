"""source: the lock, run, and score commands, through the command line.

Each test starts the tool with sys.executable and writes only under a temporary
directory. A test fails when the command does nothing: the exit status, the
output, or the file bytes are wrong.
"""

import csv
import datetime
import errno
import fnmatch
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
GOLDEN = os.path.join(ROOT, "golden.py")
DEMO = os.path.join(ROOT, "examples", "demo.sh")
SET_CELL = os.path.join(ROOT, "examples", "set_cell.py")

PASS_SENTENCE = (
    "the bar sits inside that interval: on a fresh set of cases this grader could land under it."
)
NOT_YET_SENTENCE = (
    "the bar sits inside that interval: this miss could be luck. Add cases before you decide."
)


def run_set_cell(args):
    proc = subprocess.run(
        [sys.executable, SET_CELL] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def run_golden(args, timeout=20):
    proc = subprocess.Popen(
        [sys.executable, GOLDEN] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.communicate()
        raise AssertionError("golden.py did not finish within %s seconds" % timeout)
    return proc.returncode, out, err


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    return rows[0], rows[1:]


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def kill_recorded(path):
    """Send SIGKILL to each pid listed in path. A missing process is ignored."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    for line in lines:
        line = line.strip()
        if line == "":
            continue
        try:
            os.kill(int(line), signal.SIGKILL)
        except (ProcessLookupError, ValueError, PermissionError):
            pass


def kill_command_pids(snippet):
    """Stop processes whose command contains snippet, one pid at a time."""
    proc = subprocess.run(
        ["ps", "-ax", "-o", "pid=", "-o", "command="],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        universal_newlines=True,
    )
    me = os.getpid()
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if stripped == "":
            continue
        pid_text, sep, command = stripped.partition(" ")
        if not sep or snippet not in command:
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == me:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def call_main(argv):
    """Run golden.main in this process and capture both streams."""
    import golden

    saved_out, saved_err = sys.stdout, sys.stderr
    out, err = io.StringIO(), io.StringIO()
    sys.stdout, sys.stderr = out, err
    try:
        code = golden.main(argv)
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    return code, out.getvalue(), err.getvalue()


def reap_tool(proc):
    """Stop one tool process by its own pid. Never a process group."""
    if proc.poll() is not None:
        return
    try:
        os.kill(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def reset_hangup():
    signal.signal(signal.SIGHUP, signal.SIG_DFL)


def ignore_hangup():
    signal.signal(signal.SIGHUP, signal.SIG_IGN)


def pid_is_gone(pid, seconds):
    deadline = time.monotonic() + seconds
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


class CommandCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = self.tmp.name
        self.path = os.path.join(self.folder, "cases.csv")

    def write(self, header, rows):
        write_csv(self.path, header, rows)

    def script(self, name, body):
        path = os.path.join(self.folder, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
        return path

    def lock(self, bar):
        code, out, err = run_golden(["lock", self.path, "--bar", str(bar)])
        self.assertEqual(code, 0, err + out)
        self.assertEqual(err, "")
        self.assertNotIn("Traceback", out)
        return out

    def scored(self, labeled, bar, score_args=None):
        base = []
        graded = []
        for index, pair in enumerate(labeled, start=1):
            case = "case %d text" % index
            base.append([str(index), case, pair[0]])
            graded.append([str(index), case, pair[0], pair[1]])
        self.write(["id", "case", "mine"], base)
        self.lock(bar)
        self.write(["id", "case", "mine", "grader"], graded)
        args = ["score", self.path]
        if score_args:
            args.extend(score_args)
        return run_golden(args)

    def edit_lock(self, mutate):
        lock_path = self.path + ".lock"
        with open(lock_path, encoding="utf-8") as handle:
            data = json.load(handle)
        mutate(data)
        with open(lock_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)

    def refuse(self, expected, lock_exists=True):
        before = read_bytes(self.path)
        lock_path = self.path + ".lock"
        lock_before = None
        if lock_exists:
            lock_before = read_bytes(lock_path)
        watcher = self.script(
            "should_not_run.py",
            "import sys\nsys.stdin.read()\nsys.stdout.write('nope\\n')\n",
        )
        for command in ("score", "run"):
            args = [command, self.path]
            if command == "run":
                args.extend(["--", sys.executable, watcher])
            code, out, err = run_golden(args)
            self.assertEqual(code, 2, command + "\n" + out + err)
            self.assertEqual(out, "", command)
            self.assertEqual(err, expected, command)
            self.assertNotIn("Traceback", err)
            self.assertEqual(read_bytes(self.path), before, command)
            if lock_exists:
                self.assertEqual(read_bytes(lock_path), lock_before, command)
            else:
                self.assertFalse(os.path.exists(lock_path), command)


class LockTests(CommandCase):
    def test_programs_are_executable(self):
        """source: golden.py and examples/demo.sh carry the executable bit."""
        self.assertTrue(os.access(GOLDEN, os.X_OK))
        self.assertTrue(os.access(DEMO, os.X_OK))

    def test_lock_file_is_not_ignored(self):
        """source: an ignore pattern must not match the lock file that sits beside a table."""
        path = os.path.join(ROOT, ".gitignore")
        if not os.path.isfile(path):
            return
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        for line in lines:
            pattern = line.strip()
            if pattern == "" or pattern.startswith("#"):
                continue
            self.assertFalse(fnmatch.fnmatch("answers.csv.lock", pattern), pattern)

    def test_lock_writes_the_exact_record(self):
        """source: lock freezes version, bar, count, date, normalized answers, and the case hash."""
        folder = os.path.join(self.folder, "nested")
        os.makedirs(folder)
        self.path = os.path.join(folder, "cases.csv")
        first = "Hello, friend"
        second = 'say "hi"'
        self.write(
            ["id", "case", "mine"],
            [["1", first, " Urgent "], ["10", second, "SPAM"]],
        )
        before_day = datetime.date.today().isoformat()
        out = self.lock(2)
        after_day = datetime.date.today().isoformat()
        self.assertEqual(out, "locked 2 cases, bar 2 of 2. Now run the grader.\n")
        payload = {
            "version": 1,
            "bar": 2,
            "cases": 2,
            "locked": before_day,
            "answers": {
                "1": {"mine": "urgent", "case_sha256": digest(first)},
                "10": {"mine": "spam", "case_sha256": digest(second)},
            },
        }
        lock_path = self.path + ".lock"
        raw = read_bytes(lock_path)
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        text = raw.decode("utf-8")
        locked_day = json.loads(text)["locked"]
        self.assertIn(locked_day, (before_day, after_day))
        payload["locked"] = locked_day
        self.assertEqual(text, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        self.assertFalse(os.path.exists(os.path.join(folder, "cases.lock")))

    def test_second_lock_is_refused_and_the_first_stays(self):
        """source: an existing lock is kept, and the message gives its date and bar."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"], ["2", "beta", "normal"]])
        self.lock(2)
        lock_path = self.path + ".lock"
        with open(lock_path, encoding="utf-8") as handle:
            data = json.load(handle)
        before = read_bytes(lock_path)
        code, out, err = run_golden(["lock", self.path, "--bar", "1"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("locked %s, bar %s" % (data["locked"], data["bar"]), err)
        self.assertIn("Delete it and clear the grader column to start over.", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(read_bytes(lock_path), before)

    def test_corrupt_lock_is_refused_without_a_traceback(self):
        """source: a second lock stops even when the existing file is not JSON."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        with open(self.path + ".lock", "w", encoding="utf-8") as handle:
            handle.write("not-json")
        code, out, err = run_golden(["lock", self.path, "--bar", "1"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("locked unknown, bar unknown", err)
        self.assertIn("Delete it and clear the grader column to start over.", err)
        self.assertNotIn("Traceback", err)

    def test_filled_grader_is_refused_and_names_the_first_case(self):
        """source: answers must be locked before the grader runs."""
        self.write(
            ["id", "case", "mine", "grader"],
            [
                ["1", "alpha", "urgent", "   "],
                ["2", "beta", "normal", "spam"],
                ["3", "gamma", "spam", "spam"],
            ],
        )
        code, out, err = run_golden(["lock", self.path, "--bar", "1"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("answers must be locked before the grader runs (case 2)", err)
        self.assertNotIn("case 3", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(self.path + ".lock"))

    def test_blank_grader_column_can_be_locked(self):
        """source: a grader column whose cells are blank does not block the lock."""
        self.write(
            ["id", "case", "mine", "grader"],
            [["1", "alpha", "urgent", ""], ["2", "beta", "normal", "   "]],
        )
        out = self.lock(2)
        self.assertEqual(out, "locked 2 cases, bar 2 of 2. Now run the grader.\n")

    def test_bar_must_be_a_whole_number_from_1_to_n(self):
        """source: the bar is refused unless it is a whole number from 1 to N."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"], ["2", "beta", "normal"]])
        refused = [None, "0", "3", "1.5", "foo", "-1", "+1"]
        for bad in refused:
            with self.subTest(bad=bad):
                if bad is None:
                    args = ["lock", self.path]
                else:
                    args = ["lock", self.path, "--bar", bad]
                code, out, err = run_golden(args)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn(self.path, err)
                self.assertIn("whole number from 1 to 2", err)
                self.assertNotIn("Traceback", err)
                self.assertFalse(os.path.exists(self.path + ".lock"))
        code, out, err = run_golden(["lock", self.path, "--bar"])
        self.assertEqual(code, 2)
        self.assertIn("missing value for --bar", err)
        self.assertNotIn("Traceback", err)

    def test_usage(self):
        """source: a missing command, a missing file, or an unknown option exits 2."""
        code, out, err = run_golden([])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("usage: python3 golden.py lock|run|score FILE ...", err)
        self.assertNotIn("Traceback", err)
        code, out, err = run_golden(["lock", "--bar", "1"])
        self.assertEqual(code, 2)
        self.assertIn("missing file", err)
        code, out, err = run_golden(["score", self.path, "--nope"])
        self.assertEqual(code, 2)
        self.assertIn("unknown option --nope", err)
        self.assertNotIn("Traceback", err)
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        code, out, err = run_golden(["lock", self.path, "extra", "--bar", "1"])
        self.assertEqual(code, 2)
        self.assertIn("unexpected argument extra", err)
        self.assertFalse(os.path.exists(self.path + ".lock"))

    def test_unreadable_lock_is_refused(self):
        """source: a lock that cannot be read is reported without a traceback."""
        import golden

        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        real_open = open

        def refuse_lock(path, *args, **kwargs):
            if str(path).endswith(".lock"):
                raise OSError(errno.EACCES, "Permission denied")
            return real_open(path, *args, **kwargs)

        golden.open = refuse_lock
        try:
            code, out, err = call_main(["golden.py", "score", self.path])
        finally:
            golden.open = real_open
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err, "%s: cannot read lock file\n" % (self.path + ".lock"))
        self.assertNotIn("Traceback", err)

    def test_failed_save_leaves_no_temporary_file(self):
        """source: a save the system refuses is reported without a traceback, and no temporary file remains."""
        import golden

        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        real_replace = golden.os.replace

        def refuse_replace(src, dst):
            if str(dst).endswith(".lock"):
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_replace(src, dst)

        golden.os.replace = refuse_replace
        try:
            code, out, err = call_main(["golden.py", "lock", self.path, "--bar", "1"])
        finally:
            golden.os.replace = real_replace
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(
            err,
            "%s: cannot write file: No space left on device\n" % (self.path + ".lock"),
        )
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(self.path + ".lock"))
        for name in os.listdir(self.folder):
            self.assertFalse(name.startswith(".golden-"), name)
            self.assertFalse(name.endswith(".tmp"), name)


class CsvTests(CommandCase):
    def test_csv_errors_name_the_file_and_the_line(self):
        """source: each CSV error exits 2 and names the file and the line."""
        samples = [
            ("id,case\n1,alpha\n", "missing required column mine", ":1:"),
            ("case,mine\nalpha,urgent\n", "missing required column id", ":1:"),
            ("id,mine\n1,urgent\n", "missing required column case", ":1:"),
            ("id,case,mine,mine\n1,alpha,urgent,x\n", "duplicated column name mine", ":1:"),
            ("id,case,mine\n", "no data rows", ":1:"),
            ("", "missing required column id", ":1:"),
            ("id,case,mine\n1,alpha\n", "row has 2 cells, header has 3", ":2:"),
            ("id,case,mine\n1,alpha,urgent,extra\n", "row has 4 cells, header has 3", ":2:"),
            ("id,case,mine\n,alpha,urgent\n", "empty id", ":2:"),
            ("id,case,mine\n   ,alpha,urgent\n", "empty id", ":2:"),
            ("id,case,mine\n1,alpha,urgent\n1,beta,normal\n", "duplicated id 1", ":3:"),
            ("id,case,mine\n1,alpha,   \n", "empty mine", ":2:"),
            (
                'id,case,mine\n1,"line one\nline two",urgent\n,bad,urgent\n',
                "empty id",
                ":4:",
            ),
        ]
        for text, snippet, line in samples:
            with self.subTest(snippet=snippet, line=line):
                with open(self.path, "w", encoding="utf-8", newline="") as handle:
                    handle.write(text)
                code, out, err = run_golden(["lock", self.path, "--bar", "1"])
                self.assertEqual(code, 2, text)
                self.assertEqual(out, "")
                self.assertIn(self.path, err)
                self.assertIn(line, err)
                self.assertIn(snippet, err)
                self.assertNotIn("Traceback", err)

    def test_missing_file_and_bad_encoding_have_no_traceback(self):
        """source: a missing file or a file that is not UTF-8 exits 2 without a traceback."""
        missing = os.path.join(self.folder, "missing.csv")
        code, out, err = run_golden(["lock", missing, "--bar", "1"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn(missing, err)
        self.assertIn("file not found", err)
        self.assertNotIn("Traceback", err)
        with open(self.path, "wb") as handle:
            handle.write(b"id,case,mine\n1,\xff,urgent\n")
        code, out, err = run_golden(["lock", self.path, "--bar", "1"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn(self.path, err)
        self.assertIn("not valid UTF-8", err)
        self.assertNotIn("Traceback", err)

    def test_bom_is_accepted_and_not_written_back(self):
        """source: a leading byte order mark is accepted, and the saved file has none."""
        with open(self.path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["id", "case", "mine"])
            writer.writerow(["1", "Hello, friend", "urgent"])
        self.lock(1)
        script = self.script("ok.py", "import sys\nsys.stdin.read()\nsys.stdout.write('ok\\n')\n")
        code, out, err = run_golden(["run", self.path, "--", sys.executable, script])
        self.assertEqual(code, 0, err + out)
        self.assertNotIn("Traceback", err)
        raw = read_bytes(self.path)
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", raw)
        header, rows = read_csv(self.path)
        self.assertEqual(header, ["id", "case", "mine", "grader"])
        self.assertEqual(rows, [["1", "Hello, friend", "urgent", "ok"]])

    def test_long_case_locks_runs_and_scores(self):
        """source: a case longer than the parser's old limit locks, runs, and scores."""
        text = "a" * 200000
        self.write(["id", "case", "mine"], [["1", text, "urgent"]])
        out = self.lock(1)
        self.assertIn("locked 1 cases, bar 1 of 1", out)
        script = self.script("ok.py", "import sys\nsys.stdin.read()\nsys.stdout.write('urgent\\n')\n")
        code, out, err = run_golden(["run", self.path, "--", sys.executable, script])
        self.assertEqual(code, 0, err + out)
        self.assertEqual(out, "ran 1: answered 1, could not 0\n")
        self.assertNotIn("Traceback", err)
        csv.field_size_limit(2147483647)
        header, rows = read_csv(self.path)
        self.assertEqual(rows[0][1], text)
        self.assertEqual(rows[0][3], "urgent")
        code, out, err = run_golden(["score", self.path])
        self.assertEqual(code, 0, err + out)
        self.assertIn("1 of 1, bar 1: PASS\n", out)
        self.assertNotIn("Traceback", err + out)

    def test_bare_carriage_return_is_a_parse_error(self):
        """source: a table whose lines end with a bare carriage return is a parse error."""
        with open(self.path, "wb") as handle:
            handle.write(b"id,case,mine\r1,alpha,urgent\r")
        code, out, err = run_golden(["lock", self.path, "--bar", "1"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err.count("\n"), 1)
        self.assertTrue(err.startswith("%s: cannot parse the CSV" % self.path), err)
        self.assertNotIn("Traceback", err)


class RunTests(CommandCase):
    def test_run_adds_the_column_skips_filled_rows_and_again_reruns(self):
        """source: run adds grader, leaves filled cells, and --again grades every row."""
        self.write(
            ["id", "case", "mine"],
            [["1", "alpha", "urgent"], ["2", "beta", "normal"]],
        )
        self.lock(1)
        before = os.stat(self.path).st_ino
        first = self.script("first.py", "import sys\nsys.stdin.read()\nsys.stdout.write('first\\n')\n")
        code, out, err = run_golden(["run", self.path, "--", sys.executable, first])
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        self.assertEqual(out, "ran 2: answered 2, could not 0\n")
        self.assertNotEqual(os.stat(self.path).st_ino, before)
        header, rows = read_csv(self.path)
        self.assertEqual(header, ["id", "case", "mine", "grader"])
        self.assertEqual([row[3] for row in rows], ["first", "first"])
        fresh = self.script("fresh.py", "import sys\nsys.stdin.read()\nsys.stdout.write('fresh\\n')\n")
        code, out, err = run_golden(["run", self.path, "--", sys.executable, fresh])
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "ran 0: answered 0, could not 0\n")
        header, rows = read_csv(self.path)
        self.assertEqual([row[3] for row in rows], ["first", "first"])
        code, out, err = run_golden(["run", self.path, "--again", "--", sys.executable, fresh])
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "ran 2: answered 2, could not 0\n")
        header, rows = read_csv(self.path)
        self.assertEqual([row[3] for row in rows], ["fresh", "fresh"])

    def test_run_fills_only_blank_cells_when_some_are_filled(self):
        """source: without --again a filled grader cell is left as it was."""
        self.write(
            ["id", "case", "mine"],
            [["1", "alpha", "urgent"], ["2", "beta", "normal"]],
        )
        self.lock(1)
        self.write(
            ["id", "case", "mine", "grader"],
            [["1", "alpha", "urgent", "keep-me"], ["2", "beta", "normal", ""]],
        )
        script = self.script("fresh.py", "import sys\nsys.stdin.read()\nsys.stdout.write('fresh\\n')\n")
        code, out, err = run_golden(["run", self.path, "--", sys.executable, script])
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "ran 1: answered 1, could not 0\n")
        header, rows = read_csv(self.path)
        self.assertEqual(rows[0][3], "keep-me")
        self.assertEqual(rows[1][3], "fresh")

    def test_failed_again_clears_the_old_label(self):
        """source: --again blanks a cell before the case runs, including when that run fails."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        self.write(["id", "case", "mine", "grader"], [["1", "alpha", "urgent", "stale"]])
        script = self.script("fail.py", "import sys\nsys.stdin.read()\nsys.exit(1)\n")
        code, out, err = run_golden(["run", self.path, "--again", "--", sys.executable, script])
        self.assertEqual(code, 1, err + out)
        self.assertEqual(out, "ran 1: answered 0, could not 1\n")
        self.assertEqual(err, "case 1: grader exited 1\n")
        header, rows = read_csv(self.path)
        self.assertEqual(rows[0][3], "")

    def test_again_stop_leaves_later_rows_blank(self):
        """source: a stop during a fresh grading leaves later cells blank and keeps no old label."""
        import golden

        self.write(
            ["id", "case", "mine"],
            [["1", "alpha", "urgent"], ["2", "beta", "normal"], ["3", "gamma", "spam"]],
        )
        self.lock(1)
        self.write(
            ["id", "case", "mine", "grader"],
            [
                ["1", "alpha", "urgent", "old-one"],
                ["2", "beta", "normal", "old-two"],
                ["3", "gamma", "spam", "old-three"],
            ],
        )
        calls = {"n": 0}
        real = golden.grade_one

        def fake(cmd, case_text, timeout):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise KeyboardInterrupt
            return "fresh", None

        golden.grade_one = fake
        try:
            code, out, err = call_main(
                ["golden.py", "run", self.path, "--again", "--", sys.executable, "-c", "print('x')"]
            )
        finally:
            golden.grade_one = real
        self.assertEqual(code, 130, err + out)
        self.assertIn("stopped", err)
        self.assertNotIn("Traceback", err + out)
        header, rows = read_csv(self.path)
        self.assertEqual([row[-1] for row in rows], ["fresh", "", ""])
        raw = read_text(self.path)
        self.assertNotIn("old-one", raw)
        self.assertNotIn("old-two", raw)
        self.assertNotIn("old-three", raw)

    def test_again_clear_is_on_disk_before_the_first_grader_call(self):
        """source: the cleared sheet is on disk before the grader starts, so a stop there keeps no old label."""
        self.write(
            ["id", "case", "mine"],
            [["1", "alpha", "urgent"], ["2", "beta", "normal"], ["3", "gamma", "spam"]],
        )
        self.lock(1)
        self.write(
            ["id", "case", "mine", "grader"],
            [
                ["1", "alpha", "urgent", "old-one"],
                ["2", "beta", "normal", "old-two"],
                ["3", "gamma", "spam", "old-three"],
            ],
        )
        record = os.path.join(self.folder, "calls.txt")
        script = self.script(
            "watch.py",
            "\n".join(
                [
                    "import csv",
                    "import json",
                    "import sys",
                    "sys.stdin.read()",
                    "handle = open(%r, encoding='utf-8', newline='')" % self.path,
                    "rows = list(csv.reader(handle))",
                    "handle.close()",
                    "header = rows[0]",
                    "at = header.index('grader')",
                    "cells = [row[at] for row in rows[1:]]",
                    "log = open(%r, 'a', encoding='utf-8')" % record,
                    "log.write(json.dumps(cells) + '\\n')",
                    "log.close()",
                    "sys.stdout.write('fresh\\n')",
                    "",
                ]
            ),
        )
        code, out, err = run_golden(
            ["run", self.path, "--again", "--", sys.executable, script]
        )
        self.assertEqual(code, 0, err + out)
        self.assertEqual(out, "ran 3: answered 3, could not 0\n")
        self.assertEqual(err, "")
        found = [json.loads(line) for line in read_text(record).splitlines()]
        self.assertEqual(
            found,
            [
                ["", "", ""],
                ["fresh", "", ""],
                ["fresh", "fresh", ""],
            ],
        )
        raw = read_text(record)
        self.assertNotIn("old-one", raw)
        self.assertNotIn("old-two", raw)
        self.assertNotIn("old-three", raw)

    def test_again_with_a_missing_command_keeps_old_labels(self):
        """source: a command that cannot start leaves the old labels in place."""
        self.write(
            ["id", "case", "mine"],
            [["1", "alpha", "urgent"], ["2", "beta", "normal"], ["3", "gamma", "spam"]],
        )
        self.lock(1)
        self.write(
            ["id", "case", "mine", "grader"],
            [
                ["1", "alpha", "urgent", "old-one"],
                ["2", "beta", "normal", "old-two"],
                ["3", "gamma", "spam", "old-three"],
            ],
        )
        before = read_bytes(self.path)
        code, out, err = run_golden(
            ["run", self.path, "--again", "--", "no-such-grader-golden"]
        )
        self.assertEqual(code, 2, err + out)
        self.assertEqual(out, "")
        self.assertNotIn("Traceback", err)
        self.assertEqual(read_bytes(self.path), before)

    def test_nonzero_exit_does_not_record_the_label(self):
        """source: a command that exits non-zero leaves a blank cell and exit 1."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        script = self.script(
            "bad.py",
            "import sys\nsys.stdin.read()\nsys.stdout.write('urgent\\n')\nsys.stdout.flush()\nsys.exit(3)\n",
        )
        code, out, err = run_golden(["run", self.path, "--", sys.executable, script])
        self.assertEqual(code, 1, err + out)
        self.assertEqual(out, "ran 1: answered 0, could not 1\n")
        self.assertEqual(err, "case 1: grader exited 3\n")
        self.assertNotIn("Traceback", err)
        header, rows = read_csv(self.path)
        self.assertEqual(rows[0][3], "")

    def test_no_label_leaves_a_blank_cell(self):
        """source: a command that prints no non-blank line leaves a blank cell and exit 1."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        script = self.script(
            "empty.py",
            "import sys\nsys.stdin.read()\nsys.stdout.write('\\n  \\n')\n",
        )
        code, out, err = run_golden(["run", self.path, "--", sys.executable, script])
        self.assertEqual(code, 1, err + out)
        self.assertEqual(out, "ran 1: answered 0, could not 1\n")
        self.assertEqual(err, "case 1: grader gave no label\n")
        header, rows = read_csv(self.path)
        self.assertEqual(rows[0][3], "")

    def test_color_sequence_label_is_refused(self):
        """source: a label wrapped in a terminal color sequence is refused and the cell stays blank."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        script = self.script(
            "color.py",
            "import sys\nsys.stdin.read()\nsys.stdout.write('\\x1b[31murgent\\x1b[0m\\n')\n",
        )
        code, out, err = run_golden(["run", self.path, "--", sys.executable, script])
        self.assertEqual(code, 1, err + out)
        self.assertEqual(out, "ran 1: answered 0, could not 1\n")
        self.assertEqual(err, "case 1: grader gave a label with a control character\n")
        self.assertNotIn("Traceback", err)
        header, rows = read_csv(self.path)
        self.assertEqual(rows[0][-1], "")

    def test_tab_inside_a_label_is_refused(self):
        """source: a label that holds a tab is refused and the cell stays blank."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        script = self.script(
            "tab.py",
            "import sys\nsys.stdin.read()\nsys.stdout.write('urg\\tent\\n')\n",
        )
        code, out, err = run_golden(["run", self.path, "--", sys.executable, script])
        self.assertEqual(code, 1, err + out)
        self.assertEqual(out, "ran 1: answered 0, could not 1\n")
        self.assertEqual(err, "case 1: grader gave a label with a control character\n")
        self.assertNotIn("Traceback", err)
        header, rows = read_csv(self.path)
        self.assertEqual(rows[0][-1], "")

    def test_timeout_leaves_a_blank_cell(self):
        """source: a command that sleeps past a 1 second timeout leaves a blank cell and exit 1."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        script = self.script(
            "slow.py",
            "import time\ntime.sleep(30)\nimport sys\nsys.stdout.write('urgent\\n')\n",
        )
        code, out, err = run_golden(
            ["run", self.path, "--timeout", "1", "--", sys.executable, script],
            timeout=12,
        )
        self.assertEqual(code, 1, err + out)
        self.assertEqual(out, "ran 1: answered 0, could not 1\n")
        self.assertEqual(err, "case 1: grader timed out after 1 second\n")
        self.assertNotIn("Traceback", err)
        header, rows = read_csv(self.path)
        self.assertEqual(rows[0][3], "")

    def helper_grader(self, pid_path):
        return self.script(
            "helper_grader.py",
            "\n".join(
                [
                    "import subprocess",
                    "import sys",
                    "import time",
                    "sys.stdin.read()",
                    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])",
                    "handle = open(%r, 'a', encoding='utf-8')" % pid_path,
                    "handle.write(str(child.pid) + '\\n')",
                    "handle.close()",
                    "time.sleep(30)",
                    "",
                ]
            ),
        )

    def test_timeout_stops_the_helper_the_grader_started(self):
        """source: a time limit stops the grader and the helper it started, without waiting for that helper to exit."""
        self.write(
            ["id", "case", "mine"],
            [["1", "alpha", "urgent"], ["2", "beta", "normal"]],
        )
        self.lock(1)
        pid_path = os.path.join(self.folder, "helpers.pid")
        script = self.helper_grader(pid_path)
        self.addCleanup(kill_recorded, pid_path)
        self.addCleanup(kill_command_pids, script)
        started = time.monotonic()
        code, out, err = run_golden(
            ["run", self.path, "--timeout", "1", "--", sys.executable, script],
            timeout=12,
        )
        elapsed = time.monotonic() - started
        self.assertEqual(code, 1, err + out)
        self.assertEqual(out, "ran 2: answered 0, could not 2\n")
        self.assertEqual(err.count("timed out after 1 second"), 2)
        self.assertNotIn("Traceback", err)
        self.assertLess(elapsed, 10)
        header, rows = read_csv(self.path)
        self.assertEqual(header[-1], "grader")
        self.assertEqual([row[-1] for row in rows], ["", ""])
        recorded = [int(line.strip()) for line in read_text(pid_path).splitlines() if line.strip()]
        self.assertEqual(len(recorded), 2)
        for pid in recorded:
            self.assertTrue(pid_is_gone(pid, 5), pid)

    def test_terminate_stops_the_helper_the_grader_started(self):
        """source: a terminate signal stops the tool and the helper the grader started, and the file still parses."""
        self.write(
            ["id", "case", "mine"],
            [["1", "alpha", "urgent"], ["2", "beta", "normal"]],
        )
        self.lock(1)
        before_header, before_rows = read_csv(self.path)
        pid_path = os.path.join(self.folder, "helpers.pid")
        script = self.helper_grader(pid_path)
        self.addCleanup(kill_recorded, pid_path)
        self.addCleanup(kill_command_pids, script)
        proc = subprocess.Popen(
            [sys.executable, GOLDEN, "run", self.path, "--timeout", "60", "--", sys.executable, script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            start_new_session=True,
        )
        self.addCleanup(reap_tool, proc)
        pid = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            if os.path.isfile(pid_path):
                lines = [line.strip() for line in read_text(pid_path).splitlines() if line.strip()]
                if lines:
                    pid = int(lines[0])
                    break
            time.sleep(0.05)
        self.assertIsNotNone(pid, "helper pid was not recorded")
        os.kill(proc.pid, signal.SIGTERM)
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            reap_tool(proc)
            out, err = proc.communicate()
            self.fail("tool did not exit after SIGTERM\n" + out + err)
        self.assertEqual(proc.returncode, 143, err + out)
        self.assertIn("stopped", err)
        self.assertNotIn("Traceback", err)
        self.assertTrue(pid_is_gone(pid, 5), pid)
        header, rows = read_csv(self.path)
        self.assertEqual(header[: len(before_header)], before_header)
        self.assertEqual(len(rows), len(before_rows))
        for old, new in zip(before_rows, rows):
            self.assertEqual(new[: len(old)], old)

    def test_interrupt_keeps_the_first_label(self):
        """source: an interrupt stops the run, says stopped, and keeps the label already written."""
        import golden

        self.write(
            ["id", "case", "mine"],
            [["1", "alpha", "urgent"], ["2", "beta", "normal"]],
        )
        self.lock(1)
        calls = {"n": 0}
        real = golden.grade_one

        def fake(cmd, case_text, timeout):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise KeyboardInterrupt
            return "kept", None

        golden.grade_one = fake
        try:
            code, out, err = call_main(
                ["golden.py", "run", self.path, "--", sys.executable, "-c", "print('x')"]
            )
        finally:
            golden.grade_one = real
        self.assertEqual(code, 130, err + out)
        self.assertIn("stopped", err)
        self.assertNotIn("Traceback", err + out)
        header, rows = read_csv(self.path)
        self.assertEqual(rows[0][-1], "kept")
        self.assertEqual(rows[1][-1], "")

    def sleeper_grader(self, pid_path):
        return self.script(
            "sleeper_grader.py",
            "\n".join(
                [
                    "import os",
                    "import subprocess",
                    "import sys",
                    "import time",
                    "sys.stdin.read()",
                    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])",
                    "handle = open(%r, 'w', encoding='utf-8')" % pid_path,
                    "handle.write(str(os.getpid()) + '\\n')",
                    "handle.write(str(child.pid) + '\\n')",
                    "handle.close()",
                    "time.sleep(30)",
                    "",
                ]
            ),
        )

    def start_sleeper(self, preexec):
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        pid_path = os.path.join(self.folder, "helpers.pid")
        script = self.sleeper_grader(pid_path)
        known = []

        def kill_known():
            for pid in known:
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, ValueError):
                    pass

        self.addCleanup(kill_known)
        self.addCleanup(kill_command_pids, script)
        proc = subprocess.Popen(
            [sys.executable, GOLDEN, "run", self.path, "--timeout", "60", "--", sys.executable, script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            start_new_session=True,
            preexec_fn=preexec,
        )
        self.addCleanup(reap_tool, proc)
        found = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            if os.path.isfile(pid_path):
                lines = [line.strip() for line in read_text(pid_path).splitlines() if line.strip()]
                if len(lines) >= 2:
                    found = [int(line) for line in lines[:2]]
                    break
            time.sleep(0.05)
        self.assertIsNotNone(found, "grader did not record its process ids")
        known.extend(found)
        return proc, found

    def test_hangup_stops_the_recorded_processes(self):
        """source: a hang-up stops the tool and the grader's process group, and says stopped."""
        proc, found = self.start_sleeper(reset_hangup)
        os.kill(proc.pid, signal.SIGHUP)
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            reap_tool(proc)
            out, err = proc.communicate()
            self.fail("tool did not exit after hang-up\n" + out + err)
        self.assertEqual(proc.returncode, 129, err + out)
        self.assertIn("stopped", err)
        self.assertNotIn("Traceback", err + out)
        for pid in found:
            self.assertTrue(pid_is_gone(pid, 5), pid)

    def test_hangup_with_the_terminal_gone_stops_the_recorded_processes(self):
        """source: a hang-up still stops the grader when the terminal cannot be written."""
        proc, found = self.start_sleeper(reset_hangup)
        proc.stdout.close()
        proc.stderr.close()
        os.kill(proc.pid, signal.SIGHUP)
        try:
            code = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            reap_tool(proc)
            self.fail("tool did not exit after hang-up with the terminal gone")
        self.assertEqual(code, 129)
        for pid in found:
            self.assertTrue(pid_is_gone(pid, 5), pid)

    def test_ignored_hangup_lets_the_run_finish(self):
        """source: a hang-up that was already ignored stays ignored and the run finishes."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        ready = os.path.join(self.folder, "ready")
        script = self.script(
            "nap.py",
            "\n".join(
                [
                    "import sys",
                    "import time",
                    "sys.stdin.read()",
                    "handle = open(%r, 'w', encoding='utf-8')" % ready,
                    "handle.write('ready')",
                    "handle.close()",
                    "time.sleep(1)",
                    "sys.stdout.write('kept\\n')",
                    "",
                ]
            ),
        )
        self.addCleanup(kill_command_pids, script)
        proc = subprocess.Popen(
            [sys.executable, GOLDEN, "run", self.path, "--timeout", "30", "--", sys.executable, script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            start_new_session=True,
            preexec_fn=ignore_hangup,
        )
        self.addCleanup(reap_tool, proc)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not os.path.isfile(ready):
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        self.assertTrue(os.path.isfile(ready), "grader did not reach its sleep")
        self.assertIsNone(proc.poll(), "grader finished before the signal")
        os.kill(proc.pid, signal.SIGHUP)
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            reap_tool(proc)
            out, err = proc.communicate()
            self.fail("tool did not finish under an ignored hang-up\n" + out + err)
        self.assertEqual(proc.returncode, 0, err + out)
        self.assertNotIn("Traceback", err + out)
        header, rows = read_csv(self.path)
        self.assertEqual(rows[0][-1], "kept")

    def test_main_restores_stop_handlers(self):
        """source: each return puts back the stop handlers found on entry, and a command runs under the tool's own handlers."""
        import golden

        def terminate_handler(signum, frame):
            pass

        def hangup_handler(signum, frame):
            pass

        found_term = signal.getsignal(signal.SIGTERM)
        found_hup = signal.getsignal(signal.SIGHUP)

        def put_found_back():
            signal.signal(signal.SIGTERM, found_term)
            signal.signal(signal.SIGHUP, found_hup)

        self.addCleanup(put_found_back)
        signal.signal(signal.SIGTERM, terminate_handler)
        signal.signal(signal.SIGHUP, hangup_handler)

        def restored():
            self.assertIs(signal.getsignal(signal.SIGTERM), terminate_handler)
            self.assertIs(signal.getsignal(signal.SIGHUP), hangup_handler)

        with self.subTest(way="no command"):
            code, out, err = call_main(["golden.py"])
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertIn("usage: python3 golden.py lock|run|score FILE ...", err)
            self.assertNotIn("Traceback", err)
            restored()

        ways = (
            ("returns zero", "return", 0),
            ("raises fail", "fail", 2),
            ("keyboard interrupt", "interrupt", 130),
            ("terminate status", "term", 143),
            ("hang-up status", "hup", 129),
        )
        for way, action, expected in ways:
            with self.subTest(way=way):
                seen = {}

                def stand_in(args, action=action, seen=seen):
                    seen["term"] = signal.getsignal(signal.SIGTERM)
                    seen["hup"] = signal.getsignal(signal.SIGHUP)
                    if action == "return":
                        return 0
                    if action == "fail":
                        raise golden.Fail("refused")
                    if action == "interrupt":
                        raise KeyboardInterrupt
                    if action == "term":
                        raise SystemExit(143)
                    if action == "hup":
                        raise SystemExit(129)
                    raise AssertionError(action)

                real = golden.cmd_score
                try:
                    golden.cmd_score = stand_in
                    code, out, err = call_main(["golden.py", "score"])
                finally:
                    golden.cmd_score = real
                self.assertEqual(code, expected, err + out)
                self.assertNotIn("Traceback", err + out)
                self.assertIs(seen["term"], golden.on_terminate)
                self.assertIs(seen["hup"], golden.on_terminate)
                restored()

    def test_fast_grader_accepts_a_one_second_timeout(self):
        """source: a grader that finishes inside the timeout is recorded."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        script = self.script("fast.py", "import sys\nsys.stdin.read()\nsys.stdout.write('fine\\n')\n")
        code, out, err = run_golden(["run", self.path, "--timeout", "1", "--", sys.executable, script])
        self.assertEqual(code, 0, err + out)
        self.assertEqual(out, "ran 1: answered 1, could not 0\n")
        self.assertNotIn("timed out", err)
        header, rows = read_csv(self.path)
        self.assertEqual(rows[0][3], "fine")

    def test_missing_or_unexecutable_command_leaves_the_bytes(self):
        """source: a command that cannot be started exits 2 and does not change the file."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"], ["2", "beta", "normal"]])
        before = read_bytes(self.path)
        self.lock(1)
        self.assertEqual(read_bytes(self.path), before)
        commands = [
            ["no-such-grader-golden"],
            [os.path.join(self.folder, "no-such-grader")],
        ]
        tool = os.path.join(self.folder, "tool.sh")
        with open(tool, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nprintf 'urgent\\n'\n")
        os.chmod(tool, 0o644)
        self.assertFalse(os.access(tool, os.X_OK))
        commands.append([tool])
        for command in commands:
            with self.subTest(command=command):
                code, out, err = run_golden(["run", self.path, "--"] + command)
                self.assertEqual(code, 2, err + out)
                self.assertEqual(out, "")
                self.assertIn("cannot be started", err)
                self.assertIn(command[0], err)
                self.assertNotIn("Traceback", err)
                self.assertEqual(read_bytes(self.path), before)

    def test_file_is_saved_after_every_row(self):
        """source: a grader that fails on the third case has already left the first two answers on disk."""
        self.write(
            ["id", "case", "mine"],
            [
                ["1", "alpha one", "urgent"],
                ["2", "beta two", "normal"],
                ["3", "please BREAK this", "urgent"],
                ["4", "delta four", "normal"],
            ],
        )
        self.lock(1)
        marker = os.path.join(self.folder, "marker.txt")
        script = self.script(
            "break.py",
            "\n".join(
                [
                    "import csv",
                    "import sys",
                    "text = sys.stdin.read()",
                    "if 'BREAK' in text:",
                    "    path, marker = sys.argv[1], sys.argv[2]",
                    "    handle = open(path, encoding='utf-8', newline='')",
                    "    rows = list(csv.DictReader(handle))",
                    "    handle.close()",
                    "    flag = 'yes'",
                    "    if rows[0].get('grader', '').strip() == '' or rows[1].get('grader', '').strip() == '':",
                    "        flag = 'no'",
                    "    out = open(marker, 'w', encoding='utf-8')",
                    "    out.write(flag)",
                    "    out.close()",
                    "    sys.exit(1)",
                    "sys.stdout.write('kept\\n')",
                    "",
                ]
            ),
        )
        code, out, err = run_golden(
            ["run", self.path, "--", sys.executable, script, self.path, marker]
        )
        self.assertEqual(code, 1, err + out)
        self.assertEqual(out, "ran 4: answered 3, could not 1\n")
        self.assertEqual(err, "case 3: grader exited 1\n")
        self.assertEqual(read_text(marker), "yes")
        header, rows = read_csv(self.path)
        self.assertEqual(header[-1], "grader")
        self.assertEqual([row[-1] for row in rows], ["kept", "kept", "", "kept"])

    def test_stdin_and_the_first_nonblank_line(self):
        """source: the case arrives on stdin with a newline, and the label is the first non-blank line."""
        self.write(["id", "case", "mine"], [["1", "hello", "urgent"]])
        self.lock(1)
        script = self.script(
            "stdin.py",
            "import sys\ntext = sys.stdin.read()\n"
            "sys.stdout.write('good\\n' if text == 'hello\\n' else 'bad\\n')\n",
        )
        code, out, err = run_golden(["run", self.path, "--", sys.executable, script])
        self.assertEqual(code, 0, err + out)
        header, rows = read_csv(self.path)
        self.assertEqual(rows[0][3], "good")
        later = self.script(
            "lines.py",
            "import sys\nsys.stdin.read()\nsys.stdout.write('\\n  kept  \\nsecond\\n')\n",
        )
        code, out, err = run_golden(["run", self.path, "--again", "--", sys.executable, later])
        self.assertEqual(code, 0, err + out)
        header, rows = read_csv(self.path)
        self.assertEqual(rows[0][3], "kept")

    def test_extra_columns_keep_their_order(self):
        """source: columns other than id, case, and mine stay in place, and grader is added last."""
        self.write(
            ["id", "extra", "case", "mine", "tail"],
            [
                ["1", "left", "Hello, friend", "urgent", "right"],
                ["2", "L", 'say "hi"', "normal", "R"],
            ],
        )
        self.lock(1)
        script = self.script("ok.py", "import sys\nsys.stdin.read()\nsys.stdout.write('ok\\n')\n")
        code, out, err = run_golden(["run", self.path, "--", sys.executable, script])
        self.assertEqual(code, 0, err + out)
        raw = read_bytes(self.path)
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", raw)
        self.assertTrue(raw.endswith(b"\n"))
        header, rows = read_csv(self.path)
        self.assertEqual(header, ["id", "extra", "case", "mine", "tail", "grader"])
        self.assertEqual(rows[0], ["1", "left", "Hello, friend", "urgent", "right", "ok"])
        self.assertEqual(rows[1], ["2", "L", 'say "hi"', "normal", "R", "ok"])

    def test_run_usage_and_bad_timeout(self):
        """source: a missing command, an empty command, or a bad timeout exits 2."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        before = read_bytes(self.path)
        usage = "usage: python3 golden.py run FILE [--again] [--timeout SEC] -- CMD"
        for args in (["run", self.path, sys.executable], ["run", self.path, "--"]):
            code, out, err = run_golden(args)
            self.assertEqual(code, 2, args)
            self.assertEqual(out, "")
            self.assertIn(usage, err)
            self.assertNotIn("Traceback", err)
            self.assertEqual(read_bytes(self.path), before)
        for bad in ("0", "-4", "1.5", "abc"):
            code, out, err = run_golden(
                ["run", self.path, "--timeout", bad, "--", sys.executable, "-c", "print(1)"]
            )
            self.assertEqual(code, 2, bad + err)
            self.assertEqual(out, "")
            self.assertIn("--timeout needs a positive whole number", err)
            self.assertNotIn("Traceback", err)
            self.assertEqual(read_bytes(self.path), before)


class ScoreTests(CommandCase):
    def test_score_lists_every_kind_of_line_for_a_pass(self):
        """source: score lists misses, a no-answer, an unused label, the correction, then the pass."""
        self.write(
            ["id", "case", "mine"],
            [
                ["1", "alpha", "urgent"],
                ["2", "beta", "normal"],
                ["3", "gamma", "urgent"],
                ["4", "delta", "normal"],
            ],
        )
        self.lock(2)
        self.write(
            ["id", "case", "mine", "grader", "note"],
            [
                ["1", "alpha", "urgent", "urgent", ""],
                ["2", "beta", "normal", "other", ""],
                ["3", "gamma", "urgent", "", ""],
                ["4", "delta", "urgent", "urgent", "2026-01-15 fixed the label"],
            ],
        )
        code, out, err = run_golden(["score", self.path])
        expected = (
            "case 2: you said normal, grader said other. Who is wrong?\n"
            "case 3: you said urgent, grader gave no answer.\n"
            'the grader used a label you never did: "other" (cases 2)\n'
            "answers changed since the lock, each with a dated note: 1\n"
            '  case 4 (locked "normal", now "urgent"): 2026-01-15 fixed the label\n'
            "2 of 4, bar 2: PASS\n"
            "agreement beyond chance (Cohen's kappa): 0.20\n"
            "95% bootstrap interval: 0 to 4 of 4\n"
            + PASS_SENTENCE
            + "\n"
        )
        self.assertEqual(err, "")
        self.assertEqual(out, expected)
        self.assertEqual(code, 0)
        self.assertNotIn("Traceback", out)

    def test_labels_match_after_case_fold_and_outer_space(self):
        """source: Urgent, urgent, and URGENT are one label, and outer spaces are ignored."""
        code, out, err = self.scored([("Urgent ", "urgent"), (" spam", " SPAM")], 2)
        expected = (
            "2 of 2, bar 2: PASS\n"
            "agreement beyond chance (Cohen's kappa): 1.00\n"
            "95% bootstrap interval: 2 to 2 of 2\n"
        )
        self.assertEqual(err, "")
        self.assertEqual(out, expected)
        self.assertEqual(code, 0)

    def test_display_trims_and_unused_labels_are_sorted(self):
        """source: a miss shows the typed label trimmed, and unused labels are sorted with file order."""
        self.write(
            ["id", "case", "mine"],
            [["9", "alpha", " urgent "], ["4", "beta", "normal"], ["2", "gamma", "normal"]],
        )
        self.lock(1)
        self.write(
            ["id", "case", "mine", "grader"],
            [
                ["9", "alpha", " urgent ", " Spam "],
                ["4", "beta", "normal", " alpha "],
                ["2", "gamma", "normal", "ALPHA"],
            ],
        )
        code, out, err = run_golden(["score", self.path])
        expected = (
            "case 9: you said urgent, grader said Spam. Who is wrong?\n"
            "case 4: you said normal, grader said alpha. Who is wrong?\n"
            "case 2: you said normal, grader said ALPHA. Who is wrong?\n"
            'the grader used a label you never did: "alpha" (cases 4, 2)\n'
            'the grader used a label you never did: "spam" (cases 9)\n'
            "0 of 3, bar 1: NOT YET\n"
            "agreement beyond chance (Cohen's kappa): 0.00\n"
            "95% bootstrap interval: 0 to 0 of 3\n"
        )
        self.assertEqual(err, "")
        self.assertEqual(out, expected)
        self.assertEqual(code, 1)

    def test_blank_grader_is_a_miss(self):
        """source: a blank grader cell is no answer, and it counts as a miss."""
        code, out, err = self.scored([("urgent", "   ")], 1)
        expected = (
            "case 1: you said urgent, grader gave no answer.\n"
            "0 of 1, bar 1: NOT YET\n"
            "agreement beyond chance (Cohen's kappa): 0.00\n"
            "95% bootstrap interval: 0 to 0 of 1\n"
        )
        self.assertEqual(err, "")
        self.assertEqual(out, expected)
        self.assertEqual(code, 1)

    def test_score_before_any_grader_column_is_no_answer(self):
        """source: with no grader column yet, every row is a miss and the score can say NOT YET."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        code, out, err = run_golden(["score", self.path])
        expected = (
            "case 1: you said urgent, grader gave no answer.\n"
            "0 of 1, bar 1: NOT YET\n"
            "agreement beyond chance (Cohen's kappa): 0.00\n"
            "95% bootstrap interval: 0 to 0 of 1\n"
        )
        self.assertEqual(err, "")
        self.assertEqual(out, expected)
        self.assertEqual(code, 1)

    def test_two_corrections_and_undefined_kappa(self):
        """source: dated notes are listed, a single label has no kappa, and run accepts the notes."""
        self.write(
            ["id", "case", "mine"],
            [["1", "alpha", "urgent"], ["2", "gamma", "normal"]],
        )
        self.lock(2)
        self.write(
            ["id", "case", "mine", "grader", "note"],
            [
                ["1", "beta", "urgent", "urgent", "2026-03-01 rewrote the wording"],
                ["2", "gamma", "urgent", "urgent", "2026-04-02 relabeled it"],
            ],
        )
        code, out, err = run_golden(["score", self.path])
        expected = (
            "answers changed since the lock, each with a dated note: 2\n"
            "  case 1 (case text changed): 2026-03-01 rewrote the wording\n"
            '  case 2 (locked "normal", now "urgent"): 2026-04-02 relabeled it\n'
            "2 of 2, bar 2: PASS\n"
            "agreement beyond chance (Cohen's kappa): not defined, every answer is the same label\n"
            "95% bootstrap interval: 2 to 2 of 2\n"
        )
        self.assertEqual(err, "")
        self.assertEqual(out, expected)
        self.assertEqual(code, 0)
        self.assertNotIn(PASS_SENTENCE, out)
        self.assertNotIn(NOT_YET_SENTENCE, out)
        script = self.script("later.py", "import sys\nsys.stdin.read()\nsys.stdout.write('later\\n')\n")
        code, out, err = run_golden(["run", self.path, "--", sys.executable, script])
        self.assertEqual(code, 0, err + out)
        self.assertEqual(out, "ran 0: answered 0, could not 0\n")

    def test_correction_line_names_what_changed(self):
        """source: a dated correction names the locked answer and the answer now, or that the case text changed, or both."""
        self.write(
            ["id", "case", "mine"],
            [
                ["1", "alpha", "normal"],
                ["2", "beta", "normal"],
                ["3", "gamma", "normal"],
            ],
        )
        self.lock(1)
        self.write(
            ["id", "case", "mine", "grader", "note"],
            [
                ["1", "alpha", "urgent", "urgent", "2026-01-15 answer only"],
                ["2", "beta changed", "normal", "normal", "2026-01-16 text only"],
                ["3", "gamma changed", "urgent", "urgent", "2026-01-17 both changed"],
            ],
        )
        code, out, err = run_golden(["score", self.path])
        self.assertEqual(err, "")
        self.assertEqual(code, 0, out)
        self.assertIn('  case 1 (locked "normal", now "urgent"): 2026-01-15 answer only\n', out)
        self.assertIn("  case 2 (case text changed): 2026-01-16 text only\n", out)
        self.assertIn(
            '  case 3 (locked "normal", now "urgent", case text changed): 2026-01-17 both changed\n',
            out,
        )

    def test_a_later_change_shows_the_current_answer(self):
        """source: a row that already has a dated note still passes, and the line shows the answer it has now."""
        self.write(["id", "case", "mine"], [["1", "alpha", "normal"]])
        self.lock(1)
        self.write(
            ["id", "case", "mine", "grader", "note"],
            [["1", "alpha", "urgent", "urgent", "2026-01-15 first change"]],
        )
        code, out, err = run_golden(["score", self.path])
        self.assertEqual(code, 0, err + out)
        self.assertIn('  case 1 (locked "normal", now "urgent"): 2026-01-15 first change\n', out)
        self.write(
            ["id", "case", "mine", "grader", "note"],
            [["1", "alpha", "spam", "spam", "2026-01-15 first change"]],
        )
        later_code, later_out, later_err = run_golden(["score", self.path])
        self.assertEqual(later_code, code)
        self.assertEqual(later_err, "")
        self.assertIn('  case 1 (locked "normal", now "spam"): 2026-01-15 first change\n', later_out)
        self.assertNotIn('now "urgent"', later_out)

    def test_two_label_table_prints_kappa_0_40(self):
        """source: counts 20, 5, 10, 15 print agreement beyond chance of 0.40."""
        labeled = [("a", "a")] * 20 + [("a", "b")] * 5 + [("b", "a")] * 10 + [("b", "b")] * 15
        code, out, err = self.scored(labeled, 1)
        self.assertEqual(code, 0, err)
        self.assertIn("35 of 50, bar 1: PASS\n", out)
        self.assertIn("agreement beyond chance (Cohen's kappa): 0.40\n", out)
        self.assertNotIn("Traceback", err + out)

    def test_interval_sentences_follow_the_bar(self):
        """source: the bar sentence appears only when the bar is above the low end and at most the high end."""
        labeled = [("yes", "yes")] * 16 + [("yes", "no")] * 4
        checks = [(16, True, 0), (19, True, 1), (13, False, 0)]
        for bar, want_sentence, want_code in checks:
            with self.subTest(bar=bar):
                lock_path = self.path + ".lock"
                if os.path.exists(lock_path):
                    os.remove(lock_path)
                code, out, err = self.scored(
                    labeled, bar, ["--resamples", "400", "--seed", "0"]
                )
                lines = []
                for index in range(17, 21):
                    lines.append(
                        "case %d: you said yes, grader said no. Who is wrong?" % index
                    )
                lines.append(
                    'the grader used a label you never did: "no" (cases 17, 18, 19, 20)'
                )
                status = "PASS" if 16 >= bar else "NOT YET"
                lines.append("16 of 20, bar %d: %s" % (bar, status))
                lines.append("agreement beyond chance (Cohen's kappa): 0.00")
                lines.append("95% bootstrap interval: 13 to 19 of 20")
                if bar > 13 and bar <= 19:
                    lines.append(PASS_SENTENCE if 16 >= bar else NOT_YET_SENTENCE)
                expected = "\n".join(lines) + "\n"
                self.assertEqual(err, "")
                self.assertEqual(out, expected)
                self.assertEqual(code, want_code)
                if want_sentence:
                    self.assertIn("the bar sits inside that interval:", out)
                else:
                    self.assertNotIn("the bar sits inside that interval:", out)

    def test_no_sentence_when_the_bar_is_above_the_interval(self):
        """source: a NOT YET score whose bar is above the interval does not add the sentence."""
        labeled = [("yes", "yes")] * 5 + [("yes", "no")] * 5
        code, out, err = self.scored(labeled, 10)
        lines = []
        for index in range(6, 11):
            lines.append("case %d: you said yes, grader said no. Who is wrong?" % index)
        lines.append('the grader used a label you never did: "no" (cases 6, 7, 8, 9, 10)')
        lines.append("5 of 10, bar 10: NOT YET")
        lines.append("agreement beyond chance (Cohen's kappa): 0.00")
        lines.append("95% bootstrap interval: 2 to 8 of 10")
        self.assertEqual(err, "")
        self.assertEqual(out, "\n".join(lines) + "\n")
        self.assertEqual(code, 1)
        self.assertNotIn("the bar sits inside that interval:", out)

    def test_no_sentence_when_every_case_matches_and_the_bar_is_low(self):
        """source: a PASS whose bar is below the interval does not add the sentence."""
        code, out, err = self.scored([("urgent", "urgent")] * 3, 1)
        expected = (
            "3 of 3, bar 1: PASS\n"
            "agreement beyond chance (Cohen's kappa): not defined, every answer is the same label\n"
            "95% bootstrap interval: 3 to 3 of 3\n"
        )
        self.assertEqual(err, "")
        self.assertEqual(out, expected)
        self.assertEqual(code, 0)
        self.assertNotIn("the bar sits inside that interval:", out)

    def test_defaults_match_2000_resamples_and_seed_0(self):
        """source: score defaults to 2000 resamples and seed 0."""
        labeled = [("yes", "yes")] * 7 + [("yes", "no")] * 3
        code, out, err = self.scored(labeled, 1)
        self.assertEqual(code, 0, err)
        code2, out2, err2 = run_golden(
            ["score", self.path, "--resamples", "2000", "--seed", "0"]
        )
        self.assertEqual(code2, 0, err2)
        self.assertEqual(out, out2)
        self.assertIn("95% bootstrap interval: 4 to 10 of 10\n", out)

    def test_seed_and_resamples_change_the_interval(self):
        """source: --seed and --resamples change the interval, and bad values exit 2."""
        labeled = [("yes", "yes")] * 7 + [("yes", "no")] * 3
        code, out, err = self.scored(labeled, 1, ["--resamples", "40", "--seed", "1"])
        self.assertEqual(code, 0, err)
        self.assertIn("95% bootstrap interval: 4 to 9 of 10\n", out)
        code, out2, err = run_golden(["score", self.path, "--resamples", "40", "--seed", "2"])
        self.assertEqual(code, 0, err)
        self.assertIn("95% bootstrap interval: 3 to 9 of 10\n", out2)
        self.assertNotEqual(out, out2)
        code, out3, err = run_golden(["score", self.path, "--resamples", "15", "--seed", "1"])
        self.assertEqual(code, 0, err)
        self.assertIn("95% bootstrap interval: 5 to 8 of 10\n", out3)
        code, out4, err = run_golden(["score", self.path, "--resamples", "1", "--seed", "0"])
        self.assertEqual(code, 0, err)
        self.assertRegex(out4, r"95% bootstrap interval: ([0-9]+) to \1 of 10")
        code, out5, err = run_golden(["score", self.path, "--seed", "-1"])
        self.assertNotEqual(code, 2)
        self.assertIn("95% bootstrap interval:", out5)
        self.assertNotIn("Traceback", err)
        bad = [
            (["--resamples", "0"], "--resamples needs a positive whole number"),
            (["--resamples", "-3"], "--resamples needs a positive whole number"),
            (["--resamples", "2.5"], "--resamples needs a positive whole number"),
            (["--resamples", "abc"], "--resamples needs a positive whole number"),
            (["--seed", "abc"], "--seed needs a whole number"),
            (["--seed", "1.5"], "--seed needs a whole number"),
        ]
        for extra, snippet in bad:
            with self.subTest(extra=extra):
                code, out, err = run_golden(["score", self.path] + extra)
                self.assertEqual(code, 2, err + out)
                self.assertEqual(out, "")
                self.assertIn(snippet, err)
                self.assertNotIn("Traceback", err)
        code, out, err = run_golden(["score", self.path, "--resamples"])
        self.assertEqual(code, 2)
        self.assertIn("missing value for --resamples", err)
        self.assertNotIn("Traceback", err)


class LockCheckTests(CommandCase):
    def test_no_lock_file(self):
        """source: run and score both say to run lock first when the lock file is missing."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        os.remove(self.path + ".lock")
        expected = "%s: no lock file. Run lock first.\n" % (self.path + ".lock")
        self.refuse(expected, lock_exists=False)

    def test_lock_that_is_not_json(self):
        """source: a lock that is not JSON, or not an object, is refused."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        lock_path = self.path + ".lock"
        expected = "%s: lock file is not valid JSON\n" % lock_path
        with open(lock_path, "w", encoding="utf-8") as handle:
            handle.write("{")
        self.refuse(expected)
        with open(lock_path, "w", encoding="utf-8") as handle:
            handle.write("[]\n")
        self.refuse(expected)

    def test_missing_fields_and_a_bad_version_or_bar(self):
        """source: a lock that lacks a field, or has a version other than 1, is refused."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        lock_path = self.path + ".lock"
        original = read_bytes(lock_path)

        def restore():
            with open(lock_path, "wb") as handle:
                handle.write(original)

        def drop_locked(data):
            del data["locked"]

        restore()
        self.edit_lock(drop_locked)
        self.refuse("%s: lock file lacks locked\n" % lock_path)

        def drop_two(data):
            del data["version"]
            del data["bar"]

        restore()
        self.edit_lock(drop_two)
        self.refuse(
            "%s: lock file lacks version\n%s: lock file lacks bar\n" % (lock_path, lock_path)
        )

        def bump(data):
            data["version"] = 2

        restore()
        self.edit_lock(bump)
        self.refuse("%s: lock file has version 2, expected 1\n" % lock_path)

        def bad_bar(data):
            data["bar"] = 1.5

        restore()
        self.edit_lock(bad_bar)
        self.refuse("%s: lock file lacks a whole-number bar\n" % lock_path)

        def bad_answers(data):
            data["answers"] = []

        restore()
        self.edit_lock(bad_answers)
        self.refuse("%s: lock file lacks answers\n" % lock_path)

    def test_removed_case_is_refused(self):
        """source: a case that was locked and then removed is refused."""
        self.write(
            ["id", "case", "mine"],
            [["1", "alpha", "urgent"], ["2", "beta", "normal"]],
        )
        self.lock(1)
        self.write(["id", "case", "mine"], [["2", "beta", "normal"]])
        expected = "%s: case 1 is in the lock but gone from the file\n" % (self.path + ".lock")
        self.refuse(expected)

    def test_added_case_is_refused(self):
        """source: a case added after the lock is refused."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        self.write(
            ["id", "case", "mine"],
            [["1", "alpha", "urgent"], ["3", "gamma", "normal"]],
        )
        expected = "%s: case 3 is not in the lock\n" % self.path
        self.refuse(expected)

    def test_removed_and_added_are_both_reported(self):
        """source: lock check prints one line per problem."""
        self.write(
            ["id", "case", "mine"],
            [["1", "alpha", "urgent"], ["2", "beta", "normal"]],
        )
        self.lock(1)
        self.write(
            ["id", "case", "mine"],
            [["2", "beta", "normal"], ["3", "gamma", "urgent"]],
        )
        lock_path = self.path + ".lock"
        expected = (
            "%s: case 1 is in the lock but gone from the file\n%s: case 3 is not in the lock\n"
            % (lock_path, self.path)
        )
        self.refuse(expected)

    def test_answer_change_without_a_note_is_refused(self):
        """source: a changed answer needs a dated note, and the line names both labels."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        self.write(["id", "case", "mine"], [["1", "alpha", "normal"]])
        expected = (
            '%s: case 1: answer changed since the lock (locked "urgent", now "normal")'
            " and the note has no dated reason\n" % self.path
        )
        self.refuse(expected)

    def test_text_change_without_a_note_is_refused(self):
        """source: a changed case hash needs a dated note."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        self.write(["id", "case", "mine"], [["1", "alpha ", "urgent"]])
        expected = (
            "%s: case 1: case text changed since the lock and the note has no dated reason\n"
            % self.path
        )
        self.refuse(expected)

    def test_note_date_must_be_a_real_calendar_day(self):
        """source: 2026-02-30 is refused, and a real leap day with a reason is accepted."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        refused = (
            '%s: case 1: answer changed since the lock (locked "urgent", now "normal")'
            " and the note has no dated reason\n" % self.path
        )
        for note in ("2026-02-30 x", "2026-02-29 x"):
            with self.subTest(note=note):
                self.write(
                    ["id", "case", "mine", "note"],
                    [["1", "alpha", "normal", note]],
                )
                self.refuse(refused)
        self.write(
            ["id", "case", "mine", "grader", "note"],
            [["1", "alpha", "normal", "normal", "2024-02-29 leap day counted"]],
        )
        code, out, err = run_golden(["score", self.path])
        self.assertEqual(code, 0, err + out)
        self.assertIn("answers changed since the lock, each with a dated note: 1\n", out)
        self.assertIn(
            '  case 1 (locked "urgent", now "normal"): 2024-02-29 leap day counted\n',
            out,
        )
        self.assertIn("1 of 1, bar 1: PASS\n", out)

    def test_note_must_start_with_a_date_and_a_reason(self):
        """source: a note needs a real date at the start and a reason after it."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        self.lock(1)
        refused = (
            '%s: case 1: answer changed since the lock (locked "urgent", now "normal")'
            " and the note has no dated reason\n" % self.path
        )
        for note in ("2026-01-15", "2026-01-15 ", "see 2026-01-15 because"):
            with self.subTest(note=note):
                self.write(
                    ["id", "case", "mine", "note"],
                    [["1", "alpha", "normal", note]],
                )
                self.refuse(refused)

    def test_same_label_with_different_case_is_not_a_correction(self):
        """source: changing Urgent to urgent does not change the locked answer."""
        self.write(["id", "case", "mine"], [["1", "alpha", "Urgent "]])
        self.lock(1)
        self.write(["id", "case", "mine", "grader"], [["1", "alpha", "urgent", "urgent"]])
        code, out, err = run_golden(["score", self.path])
        self.assertEqual(code, 0, err + out)
        self.assertNotIn("changed since the lock", out)
        self.assertNotIn("changed since the lock", err)
        self.assertIn("1 of 1, bar 1: PASS\n", out)


class SetCellTests(CommandCase):
    def test_set_cell_sets_an_existing_cell(self):
        """source: set_cell replaces one cell on the row whose id matches after trimming."""
        self.write(
            ["id", "case", "mine"],
            [["  12  ", "Hello, friend", "normal"], ["13", "beta", "spam"]],
        )
        code, out, err = run_set_cell([self.path, "12", "mine", "urgent"])
        self.assertEqual(code, 0, err + out)
        self.assertEqual(out, "")
        self.assertEqual(err, "")
        header, rows = read_csv(self.path)
        self.assertEqual(header, ["id", "case", "mine"])
        self.assertEqual(rows, [["  12  ", "Hello, friend", "urgent"], ["13", "beta", "spam"]])

    def test_set_cell_adds_a_missing_column(self):
        """source: a missing column is added last, and every other row gets an empty cell there."""
        self.write(
            ["id", "case", "mine"],
            [["12", "alpha", "urgent"], ["13", "beta", "spam"]],
        )
        code, out, err = run_set_cell(
            [self.path, "12", "note", "2026-01-15 the refund policy changed"]
        )
        self.assertEqual(code, 0, err + out)
        self.assertEqual(out, "")
        self.assertEqual(err, "")
        header, rows = read_csv(self.path)
        self.assertEqual(header, ["id", "case", "mine", "note"])
        self.assertEqual(
            rows,
            [
                ["12", "alpha", "urgent", "2026-01-15 the refund policy changed"],
                ["13", "beta", "spam", ""],
            ],
        )

    def test_set_cell_unknown_id_leaves_the_bytes(self):
        """source: an unknown id exits 2 and leaves the file bytes alone."""
        self.write(["id", "case", "mine"], [["12", "alpha", "normal"]])
        before = read_bytes(self.path)
        code, out, err = run_set_cell([self.path, "99", "mine", "urgent"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err.count("\n"), 1)
        self.assertNotIn("Traceback", err)
        self.assertEqual(read_bytes(self.path), before)

    def test_set_cell_wrong_argument_count(self):
        """source: the wrong number of arguments exits 2 and leaves the file bytes alone."""
        self.write(["id", "case", "mine"], [["12", "alpha", "normal"]])
        before = read_bytes(self.path)
        for args in (
            [],
            [self.path, "12", "mine"],
            [self.path, "12", "mine", "urgent", "extra"],
        ):
            with self.subTest(args=args):
                code, out, err = run_set_cell(args)
                self.assertEqual(code, 2, err + out)
                self.assertEqual(out, "")
                self.assertEqual(err.count("\n"), 1)
                self.assertNotIn("Traceback", err)
                self.assertEqual(read_bytes(self.path), before)

    def test_save_keeps_permission_bits(self):
        """source: a save keeps the permission bits a table already had."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        os.chmod(self.path, 0o640)
        self.lock(1)
        script = self.script("ok.py", "import sys\nsys.stdin.read()\nsys.stdout.write('ok\\n')\n")
        code, out, err = run_golden(["run", self.path, "--", sys.executable, script])
        self.assertEqual(code, 0, err + out)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o640)
        code, out, err = run_set_cell([self.path, "1", "mine", "normal"])
        self.assertEqual(code, 0, err + out)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o640)

    def test_new_lock_matches_a_plain_open(self):
        """source: a new lock file gets the permission bits a plain open would give it."""
        self.write(["id", "case", "mine"], [["1", "alpha", "urgent"]])
        probe = os.path.join(self.folder, "probe.txt")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("x")
        want = os.stat(probe).st_mode & 0o777
        self.lock(1)
        self.assertEqual(os.stat(self.path + ".lock").st_mode & 0o777, want)

    def test_save_follows_a_symbolic_link(self):
        """source: a save through a symbolic link writes the file it points at and leaves the link in place."""
        other = os.path.join(self.folder, "other")
        os.makedirs(other)
        target = os.path.join(other, "cases.csv")
        write_csv(target, ["id", "case", "mine"], [["1", "alpha", "urgent"]])
        link = os.path.join(self.folder, "link.csv")
        os.symlink(os.path.join("other", "cases.csv"), link)
        code, out, err = run_golden(["lock", link, "--bar", "1"])
        self.assertEqual(code, 0, err + out)
        self.assertTrue(os.path.islink(link))
        self.assertTrue(os.path.isfile(link + ".lock"))
        self.assertFalse(os.path.exists(target + ".lock"))
        script = self.script("ok.py", "import sys\nsys.stdin.read()\nsys.stdout.write('ok\\n')\n")
        code, out, err = run_golden(["run", link, "--", sys.executable, script])
        self.assertEqual(code, 0, err + out)
        self.assertTrue(os.path.islink(link))
        header, rows = read_csv(target)
        self.assertEqual(header[-1], "grader")
        self.assertEqual(rows[0][-1], "ok")
        code, out, err = run_set_cell([link, "1", "mine", "normal"])
        self.assertEqual(code, 0, err + out)
        self.assertTrue(os.path.islink(link))
        header, rows = read_csv(target)
        self.assertEqual(rows[0][2], "normal")


if __name__ == "__main__":
    unittest.main()

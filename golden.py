#!/usr/bin/env python3
import csv
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import unicodedata
from agreement import bootstrap, interval, kappa, matches, norm

class Fail(Exception):
    def __init__(self, messages):
        if isinstance(messages, str):
            messages = [messages]
        Exception.__init__(self, "\n".join(messages))
        self.messages = messages

def creation_mode():
    current = os.umask(0)
    os.umask(current)
    return 0o666 & ~current

def atomic_write(path, text):
    requested = path
    if os.path.islink(path):
        link = os.readlink(path)
        path = link if os.path.isabs(link) else os.path.join(os.path.dirname(os.path.abspath(requested)), link)
    kept = os.stat(path).st_mode & 0o777 if os.path.exists(path) else creation_mode()
    folder = os.path.dirname(os.path.abspath(path)) or "."
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".golden-", suffix=".tmp", dir=folder)
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
        os.chmod(temporary, kept)
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise Fail("%s: cannot write file: %s" % (requested, exc.strerror or str(exc)))
    finally:
        if temporary is not None and os.path.exists(temporary):
            try: os.unlink(temporary)
            except OSError: pass

def save_table(path, header, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    atomic_write(path, buffer.getvalue())

def read_table(path):
    csv.field_size_limit(2147483647)
    try:
        handle = open(path, encoding="utf-8-sig", newline="")
    except FileNotFoundError:
        raise Fail("%s: file not found" % path)
    except OSError:
        raise Fail("%s: cannot read file" % path)
    with handle:
        try: text = handle.read()
        except UnicodeDecodeError: raise Fail("%s: file is not valid UTF-8" % path)
        reader = csv.reader(io.StringIO(text))
        try:
            return _parsed_rows(path, reader)
        except csv.Error as exc:
            raise Fail("%s: cannot parse the CSV: %s" % (path, exc))

def _parsed_rows(path, reader):
    try:
        header = next(reader)
    except StopIteration:
        raise Fail("%s:1: missing required column id" % path)
    header_line, seen = reader.line_num, {}
    for name in header:
        if name in seen:
            raise Fail("%s:%d: duplicated column name %s" % (path, header_line, name))
        seen[name] = True
    for name in ("id", "case", "mine"):
        if name not in seen:
            raise Fail("%s:%d: missing required column %s" % (path, header_line, name))
    rows, ids, end = [], {}, header_line
    id_at, mine_at = header.index("id"), header.index("mine")
    for row in reader:
        start, end = end + 1, reader.line_num
        if len(row) != len(header):
            raise Fail("%s:%d: row has %d cells, header has %d" % (path, start, len(row), len(header)))
        ident = row[id_at].strip()
        if ident == "":
            raise Fail("%s:%d: empty id" % (path, start))
        if ident in ids:
            raise Fail("%s:%d: duplicated id %s" % (path, start, ident))
        ids[ident] = True
        if row[mine_at].strip() == "":
            raise Fail("%s:%d: empty mine" % (path, start))
        rows.append(row)
    if not rows:
        raise Fail("%s:%d: no data rows" % (path, header_line))
    return header, rows

def case_digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def cmd_lock(args):
    path, options, _again = parse_args(args, {"--bar": True})
    header, rows = read_table(path)
    lock_path = path + ".lock"
    if os.path.exists(lock_path):
        locked, bar_text = "unknown", "unknown"
        try:
            data = json.load(open(lock_path, encoding="utf-8"))
            if isinstance(data, dict):
                locked, bar_text = data.get("locked", locked), data.get("bar", bar_text)
        except (OSError, ValueError):
            pass
        raise Fail(
            "%s already exists (locked %s, bar %s). Delete it and clear the grader column to start over."
            % (lock_path, locked, bar_text)
        )
    filled = None
    if "grader" in header:
        for row in rows:
            if row[header.index("grader")].strip() != "":
                filled = row[header.index("id")].strip()
                break
    if filled is not None:
        raise Fail("%s: answers must be locked before the grader runs (case %s)" % (path, filled))
    text, total = options.get("--bar"), len(rows)
    if text is None or re.match(r"^[0-9]+$", text) is None or not 1 <= int(text) <= total:
        raise Fail("%s: bar must be a whole number from 1 to %d" % (path, total))
    bar = int(text)
    id_at, case_at, mine_at = header.index("id"), header.index("case"), header.index("mine")
    answers = {}
    for row in rows:
        answers[row[id_at].strip()] = {"mine": norm(row[mine_at]), "case_sha256": case_digest(row[case_at])}
    payload = {"version": 1, "bar": bar, "cases": total, "locked": datetime.date.today().isoformat(), "answers": answers}
    atomic_write(lock_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print("locked %d cases, bar %d of %d. Now run the grader." % (total, bar, total))
    return 0

def note_is_dated(note):
    if not isinstance(note, str) or re.match(r"^\d{4}-\d{2}-\d{2}\s+\S", note) is None:
        return False
    day = note[0:10]
    try:
        datetime.date.fromisoformat(day)
    except ValueError:
        return False
    return True

def load_lock(path):
    lock_path = path + ".lock"
    if not os.path.isfile(lock_path):
        return ["%s: no lock file. Run lock first." % lock_path], None
    try:
        with open(lock_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError:
        return ["%s: cannot read lock file" % lock_path], None
    except ValueError:
        return ["%s: lock file is not valid JSON" % lock_path], None
    if not isinstance(data, dict):
        return ["%s: lock file is not valid JSON" % lock_path], None
    problems = []
    for field in ("version", "bar", "cases", "locked", "answers"):
        if field not in data:
            problems.append("%s: lock file lacks %s" % (lock_path, field))
    if problems:
        return problems, None
    if type(data["version"]) is not int or data["version"] != 1:
        problems.append("%s: lock file has version %s, expected 1" % (lock_path, data["version"]))
    if type(data["bar"]) is not int:
        problems.append("%s: lock file lacks a whole-number bar" % lock_path)
    answers = data["answers"]
    if not isinstance(answers, dict):
        return problems + ["%s: lock file lacks answers" % lock_path], None
    usable = True
    for ident, entry in answers.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("mine"), str) or not isinstance(entry.get("case_sha256"), str):
            problems.append("%s: lock file lacks mine or case_sha256 for case %s" % (lock_path, ident))
            usable = False
    return (problems, data) if usable else (problems, None)

def row_change(row, header, entry):
    mine_now = norm(row[header.index("mine")])
    changed_text = case_digest(row[header.index("case")]) != entry["case_sha256"]
    return mine_now != entry["mine"], changed_text, mine_now

def check_lock(path, header, rows):
    problems, data = load_lock(path)
    if data is None:
        return problems, None
    answers, id_at = data["answers"], header.index("id")
    note_at = header.index("note") if "note" in header else None
    seen, order = {}, []
    for row in rows:
        ident = row[id_at].strip()
        seen[ident], order = row, order + [ident]
    lock_path = path + ".lock"
    for ident in answers:
        if ident not in seen:
            problems.append("%s: case %s is in the lock but gone from the file" % (lock_path, ident))
    for ident in order:
        if ident not in answers:
            problems.append("%s: case %s is not in the lock" % (path, ident))
    for ident in order:
        if ident not in answers:
            continue
        mine_changed, text_changed, mine_now = row_change(seen[ident], header, answers[ident])
        if not mine_changed and not text_changed:
            continue
        note = seen[ident][note_at] if note_at is not None else ""
        if not note_is_dated(note):
            if mine_changed:
                problems.append(
                    '%s: case %s: answer changed since the lock (locked "%s", now "%s") and the note has no dated reason'
                    % (path, ident, answers[ident]["mine"], mine_now)
                )
            else:
                problems.append("%s: case %s: case text changed since the lock and the note has no dated reason" % (path, ident))
    return problems, data

def pair_for(mine_text, grader_text):
    left = norm(mine_text)
    if grader_text.strip() == "":
        right = ""
    else:
        right = norm(grader_text)
    return left, right

def collect_pairs(header, rows):
    id_at, mine_at = header.index("id"), header.index("mine")
    grader_at = header.index("grader") if "grader" in header else None
    lines, pairs = [], []
    for row in rows:
        ident, mine_raw = row[id_at].strip(), row[mine_at]
        grader_raw = row[grader_at] if grader_at is not None else ""
        left, right = pair_for(mine_raw, grader_raw)
        pairs.append((left, right))
        if left == right:
            continue
        if grader_raw.strip() == "":
            lines.append("case %s: you said %s, grader gave no answer." % (ident, mine_raw.strip()))
        else:
            lines.append("case %s: you said %s, grader said %s. Who is wrong?" % (ident, mine_raw.strip(), grader_raw.strip()))
    return lines, pairs

def unused_label_lines(header, rows):
    if "grader" not in header:
        return []
    mine_at, grader_at, id_at = header.index("mine"), header.index("grader"), header.index("id")
    mine_labels, groups = set(norm(row[mine_at]) for row in rows), {}
    for row in rows:
        raw = row[grader_at]
        if raw.strip() == "":
            continue
        label = norm(raw)
        if label not in mine_labels:
            groups.setdefault(label, []).append(row[id_at].strip())
    return [
        'the grader used a label you never did: "%s" (cases %s)' % (label, ", ".join(groups[label]))
        for label in sorted(groups)
    ]

def change_detail(mine_changed, text_changed, locked, now):
    answer = ('locked "%s", now "%s"' % (locked, now)) if mine_changed else ""
    text = "case text changed" if text_changed else ""
    return ", ".join(bit for bit in (answer, text) if bit)

def correction_lines(header, rows, answers):
    id_at = header.index("id")
    note_at = header.index("note") if "note" in header else None
    found = []
    for row in rows:
        ident = row[id_at].strip()
        if ident not in answers:
            continue
        mine_changed, text_changed, mine_now = row_change(row, header, answers[ident])
        if mine_changed or text_changed:
            detail = change_detail(mine_changed, text_changed, answers[ident]["mine"], mine_now)
            found.append((ident, detail, (row[note_at] if note_at is not None else "").strip()))
    return found

def cmd_score(args):
    path, options, _again = parse_args(args, {"--resamples": True, "--seed": True})
    resamples = need_int(options.get("--resamples", "2000"), "--resamples", True)
    seed = need_int(options.get("--seed", "0"), "--seed", False)
    header, rows = read_table(path)
    problems, data = check_lock(path, header, rows)
    if problems:
        raise Fail(problems)
    miss_lines, pairs = collect_pairs(header, rows)
    for line in miss_lines:
        print(line)
    for line in unused_label_lines(header, rows):
        print(line)
    changed = correction_lines(header, rows, data["answers"])
    if changed:
        print("answers changed since the lock, each with a dated note: %d" % len(changed))
        for ident, detail, note in changed:
            print("  case %s (%s): %s" % (ident, detail, note))
    score, total, bar = matches(pairs), len(rows), data["bar"]
    passed = score >= bar
    print("%d of %d, bar %d: %s" % (score, total, bar, "PASS" if passed else "NOT YET"))
    value = kappa(pairs)
    if value is None:
        print("agreement beyond chance (Cohen's kappa): not defined, every answer is the same label")
    else:
        print("agreement beyond chance (Cohen's kappa): %.2f" % value)
    lo, hi = interval(bootstrap(pairs, matches, resamples, seed))
    print("95%% bootstrap interval: %s to %s of %d" % (lo, hi, total))
    if lo is not None and hi is not None and bar > lo and bar <= hi:
        if passed:
            print("the bar sits inside that interval: on a fresh set of cases this grader could land under it.")
        else:
            print("the bar sits inside that interval: this miss could be luck. Add cases before you decide.")
    return 0 if passed else 1

def need_int(text, flag, positive):
    pattern = r"^[0-9]+$" if positive else r"^-?[0-9]+$"
    if re.match(pattern, text) is None or (positive and int(text) < 1):
        raise Fail("usage: %s needs %s" % (flag, "a positive whole number" if positive else "a whole number"))
    return int(text)

def command_can_start(cmd):
    program = cmd[0]
    candidate = program if (os.path.isabs(program) or os.sep in program) else shutil.which(program)
    return bool(candidate) and os.path.isfile(candidate) and os.access(candidate, os.X_OK)

def kill_group(proc):
    if hasattr(os, "killpg"):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    proc.kill()

def grade_one(cmd, case_text, timeout):
    payload = case_text.encode("utf-8") + b"\n"
    with subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True) as proc:
        try:
            out, _err = proc.communicate(payload, timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_group(proc)
            return None, "timed out after %d %s" % (timeout, "second" if timeout == 1 else "seconds")
        except BaseException:
            kill_group(proc)
            raise
        if proc.returncode != 0:
            return None, "exited %d" % proc.returncode
        for line in out.decode("utf-8", errors="replace").splitlines():
            label = line.strip()
            if label == "":
                continue
            if any(unicodedata.category(char) == "Cc" for char in label):
                return None, "gave a label with a control character"
            return label, None
        return None, "gave no label"

def cmd_run(args):
    if "--" not in args:
        raise Fail("usage: python3 golden.py run FILE [--again] [--timeout SEC] -- CMD")
    cut = args.index("--")
    cmd = args[cut + 1 :]
    if not cmd:
        raise Fail("usage: python3 golden.py run FILE [--again] [--timeout SEC] -- CMD")
    path, options, again = parse_args(args[:cut], {"--timeout": True}, True)
    timeout = need_int(options.get("--timeout", "120"), "--timeout", True)
    header, rows = read_table(path)
    problems, _data = check_lock(path, header, rows)
    if problems:
        raise Fail(problems)
    if "grader" not in header:
        header.append("grader")
        for row in rows:
            row.append("")
    grader_at, id_at, case_at = header.index("grader"), header.index("id"), header.index("case")
    if not any(again or row[grader_at].strip() == "" for row in rows):
        print("ran 0: answered 0, could not 0")
        return 0
    if not command_can_start(cmd):
        raise Fail("%s: command cannot be started: %s" % (path, cmd[0]))
    if again:
        for row in rows:
            row[grader_at] = ""
        save_table(path, header, rows)
    tried = answered = could_not = 0
    for row in rows:
        if not again and row[grader_at].strip() != "":
            continue
        try:
            label, problem = grade_one(cmd, row[case_at], timeout)
        except OSError:
            raise Fail("%s: command cannot be started: %s" % (path, cmd[0]))
        tried += 1
        if problem is not None:
            could_not += 1
            sys.stderr.write("case %s: grader %s\n" % (row[id_at].strip(), problem))
        else:
            row[grader_at] = label
            answered += 1
        save_table(path, header, rows)
    print("ran %d: answered %d, could not %d" % (tried, answered, could_not))
    return 0 if could_not == 0 else 1

def parse_args(args, allowed, allow_again=False):
    path, options, again, index = None, {}, False, 0
    while index < len(args):
        item = args[index]
        if allow_again and item == "--again":
            again, index = True, index + 1
            continue
        if item in allowed:
            if index + 1 >= len(args):
                raise Fail("usage: missing value for %s" % item)
            options[item] = args[index + 1]
            index += 2
            continue
        if item.startswith("--"):
            raise Fail("usage: unknown option %s" % item)
        if path is not None:
            raise Fail("usage: unexpected argument %s" % item)
        path, index = item, index + 1
    if path is None:
        raise Fail("usage: missing file")
    return path, options, again

def on_terminate(signum, frame):
    raise SystemExit(143 if signum == signal.SIGTERM else 129)

def finish_stop(code):
    previous = None
    try:
        previous = signal.signal(signal.SIGPIPE, signal.SIG_IGN)
        sys.stderr.write("stopped\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        os._exit(code)
    if previous is not None:
        try: signal.signal(signal.SIGPIPE, previous)
        except (OSError, ValueError): pass
    return code

def main(argv):
    previous_term = signal.getsignal(signal.SIGTERM)
    previous_hup = signal.getsignal(signal.SIGHUP)
    signal.signal(signal.SIGTERM, on_terminate)
    replaced_hup = previous_hup != signal.SIG_IGN
    if replaced_hup:
        signal.signal(signal.SIGHUP, on_terminate)
    try:
        if len(argv) < 2 or argv[1] not in ("lock", "run", "score"):
            raise Fail("usage: python3 golden.py lock|run|score FILE ...")
        command = {"lock": cmd_lock, "run": cmd_run, "score": cmd_score}[argv[1]]
        return command(argv[2:])
    except Fail as exc:
        for message in exc.messages: sys.stderr.write(message + "\n")
        return 2
    except KeyboardInterrupt:
        return finish_stop(130)
    except SystemExit as exc:
        if exc.code not in (129, 143): raise
        return finish_stop(exc.code)
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        if replaced_hup: signal.signal(signal.SIGHUP, previous_hup)
if __name__ == "__main__":
    sys.exit(main(sys.argv))

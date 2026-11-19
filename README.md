# golden-set

This is a small tool for checking a grader against answers you wrote down first. A grader is any model or script that reads a case and prints a label. You freeze the cases, your answers, and the pass bar before the grader runs, then you see every disagreement, plus two numbers: how much of the agreement is more than chance, and how far a score on this few cases can move.

## What you get

`sh examples/demo.sh` runs the whole loop in a temporary folder and prints the transcript below. It works there on a copy of `examples/golden.csv` named `golden.csv`, so the file in this folder is never changed.

```
$ python3 golden.py lock golden.csv --bar 27
locked 30 cases, bar 27 of 30. Now run the grader.
$ python3 golden.py run golden.csv -- python3 examples/keyword_grader.py
ran 30: answered 30, could not 0
$ python3 golden.py score golden.csv
case 4: you said normal, grader said spam. Who is wrong?
case 7: you said normal, grader said urgent. Who is wrong?
case 12: you said normal, grader said urgent. Who is wrong?
27 of 30, bar 27: PASS
agreement beyond chance (Cohen's kappa): 0.85
95% bootstrap interval: 23 to 30 of 30
the bar sits inside that interval: on a fresh set of cases this grader could land under it.
$ python3 examples/set_cell.py golden.csv 12 mine urgent
$ python3 golden.py score golden.csv
golden.csv: case 12: answer changed since the lock (locked "normal", now "urgent") and the note has no dated reason
$ python3 examples/set_cell.py golden.csv 12 note "2026-01-15 the refund policy changed"
$ python3 golden.py score golden.csv
case 4: you said normal, grader said spam. Who is wrong?
case 7: you said normal, grader said urgent. Who is wrong?
answers changed since the lock, each with a dated note: 1
  case 12 (locked "normal", now "urgent"): 2026-01-15 the refund policy changed
28 of 30, bar 27: PASS
agreement beyond chance (Cohen's kappa): 0.90
95% bootstrap interval: 25 to 30 of 30
the bar sits inside that interval: on a fresh set of cases this grader could land under it.
```

## Quick start

Get this folder, then run:

```
sh examples/demo.sh
```

The demo prints each command on a line that starts with `$ `, then the output of that command.

`lock` freezes the 30 made-up tickets and sets the bar at 27. `run` feeds each ticket to `examples/keyword_grader.py`, a small stand-in that labels from keyword rules. The first `score` finds three disagreements. 27 of 30 meets the bar, so the result is PASS. The kappa and the interval follow. The last sentence of that score says the bar sits inside the interval, so a fresh draw of cases like these could land under 27.

The demo then changes case 12's answer to the label the grader printed, using `examples/set_cell.py`, and scores again. That score refuses, because an answer changed after the lock and the row has no dated note. It names the locked label and the current one.

The demo adds the note `2026-01-15 the refund policy changed` to case 12 with `examples/set_cell.py` and scores a third time. Case 12 now counts as a match, the score is 28 of 30, and the correction is listed under the disagreements.

`examples/set_cell.py` changes one cell so that the demo needs no editor. Any editor or spreadsheet does the same job.

On your own file, write a CSV with the columns `id`, `case`, and `mine`. `mine` is your answer. Then:

```
python3 golden.py lock answers.csv --bar 27
python3 golden.py run answers.csv -- python3 your_grader.py
python3 golden.py score answers.csv
```

Replace `27` with the number of matches you want before you call the result a pass, and replace the command after `--` with your grader. The bar has to be a whole number from 1 to the number of cases.

## The four steps

1. Write the cases and your answers before the grader sees any of them. Each row needs an `id`, the case text, and your label in `mine`.
2. Set the bar before the first run. `python3 golden.py lock answers.csv --bar B` writes `answers.csv.lock` beside the file and records the normalized answers, a hash of each case, and the bar. Commit the lock file together with the CSV, because it is the record that the answers and the bar were fixed before the grader ran.
3. Run the grader and score it. `python3 golden.py run answers.csv -- python3 your_grader.py` fills a `grader` column. `python3 golden.py score answers.csv` lists every disagreement and prints the score, the kappa, and the interval.
4. For each disagreement, decide who is wrong. Fix the grader, or fix your answer. An answer or a case that changes after the lock needs a note that starts with a real calendar date and a reason, such as `2026-01-15 the refund policy changed`. Then grade every case again with `python3 golden.py run answers.csv --again -- python3 your_grader.py`.

## Your own grader

The case text arrives on standard input, followed by a newline. The label is the first non-blank line of standard output, with outer spaces removed. A non-zero exit, a timeout, or no non-blank line leaves that cell blank, and a blank cell counts as a miss. What the grader writes to standard error is not shown: a failing grader gives only a line such as `case 4: grader exited 1`, so run the grader by hand on that case to read its own error. At the time limit the grader and every process in its process group are stopped, and that case's cell stays blank. A label that holds a control character is refused and the cell stays blank. A grader that leaves a background process holding its output open reads as a timeout.

```sh
#!/bin/sh
text=$(cat)
label=$(your-model-command "$text")
printf '%s\n' "$label"
exit 0
```

Put any local or hosted model call in `your-model-command`, and make it print exactly one label.

## What the numbers mean

The score is how many cases the grader labeled the same way you did. Two labels match when they are equal after outer spaces are removed, each run of white space inside counts as one space, and letter case is ignored. The bar is the count you froze at lock time, and it does not change later. The line reads `S of N, bar B: PASS` when S is at least B, and `NOT YET` when it is lower. The command exits 0 on PASS and 1 on NOT YET.

Cohen's kappa is the agreement that remains after subtracting the agreement you would expect from the label rates alone, if the two labels on each case were independent. It is not defined when every answer is the same label, because that expected agreement is already 1.

The 95% bootstrap interval says how far the match count moves when the cases in the file are redrawn. Each redraw picks as many cases as the file holds, with replacement, and counts the matches. The default is 2000 redraws, from a fixed seed, and `--resamples` and `--seed` change those. The counts are sorted. With m counts left, the low end is the value at index `int(m * 0.025)` and the high end is the value at index `int(m * 0.975) - 1`. When the bar is above the low end and at most the high end, a PASS adds: a fresh set of cases like these could land under the bar. A NOT YET adds: this miss could be luck, so add cases before you decide.

## Exit codes

`lock` exits 0 when the lock was written. `run` exits 0 when every case it tried got a label and 1 when at least one did not. `score` exits 0 on PASS and 1 on NOT YET. Every command exits 2 for a usage error, a bad file, a bad lock, a grader command that cannot be started or a file the system will not let it write, with the reason on standard error. A stop (Ctrl-C, a terminate signal or a closed terminal) exits 130, 143 or 129: the grader is stopped and the labels written so far are kept. `run --again` clears the grader column first, so after a stop a plain `run` grades the rows that are still blank.

## Limits

The lock is a tripwire for an honest user and can be deleted. To start over, delete the lock file and clear the grader column.

The tool never calls a model itself. It only runs the command you pass after `--`.

Labels must match as whole labels, apart from outer spaces, runs of white space inside and letter case, so a grader that prints a sentence scores as a miss.

The interval resamples the cases in the file and says nothing about cases unlike them.

The grader command sees one case per start, so a slow model makes a slow run. The time limit is 120 seconds per case, and `--timeout SEC` before the `--` changes it, as in `python3 golden.py run answers.csv --timeout 30 -- python3 your_grader.py`.

A correction note is checked for a date and a reason, never for whether the reason is good.

The CSV is read into memory. A blank line in it is refused with its line number, even when it is the last line of the file.

`run` saves the whole CSV after every case, from the copy it read when it started. An edit you make to the file while a run is working is lost, so wait until the run ends.

One dated note covers every later change to that row, and the note's date is never compared with the date of the lock. The score lists each changed row with its locked answer and its answer now, so read that list.

When you change a case's text after the grader ran, the stored label was made for the old text. Clear that row's grader cell with `python3 examples/set_cell.py answers.csv 12 grader ""` and run again.

A save writes the CSV as UTF-8 with plain line feeds and without a byte order mark, and keeps the file's permission bits.

The grader's label is stored as printed, apart from outer spaces. The tool does not escape it for a spreadsheet, and a spreadsheet may read a cell that starts with `=`, `+`, `-` or `@` as a formula. When the grader reads text you do not trust, look at the grader column as plain text first.

It was tested on macOS with Python 3.9 and Python 3.14. It uses process groups and the hang-up signal, which Windows does not have.

## Tests

From this folder:

```
python3 -m unittest discover -s tests -v
python3 tests/mutate.py
sh examples/demo.sh
```

The unit tests cover the agreement math and the three commands. The mutation run copies the code, the examples and the tests into a fresh folder for each broken rule, applies that one change, and runs the unit tests there. A broken rule is killed when the tests fail. The last line ends with `survived: 0` when every broken rule was caught. The demo exits 0 when each step of the tour returns the expected status.

The tests and the mutation run use the Python that started them. The demo, and the one test that runs it, use the `python3` on your path. Set `PYTHON` to pick another, as in `PYTHON=/usr/bin/python3 sh examples/demo.sh`, and the commands the demo prints then show that path.

Some tests start graders that hang on purpose. When such a test ends it kills what is left of them: the process ids those graders wrote down, and any process whose command line holds the path of a script in the test's own temporary folder.

## License

MIT. The terms are in the LICENSE file.

## Built at San Diego AI Studio

This tool comes out of San Diego AI Studio, the builders' community in San Diego, where builders show the systems they run. More projects: https://sandiegoaistudio.com/projects

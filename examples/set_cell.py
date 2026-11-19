#!/usr/bin/env python3
import csv
import os
import sys
import tempfile

def die(message):
    sys.stderr.write(message + "\n")
    return 2

def main(argv):
    if len(argv) != 5:
        return die("usage: python3 examples/set_cell.py FILE ID COLUMN VALUE")
    path, ident, column, value = argv[1:]
    try:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
    except (OSError, UnicodeDecodeError):
        return die("%s: cannot read file" % path)
    if not rows or "id" not in rows[0]:
        return die("%s: missing id column" % path)
    header, body = rows[0], rows[1:]
    id_at = header.index("id")
    hits = [row for row in body if id_at < len(row) and row[id_at].strip() == ident]
    if not hits:
        return die("%s: no row with id %s" % (path, ident))
    if column not in header:
        header.append(column)
    col_at = header.index(column)
    for row in body:
        row += [""] * (col_at + 1 - len(row))
    hits[0][col_at] = value
    destination = path
    if os.path.islink(path):
        link = os.readlink(path)
        if not os.path.isabs(link):
            destination = os.path.join(os.path.dirname(os.path.abspath(path)), link)
        else:
            destination = link
    mode = os.stat(destination).st_mode & 0o777
    folder = os.path.dirname(os.path.abspath(destination)) or "."
    fd, temporary = tempfile.mkstemp(prefix=".set-cell-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            csv.writer(handle, lineterminator="\n").writerows([header] + body)
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None and os.path.exists(temporary): os.unlink(temporary)
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception:
        sys.stderr.write("could not update the file\n")
        sys.exit(2)

"""source: examples/demo.sh runs the whole loop offline and leaves the repository untouched."""

import hashlib
import os
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "examples", "demo.sh")


def snapshot(root):
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            with open(path, "rb") as handle:
                found[rel] = hashlib.sha256(handle.read()).hexdigest()
    return found


class DemoTest(unittest.TestCase):
    def test_demo_passes_and_writes_nothing_in_the_repo(self):
        """source: demo exits 0, shows 27 then the case 12 refusal then 28, and does not touch the repo."""
        before = snapshot(ROOT)
        proc = subprocess.run(
            ["/bin/sh", DEMO],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            timeout=120,
        )
        after = snapshot(ROOT)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("27 of 30, bar 27: PASS", proc.stdout)
        self.assertIn("case 12: answer changed since the lock", proc.stdout)
        self.assertIn('locked "normal"', proc.stdout)
        self.assertIn('now "urgent"', proc.stdout)
        self.assertIn(
            'case 12 (locked "normal", now "urgent"): 2026-01-15 the refund policy changed',
            proc.stdout,
        )
        self.assertIn("28 of 30, bar 27: PASS", proc.stdout)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

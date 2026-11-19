#!/usr/bin/env python3
"""Stand-in grader. Keyword rules only, no model."""

import sys

SPAM = (
    "lottery",
    "unsubscribe",
    "free money",
    "you have won",
    "click here",
    "crypto giveaway",
)
URGENT = (
    "refund",
    "charged twice",
    "cannot log in",
    "account locked",
    "system is down",
    "outage",
)


def label_for(text):
    folded = " ".join(text.split()).casefold()
    for bit in SPAM:
        if bit in folded:
            return "spam"
    for bit in URGENT:
        if bit in folded:
            return "urgent"
    return "normal"


def main():
    sys.stdout.write(label_for(sys.stdin.read()) + "\n")


if __name__ == "__main__":
    main()

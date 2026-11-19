"""Agreement between two labelers.

Pure functions: nothing here reads a file, the clock, or the environment.
The only randomness is random.Random(seed) inside bootstrap.
"""

import random


def norm(text):
    return " ".join(text.split()).casefold()


def matches(pairs):
    count = 0
    for mine, grader in pairs:
        if mine == grader:
            count += 1
    return count


def kappa(pairs):
    """Cohen's kappa. None when one minus expected agreement is under 1e-12."""
    count = len(pairs)
    if count == 0:
        return None
    observed = matches(pairs) / float(count)
    labels = set()
    for mine, grader in pairs:
        labels.add(mine)
        labels.add(grader)
    expected = 0.0
    for label in labels:
        mine_count = 0
        grader_count = 0
        for mine, grader in pairs:
            if mine == label:
                mine_count += 1
            if grader == label:
                grader_count += 1
        expected += (mine_count / float(count)) * (grader_count / float(count))
    if (1.0 - expected) < 1e-12:
        return None
    return (observed - expected) / (1.0 - expected)


def confusion(pairs):
    counts = {}
    for mine, grader in pairs:
        key = (mine, grader)
        counts[key] = counts.get(key, 0) + 1
    return counts


def bootstrap(pairs, fn, n_resamples=2000, seed=0):
    """Draw len(pairs) pairs with replacement, n_resamples times."""
    rng = random.Random(seed)
    values = []
    size = len(pairs)
    for _resample in range(n_resamples):
        drawn = []
        for _draw in range(size):
            drawn.append(pairs[rng.randrange(size)])
        values.append(fn(drawn))
    return values


def interval(values, alpha=0.05):
    """Drop None, sort, and return the low and high percentile values."""
    kept = []
    for value in values:
        if value is not None:
            kept.append(value)
    kept.sort()
    m = len(kept)
    if m == 0:
        return (None, None)
    lo_index = int(m * alpha / 2)
    hi_index = int(m * (1.0 - alpha / 2.0)) - 1
    return (kept[lo_index], kept[hi_index])

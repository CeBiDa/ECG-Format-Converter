# Global counters for the current run
count_success = 0
count_skipped = 0
count_failed = 0


def reset():
    """Reset all counters. Call once at the start of every run."""
    global count_success, count_skipped, count_failed
    count_success = 0
    count_skipped = 0
    count_failed = 0


def snapshot():
    """Return the current counters as a dict."""
    return {
        "success": count_success,
        "skipped": count_skipped,
        "failed": count_failed,
    }

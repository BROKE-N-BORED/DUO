"""Turn statistics: compute them from Ollama's done chunk and
format the stats line and the session-total line.

Split out of dual_chat.py in v0.25 (audit T14). Pure functions,
no imports.
"""


def compute_turn_stats(metrics):
    """Turn metrics into display numbers.

    metrics comes from the streamed done chunk plus client-side
    timing: eval_count, eval_duration_ns, prompt_eval_count,
    ttft_s, total_s. Anything missing (or not a number) stays None
    and shows as "n/a" -- never invented. tokens/sec is eval_count
    over eval_duration in seconds. The counts are the turn's own
    numbers, straight from Ollama -- generated (out) and prompt
    (in) -- never estimated from characters.
    """
    count = metrics.get("eval_count")
    duration = metrics.get("eval_duration_ns")
    tok_per_sec = None
    if (isinstance(count, (int, float))
            and isinstance(duration, (int, float)) and duration > 0):
        tok_per_sec = count / (duration / 1_000_000_000)

    def _num(value):
        return value if isinstance(value, (int, float)) else None

    def _count(value):
        return int(value) if isinstance(value, (int, float)) else None

    return {"tok_per_sec": tok_per_sec,
            "eval_count": _count(metrics.get("eval_count")),
            "prompt_eval_count": _count(metrics.get("prompt_eval_count")),
            "ttft_s": _num(metrics.get("ttft_s")),
            "total_s": _num(metrics.get("total_s"))}


def format_ram(size_bytes):
    """A model size in bytes as "1.9 GB" or "512 MB"."""
    if size_bytes >= 1_000_000_000:
        return "%.1f GB" % (size_bytes / 1_000_000_000)
    return "%d MB" % round(size_bytes / 1_000_000)


STATS_EMPTY_LINE = ("-- tok/s · -- out / -- in · first token -- · "
                    "turn -- · RAM --")


def format_session_line(totals):
    """A side's running session total: "Session: 42 out / 128 in".

    totals is {"out": int, "in": int} -- tokens generated and
    prompt tokens across every generation this side has made since
    the app started.
    """
    return "Session: %d out / %d in" % (totals["out"], totals["in"])


def format_stats_line(stats):
    """One line for a pane's stats label.

    stats is {"tok_per_sec": float|None, "eval_count": int|None,
    "prompt_eval_count": int|None, "ttft_s": float|None,
    "total_s": float|None, "ram": str|None} -- "ram" is preformatted,
    e.g. "1.9 GB in RAM" or "not loaded". None (before any turn) shows
    dashes; a missing number shows "n/a". The token segment sits
    right after the speed: "42 out / 128 in" when Ollama reported
    both counts, "tokens n/a" when it reported neither, and the
    half it did report when it reported only one.
    """
    if stats is None:
        return STATS_EMPTY_LINE
    tok = stats.get("tok_per_sec")
    out = stats.get("eval_count")
    prompt = stats.get("prompt_eval_count")
    ttft = stats.get("ttft_s")
    total = stats.get("total_s")
    tok_text = ("%.1f tok/s" % tok) if tok is not None else "tok/s n/a"
    if out is None and prompt is None:
        tokens_text = "tokens n/a"
    else:
        out_text = ("%d out" % out) if out is not None else "n/a out"
        in_text = ("%d in" % prompt) if prompt is not None else "n/a in"
        tokens_text = "%s / %s" % (out_text, in_text)
    ttft_text = (("first token %.2fs" % ttft) if ttft is not None
                 else "first token n/a")
    total_text = (("turn %.1fs" % total) if total is not None
                  else "turn n/a")
    ram_text = stats.get("ram") or "RAM n/a"
    return "%s · %s · %s · %s · %s" % (
        tok_text, tokens_text, ttft_text, total_text, ram_text)

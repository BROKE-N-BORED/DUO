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


# --- model fit (v0.33, redesign R2) ----------------------------------------
# U39: model selection shows what the model does and whether it fits
# this PC -- never make the user guess at sizes or specs. A model
# fits when its size leaves the reserve (2 GB for the OS, DUO, and
# Ollama itself) untouched. A model that doesn't fit is still
# selectable -- inform, never block. Sizes come from /api/tags;
# total RAM comes from /proc/meminfo, read once and cached by the
# window. Everything here returns None on unknown input instead of
# raising: an unreadable /proc means "size only, no fit label",
# never a crash.

MODEL_FIT_RESERVE_BYTES = 2 * 1_000_000_000


def read_total_ram(meminfo_path="/proc/meminfo"):
    """Total RAM in bytes from /proc/meminfo, or None when it can't
    be read or parsed. Never raises."""
    try:
        with open(meminfo_path, encoding="utf-8") as fh:
            parsed = parse_meminfo(fh.read())
    except (OSError, ValueError):
        return None
    return parsed[0] if parsed is not None else None


def model_fits(size_bytes, total_ram_bytes):
    """True when a model of ``size_bytes`` fits in ``total_ram_bytes``
    with the reserve left over; False when it doesn't; None when the
    total is unknown (no fit judgement possible). The boundary is
    inclusive: exactly total-minus-reserve fits."""
    if total_ram_bytes is None:
        return None
    if not isinstance(size_bytes, (int, float)):
        return None
    return size_bytes <= total_ram_bytes - MODEL_FIT_RESERVE_BYTES


def format_model_entry(name, size_bytes, total_ram_bytes):
    """One model picker's display line for a model.

    "lfm3:1.7b - 1.1 GB - fits this PC" when the size and the total
    are both known; "big-model:13b - 8.0 GB - too big for this PC"
    when it doesn't fit; "name - 1.1 GB" when the total is unknown;
    the bare name when the size is unknown. Plain hyphens only --
    no em-dashes in visible text (U33). The plain name is the
    picker's item data; this text is display only."""
    if not isinstance(size_bytes, (int, float)):
        return name
    text = "%s - %s" % (name, format_ram(size_bytes))
    fits = model_fits(size_bytes, total_ram_bytes)
    if fits is True:
        return "%s - fits this PC" % text
    if fits is False:
        return "%s - too big for this PC" % text
    return text


# --- system meter (v0.29) --------------------------------------------------
# CPU % and RAM for the whole machine, read from /proc with stdlib
# only -- no psutil. CPU % needs two samples (it is the delta between
# them), so the first read only establishes the baseline. Every parse
# returns None on bad input instead of raising: a missing or strange
# /proc means the meter shows nothing, never a crash.

def parse_cpu_times(text):
    """The aggregate "cpu" line of /proc/stat as (idle, total).

    idle is idle + iowait ticks; total is every field summed. A
    missing or malformed line returns None.
    """
    for line in text.splitlines():
        if line.startswith("cpu "):
            fields = line.split()[1:]
            try:
                values = [int(field) for field in fields]
            except ValueError:
                return None
            if len(values) < 5:
                return None
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            return idle, sum(values)
    return None


def cpu_percent_between(prev, cur):
    """CPU % between two parse_cpu_times samples, or None when the
    delta is zero or the samples are missing. Clamped to 0-100: a
    counter reset must never show a wild number."""
    if prev is None or cur is None:
        return None
    idle_delta = cur[0] - prev[0]
    total_delta = cur[1] - prev[1]
    if total_delta <= 0:
        return None
    pct = 100.0 * (1.0 - idle_delta / total_delta)
    return max(0.0, min(100.0, pct))


def parse_meminfo(text):
    """(total_bytes, available_bytes) from /proc/meminfo text, or
    None when either line is missing or malformed. MemAvailable is
    what the machine can still hand out, so used is total minus
    available."""
    total = None
    available = None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            value = int(parts[1])
        except ValueError:
            continue
        if parts[0] == "MemTotal:":
            total = value * 1024
        elif parts[0] == "MemAvailable:":
            available = value * 1024
    if total is None or available is None:
        return None
    return total, available


def format_system_meter(cpu_pct, used_bytes, total_bytes):
    """The meter's one line: "CPU 42% · RAM 3.1 / 7.7 GB".

    A missing CPU % (first read, no delta yet) shows dashes; a
    missing RAM reading shows dashes too. When nothing at all is
    known -- /proc unreadable, a non-Linux machine -- the line is
    empty: the meter shows nothing rather than a row of dashes.
    """
    if (cpu_pct is None and used_bytes is None
            and total_bytes is None):
        return ""
    cpu_text = ("CPU %d%%" % round(cpu_pct)) if cpu_pct is not None \
        else "CPU --"
    if used_bytes is not None and total_bytes is not None:
        if total_bytes >= 1_000_000_000:
            ram_text = "RAM %.1f / %.1f GB" % (
                used_bytes / 1_000_000_000,
                total_bytes / 1_000_000_000)
        else:
            ram_text = "RAM %d / %d MB" % (
                round(used_bytes / 1_000_000),
                round(total_bytes / 1_000_000))
    else:
        ram_text = "RAM --"
    return "%s · %s" % (cpu_text, ram_text)


def read_system_meter(prev_cpu, stat_path="/proc/stat",
                      meminfo_path="/proc/meminfo"):
    """One meter sample: (cpu_pct, used_bytes, total_bytes, new_prev).

    cpu_pct is None on the first read (no delta yet) or when
    /proc/stat can't be read; used/total are None when /proc/meminfo
    can't be read. new_prev is the fresh /proc/stat sample for the
    next call, or the old prev when the read failed. Nothing here
    raises -- an unreadable /proc just means Nones.
    """
    cpu_pct = None
    new_prev = prev_cpu
    try:
        with open(stat_path, encoding="utf-8") as fh:
            current = parse_cpu_times(fh.read())
    except (OSError, ValueError):
        current = None
    if current is not None:
        cpu_pct = cpu_percent_between(prev_cpu, current)
        new_prev = current
    used = None
    total = None
    try:
        with open(meminfo_path, encoding="utf-8") as fh:
            parsed = parse_meminfo(fh.read())
    except (OSError, ValueError):
        parsed = None
    if parsed is not None:
        total, available = parsed
        used = max(0, total - available)
    return cpu_pct, used, total, new_prev


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

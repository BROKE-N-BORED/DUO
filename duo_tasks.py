"""Task chains: the worker/follow-up/rounds session and its
framing templates. Pure logic, no Qt -- the GUI drives it from
the runner callbacks.

Split out of dual_chat.py in v0.25 (audit T14).
"""


# task text; each later round hands the latest result to the other
# side, framed for review or continuation -- and in a review chain
# the worker gets the review back to revise from. "Nothing" means
# no second call: the task is one round.
TASK_REVIEW_TEMPLATE = (
    "Here is the result of a task. Review it and say what is good, "
    "what is wrong, and what you would change:\n\n%s")


TASK_CONTINUE_TEMPLATE = "Continue this work from where it left off:\n\n%s"


TASK_REVISE_TEMPLATE = (
    "Here is a review of your work. Revise your work based on it:\n\n%s")


TASK_FOLLOW_UPS = ("review", "continue", "nothing")


TASK_ROUNDS_MIN = 1


TASK_ROUNDS_MAX = 10


TASK_ROUNDS_DEFAULT = 1


class TaskSession:
    """One task run: the worker side, the follow-up, and the rounds.

    Pure logic, no Qt -- the GUI drives it from the runner callbacks.
    A task is a chain of rounds: round 1 is the worker answering the
    task as typed, and each later round hands the latest result to
    the other side, alternating. A "continue" chain keeps continuing;
    a "review" chain alternates review turns (the other side) with
    revise turns (the worker revising from the review). "Nothing"
    is always one round. With a follow-up the chain is at least two
    rounds (worker + one follow-up), so Rounds 1 behaves exactly as
    v0.21 did: the worker, then one follow-up round.
    """

    def __init__(self, worker, follow_up, rounds=TASK_ROUNDS_DEFAULT):
        self.worker = worker  # "A" or "B"
        self.follow_up = follow_up  # "review", "continue", "nothing"
        self.rounds = max(TASK_ROUNDS_MIN,
                          min(TASK_ROUNDS_MAX, int(rounds)))
        if self.follow_up == "nothing":
            # No follow-up, no chain: one round whatever was asked.
            self.rounds = 1
        elif self.rounds < 2:
            # A follow-up always adds at least one round after the
            # worker, so Rounds 1 with Review/Continue is exactly the
            # v0.21 task: worker, then one follow-up round.
            self.rounds = 2
        self.rounds_done = 0  # turns completed so far
        self.phase = "worker"  # "worker" -> "followup" -> done
        self.active = True

    @property
    def other(self):
        return "B" if self.worker == "A" else "A"

    @property
    def next_side(self):
        """The side that takes the next round (round rounds_done + 1):
        the worker takes odd rounds, the other side even rounds."""
        return self.worker if (self.rounds_done + 1) % 2 == 1 else self.other

    def frame(self, result):
        """The message the other model receives for the worker's reply,
        or None when the follow-up is "nothing" (no second call)."""
        if self.follow_up == "review":
            return TASK_REVIEW_TEMPLATE % result
        if self.follow_up == "continue":
            return TASK_CONTINUE_TEMPLATE % result
        return None

    def frame_next(self, result):
        """The message the next side receives for the latest result,
        or None when the follow-up is "nothing" (no further rounds).
        A review chain frames by whose turn it is: the other side
        reviews, the worker revises from the review."""
        if self.follow_up == "continue":
            return TASK_CONTINUE_TEMPLATE % result
        if self.follow_up == "review":
            if self.next_side == self.other:
                return TASK_REVIEW_TEMPLATE % result
            return TASK_REVISE_TEMPLATE % result
        return None

    def handoff_note(self):
        """The short note shown in the window for the handoff entry --
        the transcript stays readable instead of repeating the full
        framed result."""
        if self.follow_up == "review":
            return "Task result from Model %s, for review." % self.worker
        if self.follow_up == "continue":
            return "Task result from Model %s, to continue." % self.worker
        return "Task result from Model %s." % self.worker

    def handoff_note_next(self, from_side):
        """The short note for the next handoff entry: the result (or
        the review) passing from ``from_side`` to the next side."""
        if self.follow_up == "continue":
            return "Task result from Model %s, to continue." % from_side
        if self.follow_up == "review":
            if self.next_side == self.other:
                return "Task result from Model %s, for review." % from_side
            return "Review from Model %s, to revise." % from_side
        return "Task result from Model %s." % from_side

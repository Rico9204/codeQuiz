LEVELS = ["기초", "보통", "심화"]


def next_difficulty(current: str, initial: str, score: int, enabled: bool = True) -> str:
    """Return the next base-question level while keeping a one-step lower bound."""
    if not enabled:
        return current

    current_index = LEVELS.index(current)
    initial_index = LEVELS.index(initial)
    minimum = max(0, initial_index - 1)
    adjustment = 1 if score >= 3 else -1 if score <= 1 else 0
    return LEVELS[max(minimum, min(len(LEVELS) - 1, current_index + adjustment))]

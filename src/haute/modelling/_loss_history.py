"""Shared bounded loss-history projection for job and workspace responses."""

from collections.abc import Iterable

from haute._env import int_env


def loss_history_limit() -> int:
    return int_env("HAUTE_TRAIN_LOSS_HISTORY_LIMIT", 200)


def bounded_loss_history(
    history: Iterable[dict[str, float]],
    *,
    best_iteration: int | None,
) -> tuple[list[dict[str, float]], bool]:
    """Thin a fit's loss history to the limit, keeping its whole span.

    Keeps the first and last rows, the best iteration's row (rows count from
    one, ``best_iteration`` from zero) and an even stride between them.
    """
    rows = list(history)
    limit = loss_history_limit()
    if len(rows) <= limit:
        return rows, False
    kept = {0, len(rows) - 1}
    if best_iteration is not None:
        kept.update(
            index for index, row in enumerate(rows) if row["iteration"] == best_iteration + 1
        )
    spare = limit - len(kept)
    if spare > 0:
        step = (len(rows) - 1) / (spare + 1)
        kept.update(round(step * slot) for slot in range(1, spare + 1))
    return [rows[index] for index in sorted(kept)][:limit], True

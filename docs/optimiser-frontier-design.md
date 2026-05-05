# Optimiser Frontier Design

## Problem

The optimiser needs to expose efficient frontiers for both online and ratebook solves without changing the core solve contract or keeping heavyweight solver data around after a user has applied a result. Frontier requests also need predictable failure modes: invalid user configuration should return a client error, backend execution failures should be visible, and long-running jobs should always leave the shared job store in an observable terminal state.

## Approach

The backend keeps the individual solve as the canonical first step, then computes frontier points from explicit absolute threshold ranges. Online solves pass only the kwargs supported by `price-contour`; ratebook solves keep the ratebook-specific solver inputs scoped to the ratebook path. Auto-range estimation is treated as a short-lived internal job and is removed from the shared store once it finishes because there is no polling API for it.

The frontend stores accepted partial frontier payloads but auto-selects the first point only when that point contains enough numeric summary fields to derive a complete `SolveResult`. This lets valid lightweight frontier payloads render without crashing while still failing loudly if a selected point is malformed.

Successful online selected-point apply clears all solver runtime data for that job. Ratebook frontier sessions preserve their runtime data while the user is switching candidate points, because the backend needs that state to materialise later selections.

## Alternatives Considered

- Treat `frontier_min` and `frontier_max` as multipliers. This was rejected because the current API and `price-contour` integration operate on absolute threshold values, and multiplier semantics are ambiguous when constraints have different scales.
- Always return an auto-range job id. This was rejected because there is no public polling path for auto-range jobs; keeping the job private and cleaning it up immediately avoids unobservable store growth.
- Add frontend fallbacks for missing frontier totals. This was rejected because a selected frontier point without complete totals cannot produce a faithful solve summary.

## Open Questions

- Whether ratebook frontier point switching should move to a dedicated resumable session API instead of reusing the solve job store.
- Whether future `price-contour` releases should expose a uniform frontier kwargs contract across online and ratebook solvers.

# Optimiser roadmap

## Scope

The Optimiser node's solve, as [the optimiser
specification](../optimiser/high-level.md) describes it: what a solve says
about scored rows it cannot use, whether a solve meets a bound that can be
met, and what a finished solve keeps in memory. The packages below come
from the review plan's sweep of 11 November 2026 that computed answers a
second way: 83 online solves and 23 ratebook solves of a few quotes each,
their totals recomputed from the rows and the online ones checked against
every assignment enumerated. The main session ran each finding again
through the routes, the solver ones on books of 3,000 and 5,000 quotes with
the node's default settings.

## Priorities

**Start with:** `OPM-02`

| Package | State | Priority | Outcome |
|---|---|---:|---|
| OPM-02 | Planned | P2 | A solve with one binding constraint converges and meets its bound, as point 0 of its own frontier does. |
| OPM-03 | Decision | P2 | A ratebook solve that ends short of a bound says so in its status and warning, not only in the Summary table. |
| OPM-04 | Planned | P2 | Solving a node again ends the solver process of its earlier solve, so tuning a constraint does not hold one process per press for fifteen minutes. |

## Planned improvements

### OPM-02 — A one-constraint solve finds what its frontier's first point finds
**Why:** Reproduced on 11 November 2026 through the routes, on 5,000 quotes
of 9 steps each with one constraint: volume at least 3,113.05, half way
between what each quote's best step for income gives and the most the grid
allows. With the node's defaults, 50 iterations and a tolerance of 1e-6,
the solve ends without converging, at a volume of 2,930.4, 5.9% under its
bound, with the warning "Solver did not converge. Consider increasing
max_iter or relaxing tolerance." The advice does not get there:

| Max iterations | Tolerance | Converged | Volume against the bound |
|---:|---:|---|---:|
| 50 | 1e-6 | no | -5.87% |
| 200 | 1e-6 | no | -4.85% |
| 1,000 | 1e-6 | no | -3.26% |
| 1,000 | 1e-4 | no | -3.26% |
| 5,000 | 1e-3 | no | -1.00% |

The same solve, asked for a three-point frontier whose range starts at that
bound, holds the answer. Point 0 is the same problem, and it converged at a
volume of 3,113.06 with an objective of 1,691,754. The as-solved result
beside it misses the bound, and it is the one the Export pane publishes
unless a point is picked.

The sweep met this on small problems and counted it. Of 67 solves whose
constraint binds, 65 did not converge at any setting tried, and 58 of those
broke their bound. Where a frontier was asked for, point 0 differed from
the as-solved result in 12 of 14. On the instance kept as the example,
point 0 found the best objective that enumerating every assignment finds,
718.8664.

The two results come from two methods. The solve runs the library's
subgradient search over the multipliers. A frontier point over one swept
constraint is found by bisection, which the results panel's Convergence
view shows as "each λ probed within its bracket". The analyst who asks for
no frontier gets the first.

**Plan:** Solve a problem with one binding constraint by the method the
frontier uses for it, in Haute's call or in the library, so that the solve
and point 0 are one answer. Keep the subgradient search for problems with
more than one constraint, and make its warning true to what helps: on these
runs more iterations did not.

**Acceptance:** On a book of 5,000 quotes with one binding constraint and
the node's defaults, the solve converges and meets its bound, and its
totals equal those of point 0 of a frontier whose range starts at that
bound. A test holds the two equal.

**Dependencies:** None in Haute. The method is price-contour's.

**Evidence:** `src/haute/routes/_optimiser_solver.py::_solve_online`;
`specs/optimiser/low-level.md`; `docs/building-models/nodes/optimiser.md`.

### OPM-03 — A ratebook solve that stops short of a bound says so
**Why:** Reproduced on 11 November 2026 through the routes, on 3,000 quotes
of 9 steps each with the factors region (four levels) and age (five), and
one constraint: volume at least 1,868.27. The lowest step for every quote
gives 1,997.4, so the bound can be met. With the node's defaults the solve
ends converged after 4 passes, with no warning and a volume of 1,835.1,
1.8% under its bound. The multiplier rises at every pass, from 47.7 to
177.6, while the tables stop moving, and the passes end there. The sweep
saw the same in 14 of its 23 small ratebook solves, short by 1% to 41%.

The specification knows the case: feasibility is "judged by haute in both
modes because a converged ratebook point can still breach a bound", and the
Summary pane's table says "Breached" for the constraint. What the analyst
gets first is a solve marked converged, with no warning and no advice,
where an online solve that misses its bound carries both.

**Plan:** Decide what a ratebook solve owes a bound it could meet. At the
least, a solve that ends with a bound breached says so where it says
converged, naming the constraint and the shortfall as the Summary does.
Whether the passes should go on while the multiplier is still moving is the
library's question.

**Acceptance:** A ratebook solve that ends 1.8% under a minimum it could
meet shows the breach in its status and its warning, not only in the
Summary table.

**Dependencies:** None in Haute. The stopping rule is price-contour's.

**Evidence:** `src/haute/routes/_optimiser_solver.py::_solve_ratebook`;
`specs/frontend-modelling-optimiser-ui/high-level.md`;
`docs/building-models/nodes/optimiser.md`.

### OPM-04 — A node solved again lets go of its earlier solve
**Why:** Reproduced on 11 November 2026 through the routes: one server, a
book of 5,000 quotes, and the same Optimiser node solved five times with a
different bound each time, as an analyst tuning a constraint does.

| | Processes under the server | Memory committed |
|---|---:|---:|
| Before any solve | 1 | 1,749 MB |
| After solve 1 | 2 | 2,628 MB |
| After solve 2 | 3 | 3,499 MB |
| After solve 3 | 4 | 4,385 MB |
| After solve 4 | 5 | 5,255 MB |
| After solve 5 | 6 | 6,139 MB |

Each solve leaves one more process, holding about 880 MB of committed
memory, the figure Windows counts against its limit; about 150 MB of it is
resident. They are solver sessions. The specification gives each solve "one
dedicated spawn worker per solve that holds the solver and quote grid for
as long as the result keeps them", and the result keeps them for fifteen
minutes (read, not waited for). The first solve's Quotes page was still
served after the fifth.

The editor shows one solve for a node. Once the node is solved again,
nothing on the screen can reach the earlier one, and its process waits out
the fifteen minutes all the same. Ten presses of Optimise in a quarter of
an hour hold nearly 9 GB for results that cannot be opened. Another run was
stopped by its 8,000 MB limit during its seventh solve, and the sweep's
eleven solves of a few quotes each reached 6,010 MB.

**Plan:** When a solve of a node completes, end the sessions of that node's
earlier solves in the same project and slim those jobs to their summaries,
as the retention window would later. Put a small ceiling on the sessions
alive at once, oldest first, for solves of different nodes.

**Acceptance:** After five solves of one node, one solver session process
is alive. The earlier jobs still answer their summaries, and a request for
their Quotes pages answers as it does after the retention window.

**Dependencies:** None.

**Evidence:** `src/haute/routes/_optimiser_session.py::SolverSession`;
`src/haute/routes/_job_store.py`; `specs/optimiser/high-level.md`.

# Execution engine roadmap

## Scope

Running a graph, as [the execution engine
specification](../execution-engine/high-level.md) describes it: which source a
run reads, how a node's settings become the frame it produces, and how a
failure before any node runs is reported. The packages below come from the
9 November 2026 codebase review. `EXE-07`
comes from the review plan's phase 5, a reader from a second model
family (Codex) over the code changed since the 31 October audit; a worker
built each run and the main session ran it again on 13 November.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EXE-07 | Planned | P2 | A preview worker that died while idle is replaced whatever the size of the next request; previews and the server's shutdown no longer hang until a restart. |

## Planned improvements

### EXE-07 — A preview worker that died is replaced, whatever the next request's size
**Why:** Reproduced on 13 November 2026 with the real worker pool and
through the editor's preview route. The editor keeps a worker process for
previews. That process can die while it is idle: ended in Task Manager, or
killed by the system when memory is short. The next request finds it dead.
With a request of 4,000 bytes the pool reports a crashed worker in 3.5
seconds, starts another, and answers the request after that. With a request
of 16,000 bytes or more the call never returns. The pool closes the dead
worker's request queue and waits for the queue's feeder thread, which is
still writing the request into a pipe that nothing reads. The next request
times out waiting for the slot, and the pool's own close, which the server
calls when it shuts down, does not return either.

Through the editor, the shipped rating example's graph is 2,224 bytes as
sent to the worker. With the worker killed, the next preview answers 500
with an incident ("Haute hit a failure it does not recognise"), and the one
after it works. With the example's rating table grown to 5,000 rows the graph is
151,156 bytes: after the worker is killed the preview has no answer after
60 seconds, and the next preview of the shipped graph has none after 30.
Previews stay dead until the server is restarted. The execution
specification has a request never wait "unbounded".

**Plan:** When a worker is known to be dead, do not wait for its queue's
feeder: abandon what was not yet sent, and bound the wait that remains.
Answer the first request after a worker's death by saying that the worker
stopped and was started again.

**Acceptance:** With the idle worker killed, a preview whose graph is 151
kB as sent is answered, with rows or with a named failure, within the
request's own time limit; the preview after it has rows; and the server
shuts down. The first preview after a worker's death says that the worker
stopped and was started again, not that the failure is unrecognised.

**Dependencies:** None.

**Evidence:** `src/haute/_interactive_workers.py`;
`specs/execution-engine/low-level.md`.

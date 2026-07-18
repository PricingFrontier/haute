// Faithful simulation of useTracing.handleCellClick state logic (useTracing.ts:207-240).
// Models the two INDEPENDENT useState cells and the unconditional .then setter.
// Proves: when click A then click B, and A resolves AFTER B, the final
// (traceResult, tracedCell) pair is INCONSISTENT and traceResult holds the STALE A trace.

let traceResult = null;        // setTraceResult target (drives decorations + panel)
let tracedCell  = null;        // setTracedCell target (drives highlighted cell)

const setTraceResult = (v) => { traceResult = v; };
const setTracedCell  = (v) => { tracedCell  = v; };

// Deferred promise so we control resolution ORDER independently of call ORDER.
function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

// Mirrors lines 210-228 exactly: sync setTracedCell, then traceCell().then(setTraceResult)
// with NO signal and NO "is this the latest request" guard.
function handleCellClick(rowIndex, column, traceCellPromise) {
  setTracedCell({ rowIndex, column });
  return traceCellPromise.then((data) => {
    if (data.status === "ok" && data.trace) {
      setTraceResult(data.trace);          // <-- UNCONDITIONAL, no sequence check
    }
  });
}

const A = deferred();
const B = deferred();

// User clicks cell A (row 0, col "price"), then cell B (row 5, col "premium")
// BEFORE A resolves. Both requests are now in flight.
const pA = handleCellClick(0, "price",   A.promise);
const pB = handleCellClick(5, "premium", B.promise);

// At this point tracedCell === B (last sync set wins). Network returns out of order:
// B resolves FIRST (fresh), then A resolves LAST (stale) — the classic race.
B.resolve({ status: "ok", trace: { id: "trace-B", row_index: 5, column: "premium" } });
A.resolve({ status: "ok", trace: { id: "trace-A", row_index: 0, column: "price"   } });

await Promise.all([pA, pB]);

const highlightedCell = tracedCell;                 // DataPreview isTraced source
const decorationsTrace = traceResult;               // nodesWithStatus/edgesWithTrace/panel source

console.log("highlightedCell (tracedCell):", JSON.stringify(highlightedCell));
console.log("decorationsTrace (traceResult.id):", decorationsTrace.id, "row", decorationsTrace.row_index);

const consistent =
  decorationsTrace.row_index === highlightedCell.rowIndex &&
  decorationsTrace.column    === highlightedCell.column;

console.log("CONSISTENT:", consistent);
console.log("traceResult is STALE A (clobbered fresh B):", decorationsTrace.id === "trace-A");

// Assert the SPECIFIC wrong behaviour, not merely "something happened".
if (consistent) {
  console.log("ASSERT-FAIL: expected desync but state was consistent");
  process.exit(2);
}
if (decorationsTrace.id !== "trace-A") {
  console.log("ASSERT-FAIL: expected stale trace-A to win, got " + decorationsTrace.id);
  process.exit(3);
}
if (highlightedCell.column !== "premium" || highlightedCell.rowIndex !== 5) {
  console.log("ASSERT-FAIL: expected highlighted cell to be B(5,premium)");
  process.exit(4);
}
console.log("PROVEN: highlighted cell = B(row5,premium) but decorations/panel show stale trace-A(row0,price); fresh B trace silently overwritten.");

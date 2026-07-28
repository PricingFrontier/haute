Data inputs:
-Provider should be a coloured radio button (Ie, look at 'Load File' node and see theres different buttons for different file types, like this)
-Do we need read mode for parquet? Always scan?

-The Cache UI has changed, it should look the same as the cache button in Quote Input node.

Quote input:
-in the main UI, in the quote input node, emitted tables should have same font as the node name in other nodes.
-The first emitted table is called root, could this be 'quote_info' instead

Edge Joins:
-Should auto populate with a field to join on. Maybe the first common column from both datasets?
-In main UI when refreshing a node, the nodes get little green circles, for edge join this is outside the node, could it be inside instead?

General
-Bug when selecing all/None in the columns pane. It asks to refresh preview, happens across different nodes.
-polars additional code isnt consistent across nodes. I think this should be in a pane called Polars. And it has the code box (shared utility across nodes please) But each node type has the respective info on input.output names (ie Load File has a note to say obj is the input)
-Can we get some path selector consistency, looks good in quote inputs
-MLFlow should be installed by default with haute

---

Decisions (28 Jul):

Quote input:
-First emitted table default label = "quote_info" (was "root"). Change the default in _json_shred.py (~L1965) + shred tests. Note: the label is the frame handle id and the generated-code argument name; existing pipelines keep their saved labels.

Columns pane All/None:
-All = tick every box, None = untick every box. "All unticked" becomes editor-local draft state (config [] already means "all"; zero columns can never run) — show "select at least one column" and only commit once >=1 is ticked. While in there, fix the spurious "Stale columns / refresh" banner: freshly picked columns look like they're validated against the wrong schema.

Data input caching:
-Auto-cache: every Data Input is snapshot-only. Any execution that needs an input with no snapshot kicks the existing build job automatically (visible progress, cancellable), then runs. Keep the executor invariant (execution never contacts the provider) — orchestrate the build above it.
-A stale cache never auto-refreshes: keep serving the existing snapshot until the user acts.
-UI: replace InputCacheControls with the shared CacheFetchButton (same component Quote Input uses) wired to the input-cache endpoints — one button for build/refresh with progress/cancel, stats line with clear.
-Persists between sessions until refresh/remove (already true via on-disk generations). Snapshots must never be silently quota-evicted; quota pressure rejects the incoming build.

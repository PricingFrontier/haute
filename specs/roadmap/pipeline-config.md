# Pipeline config roadmap

## Scope

Node settings and their recovery, as [the pipeline config
specification](../pipeline-config/high-level.md) describes them: how an issue
found in a settings file says where it is. The package below comes from the
9 November 2026 codebase review. It is read from the source; what the editor
does with the two pointer forms was not traced.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| PCF-01 | Planned | P3 | A recovery issue points at a nested setting the same way whichever check found it. |

## Planned improvements

### PCF-01 — One pointer form for a nested setting
**Why:** Read from the source. Recovery reports each issue with a path into
the node's settings. An issue from the completeness checks goes through
`gap_pointer`, which splits a dotted setting into segments:
`/input_scenario_map/quotes`. An issue the engine's own check raised is built
with the whole dotted setting as one segment: `/input_scenario_map.quotes`.
The same function decides whether the issue is a missing value or an invalid
one by looking the dotted setting up as a single key, which is never present
for a nested setting, so a nested issue is always reported as missing. The
only nested setting an engine check names today is a Source Switch input
mapped to an empty source, so the reach is one message in one editor.

**Plan:** Build the engine issue's path with `gap_pointer` and walk the
segments to decide its code.

**Acceptance:** A Source Switch input mapped to an empty source is reported
at `/input_scenario_map/<input>` with the invalid-value code, and a test
holds the two routes to the same path for the same setting.

**Dependencies:** None.

**Evidence:** `src/haute/_node_config_recovery.py::_engine_issues`;
`src/haute/_node_config_recovery.py::gap_pointer`;
`src/haute/_node_config_models.py::_check_live_switch` raises with the
dotted setting.

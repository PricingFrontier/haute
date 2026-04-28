# Config Sidecar Files

## Problem

Pipeline decorators accumulate large config payloads (banding rules, rating
tables, optimiser settings) that make `main.py` unreadable. A single banding
node can produce 50+ lines of inline kwargs. This also makes diffs noisy and
manual config editing painful.

## Approach

Node config is externalised into JSON sidecar files under a `config/`
directory, organised by node type:

```
config/
  banding/              # banding nodes
  rating_step/          # rating step nodes
  data_source/          # data source nodes
  model_scoring/        # model scoring nodes
  optimisation/         # optimisation nodes
  apply_optimisation/   # apply optimisation nodes
  expander/             # scenario expander nodes
  quote_response/       # quote response (output) nodes
  data_sink/            # data sink nodes
  load_file/            # load file (external) nodes
  model_training/       # model training nodes
  constant/             # constant nodes
  quote_input/          # quote input (API) nodes
  source_switch/        # source switch (live) nodes
```

Each node gets one file: `config/<type_folder>/<node_name>.json`.

The decorator references the file instead of carrying inline config:

```python
@pipeline.banding(config="config/banding/optimiser_banding.json")
def optimiser_banding(data_source):
    ...
```

### What stays in Python

- **User code** (`code` key) remains in the `.py` function body. The JSON
  file never contains executable code.
- **Transform nodes** have no config file — they are code-only.
- **Submodel and submodel port** nodes have no config file.

### Folder-as-type convention

The subfolder name determines the node type at parse time. This is a
deliberate design choice: node types are immutable once created (changing
type means deleting and recreating), so the folder→type mapping is stable.

The canonical mapping lives in `_config_io.NODE_TYPE_TO_FOLDER`.

### Parser Contract

For node types with JSON sidecar config, the decorator must include a
`config=` reference. If the reference is missing, points outside the project,
or contains invalid JSON, parsing raises a structured config error.

### Banding Rule Shape

Banding keeps the editor and runtime config in the explicit row-array shape,
but writes concise JSON for sidecars where the rule has a natural key/value
form.

Categorical banding sidecars use the source category as the key and the
assigned band as the value:

```json
{
  "factors": [{
    "banding": "categorical",
    "column": "fuel_type",
    "outputColumn": "fuel_band",
    "rules": {
      "Petrol": "Standard",
      "Diesel": "Standard",
      "Electric": "Green"
    },
    "default": "Other"
  }]
}
```

Breakpoint banding sidecars use the boundary as the key and the assigned band
as the value. The empty-string key represents the open-ended final band:

```json
{
  "factors": [{
    "banding": "breakpoints",
    "column": "driver_age",
    "outputColumn": "age_band",
    "rules": {
      "25": "young",
      "65": "adult",
      "": "senior"
    }
  }]
}
```

Continuous rules remain as explicit rule objects because ranges need operators
and one or two threshold values.

### Rating Step Entry Shape

Rating steps also keep the editor and runtime config in the explicit row-array
shape, but sidecars write lookup entries as nested maps. One- and two-factor
tables use the table's `factors` order. Three-factor tables use the editor's
axis order: the third factor is the outer slice/dropdown, the second factor is
the column group, and the first factor is the row key. The leaf value is the
value assigned by the rating table.

One-factor table:

```json
{
  "tables": [{
    "name": "area_factor",
    "factors": ["area"],
    "outputColumn": "area_factor",
    "defaultValue": "1.0",
    "entries": {
      "London": 1.25,
      "Rural": 0.85
    }
  }]
}
```

Two-factor table:

```json
{
  "tables": [{
    "name": "vehicle_factor",
    "factors": ["vehicle_age_band", "cover_type"],
    "outputColumn": "vehicle_factor",
    "entries": {
      "1-3": {
        "comprehensive": 0.9,
        "tpft": 1.1
      },
      "10+": {
        "comprehensive": 1.4
      }
    }
  }]
}
```

Three-factor table:

```json
{
  "tables": [{
    "name": "vehicle_factor",
    "factors": ["vehicle_age_band", "cover_type", "channel"],
    "outputColumn": "vehicle_factor",
    "entries": {
      "confused": {
        "comprehensive": {
          "1-3": 0.91,
          "4-5": 0.96
        },
        "third_party_only": {
          "1-3": 1.08
        }
      }
    }
  }]
}
```

When loaded, these sidecars expand back to canonical rows like
`{"vehicle_age_band": "1-3", "cover_type": "comprehensive", "value": 0.9}`.
Duplicate factor combinations and malformed nesting raise errors instead of
being guessed.

## Key modules

| Module | Role |
|---|---|
| `_config_io.py` | Path conventions, read/write, `collect_node_configs()` |
| `_banding_config.py` | Banding row-array/map conversion for sidecars |
| `_rating_step_config.py` | Rating-step row-array/nested-map conversion for sidecars |
| `_parser_helpers._resolve_node_config()` | Shared config resolution for parser + submodel parser |
| `codegen._node_to_code()` | Post-processes decorator to `config=` reference |
| `routes/pipeline.py` | Writes config JSON files on save |
| `server.py` | Watches `config/` directory for live sync |

## Alternatives considered

1. **Flat config folder** (all JSON files in one `config/` directory) —
   rejected because it doesn't scale and provides no type information from
   the path alone.
2. **Extend `.haute.json`** — rejected because `.haute.json` is layout
   metadata, not business logic. Mixing concerns would complicate both
   parsing and the GUI.
3. **Python config files** — rejected because JSON is more accessible to
   non-engineers and is the format the GUI already uses internally.

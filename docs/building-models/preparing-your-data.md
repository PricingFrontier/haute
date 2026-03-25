# Preparing Your Data

When you load JSON data into Haute, it arrives with **dot-notation column names** like `proposer.date_of_birth`, `vehicle.make`, and `additional_drivers.1.gender`. Before you can build a model, you need to turn these into clean, simple column names.

This page shows you how to use `haute.clean_columns()` to do that in one line.

---

## What you're starting with

After connecting a **Quote Input** node (or any JSON data source), your data preview will show columns like these:

```
proposer.date_of_birth          proposer.gender
proposer.licence.licence_type   proposer.licence.licence_date
vehicle.make                    vehicle.model
vehicle.security.alarm          vehicle.security.immobiliser
additional_drivers.1.gender     additional_drivers.2.gender
add_ons.breakdown_cover.selected
address.postcode                address.city
policy_details.cover_type       policy_details.voluntary_excess
policy_details.cover_start_date     policy_details.compulsory_excess
```

These dot-notation names come directly from the nested JSON structure. They're accurate, but they're awkward to work with.

---

## Using `clean_columns()`

Add a **Polars** node after your data source. In the code editor, write:

```python
df = haute.clean_columns(quotes)
return df
```

That's it. Click **Run** and look at the data preview -- your columns are now clean.

`clean_columns()` detects patterns from the column names and does three things:

| What it does | Example |
|---|---|
| **Renames** every dot to an underscore | `proposer.gender` becomes `proposer_gender` |
| **Counts array items** | Adds `number_of_additional_drivers` and `has_additional_driver` |
| **Counts boolean groups** | Adds `number_of_add_ons` from the shared boolean field across siblings |

No schema file is needed -- everything is detected from column names.

After `clean_columns()`, everything else -- dropping columns, selecting columns, deriving new features -- is normal Polars code. Use the node's **Columns** tab to select which columns to keep and strip prefixes, and `with_columns` to add derived features.

---

## How the naming works

The rename is fully mechanical: every `.` becomes `_`. No heuristics, no surprises.

| Original column | After `clean_columns()` |
|---|---|
| `address.postcode` | `address_postcode` |
| `vehicle.make` | `vehicle_make` |
| `proposer.licence.licence_type` | `proposer_licence_licence_type` |
| `vehicle.security.alarm` | `vehicle_security_alarm` |
| `additional_drivers.1.gender` | `additional_drivers_1_gender` |
| `add_ons.breakdown_cover.selected` | `add_ons_breakdown_cover_selected` |

The names are longer than you might want -- that's intentional. They preserve the full path from the JSON structure, so you always know exactly which field a column came from. Use the **Columns** tab to strip prefixes visually, or override specific names with the `rename` parameter.

---

## Overriding names

If you want shorter or cleaner names for specific columns, pass `rename`:

```python
df = haute.clean_columns(quotes, rename={
    "address_postcode": "postcode",
    "policy_details_cover_type": "cover_type",
    "vehicle_security_alarm": "security_alarm",
    "proposer_licence_licence_type": "proposer_licence_type",
})
```

You can also use the original dot-notation name as the key:

```python
df = haute.clean_columns(quotes, rename={"address.postcode": "postcode"})
```

Or chain `.rename()` after `clean_columns()` -- it's just normal Polars:

```python
df = haute.clean_columns(quotes)
df = df.rename({"address_postcode": "postcode", "vehicle_make": "make"})
```

---

## Count columns

`clean_columns()` automatically adds counting columns for two patterns it detects from column names:

### Array counts

For every array pattern detected (columns with numeric segments like `.1.`, `.2.`), `clean_columns()` adds:

- `number_of_{array_name}` -- how many non-null items exist in each row
- `has_{singular}` -- boolean, true if at least one item exists

These work at any nesting depth:

| Column pattern | Count columns |
|---|---|
| `additional_drivers.1.*`, `additional_drivers.2.*` | `number_of_additional_drivers`, `has_additional_driver` |
| `proposer.claims.1.*`, `proposer.claims.2.*` | `number_of_proposer_claims`, `has_proposer_claim` |

### Boolean group counts

When columns follow a pattern like `section.child_a.selected`, `section.child_b.selected` -- where the same boolean field name (`selected`, `active`, `included`, `enabled`) appears across multiple siblings -- `clean_columns()` adds a count of how many are true:

```json
"add_ons": {
  "breakdown_cover": {"selected": true, "level": "gold"},
  "legal_expenses": {"selected": false, "level": "basic"}
}
```

Produces `number_of_add_ons` (value: 1 in this example).

---

## Adding derived features

After `clean_columns()`, add derived columns with normal Polars code. Your project's `utility/features.py` has helpers for common operations:

```python
from utility.features import to_date, years_between, postcode_area

df = haute.clean_columns(quotes)

cover_start = to_date("policy_details_cover_start_date")

df = df.with_columns(
    years_between(to_date("proposer_date_of_birth"), cover_start).alias("proposer_age"),
    years_between(to_date("proposer_licence_licence_date"), cover_start).alias("licence_years"),
    (pl.col("policy_details_voluntary_excess") + pl.col("policy_details_compulsory_excess")).alias("total_excess"),
    postcode_area("address_postcode").alias("postcode_area"),
)

return df
```

!!! tip "Check your utility helpers"
    `haute init` generates `utility/features.py` with helpers for common tasks: `to_date`, `years_between`, `months_between`, `days_between`, `postcode_area`, and `cols_matching`. Open the file to see what's available -- they're short, readable functions you can modify or extend.

!!! tip "Use the column sidebar"
    The code editor has an **Available Columns** panel below it. Click the **+** button next to any column name to insert it at your cursor. If you're typing inside quotes (`"..."`), the editor will also suggest column names as you type.

---

## Large arrays

Some schemas have large arrays -- for example, a commercial fleet with up to 50 vehicles. By default, `clean_columns()` only keeps per-slot columns for arrays with 10 or fewer items. Larger arrays get **count columns only** (`number_of_vehicles`, `has_vehicle`) without creating 50 sets of per-vehicle columns.

You can change this threshold:

```python
df = haute.clean_columns(quotes, max_array_expand=20)
```

---

## When things go wrong

### Column name not found

If you see `ColumnNotFoundError`, it usually means a typo. The code editor suggests column names as you type inside quotes -- use these suggestions. You can also check the **Available Columns** sidebar.

### Two columns map to the same name

`clean_columns()` checks for collisions automatically. If two source columns would end up with the same name, it raises a clear error. Fix it by adding a `rename` override for one of them.

### Names are too long

The mechanical rename preserves the full dot-path as underscores. Use the **Columns** tab to strip prefixes visually, or add specific overrides:

```python
df = haute.clean_columns(quotes, rename={
    "proposer_licence_licence_type": "proposer_licence_type",
})
```

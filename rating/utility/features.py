"""Feature engineering helpers for insurance quote processing.

Provides utilities for:
- Date parsing and age calculation from nested JSON fields
- Additional driver feature extraction (ages, licence years, categorical fields)
- Add-on feature extraction (selected flags, counts)
- Column rename mappings (dot-notation → clean snake_case)
"""

from __future__ import annotations

from collections.abc import Callable

import polars as pl

# ── Date helpers ──────────────────────────────────────────────────────


def to_date(col_name: str, fmt: str = "%Y-%m-%d") -> pl.Expr:
    """Parse a string column to a date."""
    return pl.col(col_name).str.to_date(fmt)


def years_between(earlier: pl.Expr, later: pl.Expr) -> pl.Expr:
    """Whole years between two date expressions (floor)."""
    return ((later - earlier).dt.total_days() / 365.25).floor().cast(pl.Int64)


def months_between(earlier: pl.Expr, later: pl.Expr) -> pl.Expr:
    """Calendar months between two date expressions."""
    return (later.dt.year() - earlier.dt.year()) * 12 + (later.dt.month() - earlier.dt.month())


def days_between(earlier: pl.Expr, later: pl.Expr) -> pl.Expr:
    """Days between two date expressions."""
    return (later - earlier).dt.total_days()


# ── String helpers ────────────────────────────────────────────────────


def postcode_area(col_name: str) -> pl.Expr:
    """Extract the outward code (first part) from a UK postcode."""
    return pl.col(col_name).str.split(" ").list.first()


# ── Column cleaning ──────────────────────────────────────────────────


def clean_columns(df: pl.LazyFrame) -> pl.LazyFrame:
    """Replace every ``.`` with ``_`` in column names.

    Example::
        df = clean_columns(quotes)
    """
    rename = {c: c.replace(".", "_") for c in df.collect_schema().names() if "." in c}
    return df.rename(rename) if rename else df


# ── Column matching ──────────────────────────────────────────────────


def cols_matching(all_cols: list[str], pattern_fn: Callable[[str], bool]) -> list[str]:
    """Return columns from *all_cols* where pattern_fn(col) is True."""
    return [c for c in all_cols if pattern_fn(c)]


# ── Additional driver features ────────────────────────────────────────

# Fields to extract from each additional_drivers.N sub-object.
# Maps dot-notation suffix → clean column suffix.
AD_FIELDS = {
    "gender": "gender",
    "marital_status": "marital_status",
    "relationship_to_proposer": "relationship",
    "employment_status": "employment_status",
    "licence.licence_type": "licence_type",
    "has_criminal_convictions": "has_criminal_convictions",
    "uk_resident_since_birth": "uk_resident_since_birth",
}


def driver_features(
    cols: list[str],
    cover_start: pl.Expr,
    max_additional: int = 4,
) -> tuple[list[pl.Expr], list[str], dict[str, str], list[str]]:
    """Build everything needed for additional driver columns.

    Returns (derived_exprs, age_col_names, rename_map, keep_cols).
    """
    derived: list[pl.Expr] = []
    age_col_names: list[str] = []
    rename_map: dict[str, str] = {}
    keep_cols: list[str] = []

    for i in range(1, max_additional + 1):
        pfx = f"additional_drivers.{i}"
        dob_col = f"{pfx}.date_of_birth"
        lic_col = f"{pfx}.licence.licence_date"

        if dob_col in cols:
            age_name = f"additional_driver_{i}_age"
            derived.append(years_between(to_date(dob_col), cover_start).alias(age_name))
            age_col_names.append(age_name)
            keep_cols.append(age_name)

        if lic_col in cols:
            lic_name = f"additional_driver_{i}_licence_years"
            derived.append(years_between(to_date(lic_col), cover_start).alias(lic_name))
            keep_cols.append(lic_name)

        for field, leaf in AD_FIELDS.items():
            src = f"{pfx}.{field}"
            if src in cols:
                rename_map[src] = f"additional_driver_{i}_{leaf}"

    # number_of_drivers and has_additional_driver
    dob_cols = cols_matching(
        cols,
        lambda c: c.endswith(".date_of_birth") and c.startswith("additional_drivers."),
    )
    if dob_cols:
        num_ad = pl.lit(0)
        for c in dob_cols:
            num_ad = num_ad + pl.col(c).is_not_null().cast(pl.Int64)
        derived.append((pl.lit(1) + num_ad).alias("number_of_drivers"))
        derived.append((num_ad > 0).alias("has_additional_driver"))
    else:
        derived.append(pl.lit(1).alias("number_of_drivers"))
        derived.append(pl.lit(False).alias("has_additional_driver"))

    keep_cols += list(rename_map.values()) + ["number_of_drivers", "has_additional_driver"]
    return derived, age_col_names, rename_map, keep_cols


# ── Add-on features ──────────────────────────────────────────────────


def addon_features(
    cols: list[str],
    addon_names: list[str],
) -> tuple[list[pl.Expr], dict[str, str], list[str]]:
    """Build everything needed for add-on columns.

    Returns (derived_exprs, rename_map, keep_cols).
    """
    derived: list[pl.Expr] = []
    rename_map: dict[str, str] = {}
    keep_cols: list[str] = []

    selected_cols: list[str] = []
    for name in addon_names:
        src = f"add_ons.{name}.selected"
        if src in cols:
            selected_cols.append(src)
            rename_map[src] = f"addon_{name}"

    if selected_cols:
        count_expr = pl.lit(0)
        for c in selected_cols:
            count_expr = count_expr + pl.col(c).fill_null(False).cast(pl.Int64)
        derived.append(count_expr.alias("number_of_add_ons"))
        keep_cols.append("number_of_add_ons")

    keep_cols += list(rename_map.values())
    return derived, rename_map, keep_cols


# ── Column mappings ──────────────────────────────────────────────────

# Flattened dot-notation → clean snake_case
RENAME_MAP = {
    # Quote
    "quote_metadata.quote_id": "quote_id",
    "quote_metadata.channel": "channel",
    # Policy
    "policy_details.cover_type": "cover_type",
    "policy_details.payment_frequency": "payment_frequency",
    "policy_details.voluntary_excess": "voluntary_excess",
    "policy_details.compulsory_excess": "compulsory_excess",
    "policy_details.ncd_years": "ncd_years",
    "policy_details.ncd_protected": "ncd_protected",
    "policy_details.annual_mileage": "annual_mileage",
    "policy_details.is_renewal": "is_renewal",
    "policy_details.usage.social_domestic_pleasure": "usage_sdp",
    "policy_details.usage.commuting": "usage_commuting",
    # Proposer
    "proposer.gender": "proposer_gender",
    "proposer.marital_status": "proposer_marital_status",
    "proposer.employment_status": "proposer_employment_status",
    "proposer.licence.licence_type": "proposer_licence_type",
    "proposer.is_homeowner": "proposer_is_homeowner",
    "proposer.is_main_driver": "proposer_is_main_driver",
    "proposer.has_criminal_convictions": "proposer_has_criminal_convictions",
    "proposer.uk_resident_since_birth": "proposer_uk_resident_since_birth",
    "proposer.access_to_other_vehicles": "proposer_access_to_other_vehicles",
    # Vehicle
    "vehicle.make": "vehicle_make",
    "vehicle.model": "vehicle_model",
    "vehicle.body_type": "body_type",
    "vehicle.fuel_type": "fuel_type",
    "vehicle.transmission": "transmission",
    "vehicle.engine_size_cc": "engine_size_cc",
    "vehicle.engine_power_bhp": "engine_power_bhp",
    "vehicle.number_of_seats": "number_of_seats",
    "vehicle.insurance_group": "insurance_group",
    "vehicle.estimated_value": "vehicle_value",
    "vehicle.is_imported": "vehicle_is_imported",
    "vehicle.has_been_modified": "vehicle_has_been_modified",
    "vehicle.overnight_location": "overnight_location",
    "vehicle.daytime_location": "daytime_location",
    "vehicle.owner": "vehicle_owner",
    "vehicle.current_mileage": "current_mileage",
    "vehicle.security.alarm": "security_alarm",
    "vehicle.security.immobiliser": "security_immobiliser",
    "vehicle.security.tracker": "security_tracker",
    # Address
    "address.postcode": "postcode",
    "address.city": "city",
    "address.years_at_address": "years_at_address",
}

ADDON_NAMES = [
    "breakdown_cover",
    "legal_expenses",
    "motor_legal_protection",
    "key_cover",
    "courtesy_car",
    "windscreen_cover",
    "excess_protection",
    "no_claims_step_back",
    "personal_accident",
    "personal_belongings",
    "tools_in_transit",
]

# Columns created by with_columns (not renamed from source)
DERIVED_COLS = [
    "proposer_age",
    "proposer_licence_length_years",
    "vehicle_age",
    "total_excess",
    "address_total_months",
    "postcode_area",
    "youngest_driver_age",
]

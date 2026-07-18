"""V064 reproduction.

Claim: GLMFactorConfig.removeFactor() deletes a factor from `terms` but NOT
from `interactions`. The orphaned factor name persists in an interaction and is
sent to the backend. In _build_interactions, a factor that is no longer in
`terms` (a) gets the categorical fallback spec and (b) forces all_in_terms=False,
so `include_main` is read from the (stale) config and can be True -- flipping the
safe `include_main=False` (which the function uses precisely to avoid duplicating
main effects -> perfect collinearity / singular matrix) to the unsafe True.

This script:
  1. Models the EXACT frontend removeFactor transform (terms only, interactions
     untouched) -- mirroring GLMFactorConfig.tsx:134-137.
  2. Feeds the post-removal config into the REAL backend _build_interactions
     (src/haute/modelling/_rustystats.py).
  3. Asserts on the specific WRONG values: include_main flips False -> True and
     the orphaned factor silently gains a {"type": "categorical"} main effect.

No disk I/O, no project files -- _build_interactions is a pure dict transform.
"""

from __future__ import annotations

from haute.modelling._rustystats import _build_interactions


def frontend_remove_factor(config: dict, name: str) -> dict:
    """Faithful port of GLMFactorConfig.tsx removeFactor (lines 134-137).

        const { [name]: _removed, ...rest } = terms
        onUpdate("terms", rest)

    Only `terms` is rewritten; `interactions` is left exactly as-is. We return a
    new config object the way React state would hold it after the onUpdate.
    """
    terms = dict(config.get("terms", {}))
    terms.pop(name, None)  # remove only from terms
    new_config = dict(config)
    new_config["terms"] = terms
    # NOTE: interactions deliberately NOT modified -- this is the bug.
    return new_config


def main() -> None:
    # --- Initial valid config: two factors, one interaction between them. ---
    # This is the normal, healthy state the UI produces.
    config = {
        "terms": {
            "age": {"type": "linear"},
            "region": {"type": "categorical"},
        },
        "interactions": [
            {"factors": ["age", "region"], "include_main": True},
        ],
    }

    # Baseline: with both factors present, the backend SAFELY forces
    # include_main=False (both factors already standalone main effects).
    baseline = _build_interactions(config["interactions"], config["terms"])
    assert len(baseline) == 1
    assert baseline[0]["include_main"] is False, (
        f"baseline include_main should be False, got {baseline[0]['include_main']!r}"
    )
    assert baseline[0]["region"] == {"type": "categorical"}
    print(f"[baseline] both factors in terms -> include_main={baseline[0]['include_main']} (safe)")

    # --- User clicks the trash icon on 'region'. ---
    # Frontend removes 'region' from terms but leaves the interaction dangling.
    config_after = frontend_remove_factor(config, "region")

    # Confirm the orphaned state the frontend leaves behind.
    assert "region" not in config_after["terms"], "region should be gone from terms"
    assert config_after["interactions"] == [
        {"factors": ["age", "region"], "include_main": True}
    ], "interaction still references the removed 'region' factor (orphaned)"
    print("[frontend] removeFactor('region'): terms drops region, interaction still references it")

    # --- Backend processes the orphaned config (what gets sent at train time). ---
    rs = _build_interactions(config_after["interactions"], config_after["terms"])
    assert len(rs) == 1
    actual_include_main = rs[0]["include_main"]
    actual_region_spec = rs[0].get("region")

    print(f"[backend]  after removal -> include_main={actual_include_main}, "
          f"region spec={actual_region_spec!r}")

    # --- The wrong values. ---
    # Expected (if removal were handled): the orphaned interaction factor would be
    # dropped/validated, leaving a single-factor (skipped) interaction OR the
    # same safe include_main=False. Instead:
    expected_safe = False
    if actual_include_main == expected_safe:
        raise SystemExit(
            "NOT REPRODUCED: include_main stayed safe (False); orphaned factor "
            "did not flip the semantics."
        )

    assert actual_include_main is True, (
        "BUG: removing 'region' flipped include_main from the safe False to True. "
        f"Got {actual_include_main!r}. Backed by _rustystats.py:91-95: orphaned "
        "factor makes all_in_terms=False, so the stale config include_main=True wins."
    )

    # And the orphaned factor silently gets an unconfigured categorical main effect.
    assert actual_region_spec == {"type": "categorical"}, (
        "BUG: orphaned 'region' silently re-introduced as categorical "
        f"(fallback at _rustystats.py:86-87). Got {actual_region_spec!r}."
    )

    print()
    print("REPRODUCED: orphaned interaction factor flips include_main False -> True")
    print("  and re-introduces an unconfigured categorical main effect ->")
    print("  duplicate main effect / singular-matrix risk (per docstring l.69-74).")


if __name__ == "__main__":
    main()

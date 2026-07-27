# I/O layer roadmap

## Scope

Owns input/output correctness, registry-backed formats, shared snapshots,
publication and overwrite semantics, cache lifecycle, authoring feedback, and
I/O performance boundaries.

The previously audited I/O packages are delivered. Their current behaviour and
evidence live in the I/O, caching, Databricks, codegen, server-api, and frontend
editor specifications and ordinary regression tests.

## Priorities

No active implementation packages.

## Planned improvements

There are no active I/O roadmap packages.

## Delivered outcomes

- `IO-JSON-01` closes the v2 API-input scalar boundary: `emit` and
  `selected` accept only real booleans, `status` accepts only
  `Confirmed|Inferred`, and every failure names its exact field path before
  shredding can apply truthiness coercion.

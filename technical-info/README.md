# technical-info

This folder contains technical information about **branches** — what a long-running branch
contributes, where it stands against `main`, and what a merge must preserve — for use by agents or
developers picking the work up later. One artefact per branch, named after the branch's workstream
(e.g. [UI_IMPROVEMENTS.md](UI_IMPROVEMENTS.md)).

The folder is an OKF bundle (see `notes-haute/common/OKF.md` for the house profile): artefacts are
`.md` files with YAML frontmatter (required `type`, plus `title`/`description`/`tags`/`timestamp`),
and [index.md](index.md) declares `okf_version` and lists the contents.

Artefacts describe the branch **as it currently is**, in the spirit of `docs/specs/` (see its
`TEMPLATE.md`): observable contributions first, then reconcile/merge detail; suspected problems are
flagged with `> NOTE:` callouts rather than smoothed over.

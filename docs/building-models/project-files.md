# Project Files

A Haute project keeps its files in two kinds of place. The pipeline folder is versioned with
the pipeline. The local folders at the project root stay on your machine.

## Where files go

| Folder | What goes in it | Versioned |
|---|---|---|
| `rating/` | The pipeline: `main.py`, its layout file, node settings in `config/`, shared Python in `utility/` | Yes |
| `rating/files/data/` | Tables and data the pipeline ships with, such as a postcode lookup | Yes |
| `rating/files/models/` | Model files with their feature contracts, and optimiser results | Yes |
| `data/` | Local data: portfolios, extracts, anything large or personal | No |
| `outputs/` | What runs write: Data Output files and exported data | No |

`rating/` is the pipeline folder of a new project. A project whose `haute.toml` names another
pipeline file uses that file's folder.

Everything in the pipeline folder goes into version history. Save records the pipeline's own
files, and Commit also records every other file you add to the pipeline folder, so a model or
table in `rating/files/` reaches CI and deploys with the pipeline. A file larger than 100 MiB
can't be versioned (GitHub refuses it), so Commit stops and names it. Keep a file that size
in `data/`, or log the model to MLflow.

`data/` and `outputs/` stay on your machine. Moving to another version, catching up or
branching away deletes `outputs/`, because what a run writes belongs to the version that
wrote it. Keep anything you need in `data/` or the pipeline folder.

**Save model to file** and an optimiser result's **Save to file** put a filename without a
folder in `rating/files/models/`.

## The Files buttons

Two buttons in the toolbar open the project's files beside the canvas or the workbench:

- **Pipeline files** opens `rating/files/`. A file you add here is saved into version history
  straight away, and a file you delete is recorded the same way.
- **Local files** opens `data/` and `outputs/`. Files here stay on your machine. You can add
  and delete files in `data/`; `outputs/` only downloads, because a run writes it.

The panel shows one folder at a time. Click a folder to open it, and click a name in the path
under the title to go back. Each file shows its size and when it last changed, with
**Download** and, where files can be added, **Delete** on the right of its row.

To add files, drag them from your computer onto the panel: onto the list to add them to the
folder that's open, or onto a folder's row to add them there. **Upload** at the top of the
panel picks files instead. Dropped folders aren't added; drop the files inside them. If a
file with the same name is already there, Haute asks before replacing it. A replaced or
deleted pipeline file stays in version history, but a local one can't be recovered.

In Pipeline files a file marked **New** isn't in version history yet, such as one copied in
with your file explorer; the next Commit adds it. One marked **Ignored** is left out by your
`.gitignore`, so it is never versioned. A file larger than 100 MiB can't be added to
Pipeline files: put it in `data/`.

A hosted Haute app keeps only what version history keeps, so it takes no files in Local
files.

!!! note "Projects made before this layout"
    Older projects have empty `rating/data/` and `rating/outputs/` folders that nothing uses:
    delete them. If your `.gitignore` has a `data/` line, change it to `/data/`. Without the
    leading slash it also ignores `rating/files/data/`. Add `/outputs/` and `/.cache/` while
    you're there.

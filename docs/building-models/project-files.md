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

!!! note "Projects made before this layout"
    Older projects have empty `rating/data/` and `rating/outputs/` folders that nothing uses:
    delete them. If your `.gitignore` has a `data/` line, change it to `/data/`. Without the
    leading slash it also ignores `rating/files/data/`. Add `/outputs/` and `/.cache/` while
    you're there.

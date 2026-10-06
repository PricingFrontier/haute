# Setting Up the Assistant

The **Assistant** button in the toolbar opens the pricing assistant: a chat panel on the right where you describe a change to the pipeline and the assistant builds it on the canvas. This page explains what you see in that panel, how to switch the assistant on for a project, and what each setting allows it to send to the model.

---

## What the assistant does

The assistant edits the pipeline that is open on the canvas. It can:

- build and edit nodes, writing their logic as steps you can open in the step builder
- set up banding, rating tables and joins
- configure outputs and the quote response
- set up model training and scoring nodes

It cannot run the pipeline, train models, deploy, or use Git. Every change it saves is recorded the same way as one of your own saves, and each one appears in the chat as a change card with **Undo this change** and **Compare**.

A new chat lists these with a few example requests. Clicking an example puts it in the message box so you can adjust it before sending.

### While it works

While the assistant is working, the canvas is read-only. A pill at the top of the canvas reads **Assistant is working**; its **Stop** button stops the assistant, as does **Stop** in the panel. Changes it already saved stay saved. You can still select nodes, pan, zoom, and look at previews while you wait.

You can close the panel while it works. The toolbar's Assistant button shows a spinner until it finishes, and a dot if it finished while the panel was closed.

When you ask for something in several stages, such as a source, a banding, a rating and a response, the assistant lists the stages in a **Checklist** above the message box. Each stage shows whether the assistant has marked it done and the changes saved for it; click a change to scroll to its change card. A stage with saved changes that is not marked done is still in progress, and undoing a stage's only change opens it again. If the assistant stops before the end, the open stages stay on the list, and asking it to continue picks them up.

### Checking the data

When the project allows it (`allow_aggregate_statistics`, below), the assistant checks each change against your data before it saves it: it runs the nodes the change adds or edits the way a preview does, writes nothing, and reads back counts only. While it does, the activity row reads **Checking the data**. If the counts show the change is wrong, such as every row falling into a band's default or a join that matches nothing, the assistant corrects the change before saving it.

The change card then shows what the check found:

- Findings worth a look are listed under the node they concern, for example "All 1,204 rows fell into the default band of age_band."
- Findings that are often intended, such as a join that matches most but not all rows, are folded under **informational findings**; click the line to open them.
- **Data checked: no findings.** means the check ran and found nothing to flag. It does not prove the change is right.
- **Data not checked:** says why the check could not run, for example because the preview worker was busy, and **Not checked:** names nodes it skipped and what would let them be checked, such as previewing an input first.
- **Data findings measured on inputs that have changed since.** means a data file changed after the check ran.

You can also ask why a node fails or why a column is empty for some rows: the assistant runs the same check over the saved pipeline up to that node and answers from its counts, such as which join leaves rows unmatched.

Only counts reach the model, never the values in your data.

### Pointing it at nodes and errors

Nodes you select on the canvas are sent with your message, so "join this onto the quotes" means the selected node. The chip above the message box names them.

When a node's preview fails, the error in the preview panel offers **Ask the assistant to fix**. It selects that node, opens the assistant with a request to fix the error, and adds a **Run error** chip naming the node. The assistant checks the node again to see the error for itself; how much of the error's text it sees depends on `allow_row_samples` below. Remove the chip with its **×** if you would rather not send it.

---

## Readiness messages

The panel opens on the list of chats for the pipeline on the canvas. If the assistant cannot be used yet, a card above the list says why and offers **Check again**.

**Assistant is not set up** means the project has no working assistant settings. The card shows one of these reasons:

| Reason in the panel | What to do |
|---|---|
| No [assistant] table is configured in haute.toml. | Add the `[assistant]` table described below. |
| Missing assistant model. | Add `model` to the `[assistant]` table. |
| Unknown assistant provider | Set `provider` to `databricks`, `anthropic` or `openai`. |
| Missing required [assistant].egress table | Add the `[assistant.egress]` table described below. |
| Missing API key environment variable: | Add the named key to the project's `.env` file. |
| Missing Databricks host environment variable: DATABRICKS_HOST. | Add `DATABRICKS_HOST` to the project's `.env` file. |

**Assistant cannot edit this project** means the settings are complete but edits are switched off. The assistant records each change it saves in Git, so it needs Git and a working branch:

| Reason in the panel | What to do |
|---|---|
| Initialise Git before using assistant mutations. | The project folder is not a Git repository yet. |
| Create or select a working branch in the Git panel before using assistant mutations. | Open the branch indicator in the toolbar and choose a working branch. |
| Resolve the working-branch divergence in the Git panel before using assistant mutations. | Bring the working branch back in line from the Git panel. |
| Attach HEAD by selecting or creating a working branch in the Git panel before using assistant mutations. | You are looking at an earlier version; choose a working branch in the Git panel. |
| Git is not available on this host; assistant edits need Git to record each change. | Install Git, then restart Haute. |
| [assistant.egress].max_sensitivity is "public" | The egress policy lets the assistant read nothing from the project; see below. |

**Assistant settings could not be read** means `haute.toml` itself has a mistake, such as a misspelt key or a value of the wrong kind. The card names the problem. Fix `haute.toml`, then click **Check again**.

Changes to `haute.toml` are picked up by **Check again**. Changes to `.env` are read when Haute starts, so restart Haute after editing it.

Once the assistant is ready, the panel header names the model it uses, and a line under the header shows where requests go and the egress policy in force.

---

## The settings in haute.toml

The assistant is switched on per project, in the project's `haute.toml`:

```toml
[assistant]
provider = "databricks"
model = "databricks-qwen35-122b-a10b"

[assistant.egress]
trust = "organization"
max_sensitivity = "restricted"
allow_project_knowledge = true
allow_executable_source = true
allow_row_samples = false
allow_aggregate_statistics = true
```

### [assistant]

| Setting | What it means |
|---|---|
| `provider` | Who serves the model: `databricks` (Databricks model serving), `anthropic`, or `openai`. |
| `model` | The model's name at that provider. |
| `base_url` | Only for `openai`: the address of an OpenAI-compatible endpoint, when it is not OpenAI itself. Databricks and Anthropic do not accept it. |

### Choosing a Databricks model

With `provider = "databricks"`, `model` is the name of a serving endpoint in your workspace. The assistant is tested against `databricks-qwen35-122b-a10b`, so start with that one. Other open models your workspace serves can be named the same way.

The workspace address comes from `DATABRICKS_HOST`, not from `haute.toml`, so the assistant always talks to the same workspace as the rest of the project.

### Choosing a Claude model

With `provider = "anthropic"`, `model` must be a Claude model that thinks before it answers, such as `claude-opus-5-5` or `claude-sonnet-5-5`. Sending a message with any other Claude model is refused, and the message names the models that work. While the model thinks, the chat shows **Thinking…**.

### [assistant.egress]

The egress policy decides what project material the assistant may send to the model. Every setting is required, so the policy is always a deliberate choice.

| Setting | What it allows |
|---|---|
| `trust` | Who runs the endpoint. `local` is a model on this computer (the address must be `localhost`). `organization` is a service your organisation controls, such as your Databricks workspace (HTTPS only). `external` is a third-party service (HTTPS only). |
| `max_sensitivity` | The most sensitive material that may be sent. `internal` allows the pipeline's structure: node names, connections and column schemas. `restricted` also allows node settings. `public` allows neither, which leaves the assistant unable to read or edit the pipeline. |
| `allow_project_knowledge` | Whether the assistant may use background facts about the project: a summary of the saved pipeline, a digest of `haute.toml` without its values, and the project's own documentation. |
| `allow_executable_source` | Whether the assistant may read the code in your nodes and in the pipeline's imports. Without it, code is withheld when the assistant inspects a node. |
| `allow_row_samples` | Whether actual values from your data may be sent: profiles of a column's values, and error messages that quote a value. Without it, the assistant sees column names and types, and an error is reported by its type and the step that raised it. |
| `allow_aggregate_statistics` | Whether the assistant may run the nodes it changes, or the nodes behind one you ask about, over your data, as a preview does, to check what they produce, and send the model counts and shares from that check: how many rows each node reads and keeps, how many of a new column's values are missing, how many rows fall into each band, how many rows a rating table has no entry for, and how many rows a join matches. The values themselves are never sent. What the check found appears on each change card (see [Checking the data](#checking-the-data)). Without it, the assistant checks only that a change fits the pipeline's columns and types, not that its data comes out right. |

An `external` endpoint must use `max_sensitivity = "public"` and cannot allow executable source, row samples or aggregate statistics. In practice that means an external service gets an assistant that can neither read nor edit the pipeline, so use `organization` for your own Databricks workspace.

---

## The keys in .env

Credentials never go in `haute.toml`. They go in the project's `.env` file, which Git ignores, and Haute reads them when it starts:

| Provider | Keys |
|---|---|
| `databricks` | `DATABRICKS_HOST` (your workspace address, starting `https://`) and `DATABRICKS_TOKEN` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openai` | `OPENAI_API_KEY` |

A Databricks project that already deploys to Databricks usually has both keys in `.env` already. Two optional keys tune the assistant: `HAUTE_ASSISTANT_MAX_OUTPUT_TOKENS` limits how long each reply from the model may be, and `HAUTE_ASSISTANT_TURN_TIMEOUT` sets how many seconds the assistant may work on one message before it is stopped (600 by default).

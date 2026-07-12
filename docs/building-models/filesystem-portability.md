# Filesystem Portability

Every data path in a Haute pipeline — a **Data Source** `path`, an **External File** `path`, a Quote Input file — is ultimately a filename handed to a filesystem. On a single machine this just works, and you can skip this page.

Read it if a pipeline (or its data folder) **moves between machines or operating systems**: a checkout shared between a Windows laptop and a Linux server, work inside WSL, files on a network mount, or a Databricks/CI environment picking up a pipeline authored on a Mac. Different filesystems disagree about when two spellings of a filename are "the same file", and those disagreements produce bugs that appear only after the move.

---

## What Haute does — and deliberately doesn't do

Haute passes your path to the operating system **exactly as you spelled it**. It normalises path *shape* — backslashes become forward slashes, relative paths are anchored to the pipeline folder — but it never rewrites the *names*: no case-folding, no accent/Unicode normalisation, no snapping to the on-disk spelling.

That means **which file answers (or whether any file answers) is the filesystem's decision, not Haute's** — and different filesystems decide differently. Haute keeps this deliberate: silently "fixing" a spelling would make pipelines open different files on different platforms with no visible signal.

---

## Which matching rule applies?

The common cases first — if you're in this table's first two rows and your files stay on one machine or in OneDrive/SharePoint, the simple mental model is correct and nothing below will ever bite you:

| Where the lookup happens | Case (`Foo.csv` vs `foo.csv`) | Accents (`café` NFC vs NFD) |
|---|---|---|
| Windows on NTFS | Same file | **Different files** |
| macOS on APFS/HFS+ | Same file | Same file |
| Linux on ext4/xfs | **Different files** | **Different files** |

The subtlety, and the limit of the simple mental model: **the rule follows the software stack that performs the lookup, not the operating system you sit at.** "Windows machine" does not automatically mean "case-insensitive":

| Setup | Effective rule |
|---|---|
| Haute running **inside WSL2** on a Windows machine, data on the Linux filesystem | Exact bytes — it's a Linux process on ext4 |
| Windows app reading WSL files via `\\wsl.localhost\...` | Exact bytes — the Linux side performs the lookup |
| Windows with a **third-party ext4 driver** | Driver-defined: usually Windows-style case-insensitive matching over byte-exact entries — and if genuine case-twins exist on the disk, *which one you get is undefined* |
| Network mounts (SMB/NFS shares) | The **server's** filesystem and protocol settings decide, not your client OS |
| NTFS folders with per-directory case-sensitivity enabled | Exact bytes for that folder only |

!!! warning "Accents are the quiet one"
    Only macOS treats the two Unicode spellings of an accented character (`é` typed directly vs `e` + combining accent) as the same name. A filename created on a Mac can carry the decomposed form invisibly — it looks identical on screen, opens fine on the Mac, and misses on both Windows **and** Linux. If files are shared across platforms, prefer plain ASCII names.

---

## The three traps

1. **Case-twins created on Linux.** `Rates.csv` and `rates.csv` coexist happily on ext4. Move that folder to Windows or macOS and the pair collapses to one file — the reference is now ambiguous, and on exotic setups (third-party drivers) which file wins is anyone's guess.
2. **Spelling drift.** The config says `rates.csv`, the disk says `Rates.csv`. On Windows and macOS this opens fine — and breaks the moment the checkout lands on Linux. The forgiving platforms *hide* the mismatch; the strict one reveals it.
3. **Invisible accent forms.** As above — created on a Mac, identical on screen, missing everywhere else.

---

## How Haute protects you

- **Save-time guards.** Node and table names that would collide case-insensitively on disk (and Windows-reserved names like `CON` or `LPT1`) are rejected when you save — on *every* platform, so a pipeline authored on Linux stays loadable on Windows and vice versa.
- **Access-time warning.** When a data file is read whose path has case-equivalent sibling spellings on disk (trap 1 or 2 in the making), Haute logs a warning naming the twins in the server log. Treat it as a portability defect and fix the spelling before sharing the checkout.
- **No silent fixing.** By design — an error you can see beats a file you didn't mean to open.

---

## Practical rules

- Match the config spelling to the on-disk name **exactly**, even where your OS forgives you — the next platform may not.
- A boring naming convention (lowercase, underscores, ASCII) for data files sidesteps every trap on this page.
- If you see the case-ambiguity warning in the server log, resolve it before the checkout travels.

**See also:** [Data Source](nodes/data-source.md), [External File](nodes/external-file.md), [Preparing Your Data](preparing-your-data.md).

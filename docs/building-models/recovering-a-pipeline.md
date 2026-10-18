# Recovering a Pipeline

A pipeline file can hold something the editor can't load as written: a setting an older
version of Haute wrote, a hand edit, or a file changed outside the editor. Haute then opens
the pipeline in recovery mode rather than refusing it. A banner across the canvas says how
many issues there are, and **Review detected issues** lists each one with where it is. The
pipeline is shown as it is, and you can't edit, save or run it until every issue is resolved.

## Nodes

A node Haute can't load is marked **unavailable**. Nodes downstream of it are marked
**blocked**; they need nothing doing, and load once the node above them does. Select an
unavailable node to see what's wrong with it and what you can do:

- **Recover settings** rebuilds the node from the settings it has. Its code and every valid
  setting stay; a setting it lacks is filled in as a new node's would be, and one that no
  longer belongs is dropped. A summary lists each change. Try this first.
- **Reset node** replaces the node's settings and code with a new node's. An Edge Join is
  never reset, because a new join has no keys: recover it instead.
- **Remove unavailable node** deletes the node and its connections. Its config file stays
  unless you also choose to delete it.

A node whose connections are wrong, such as an Explore with no input, offers only removal:
rebuilding its settings can't mend its connections.

## Submodels

A submodel with a broken node inside it is unavailable as a whole, and the canvas doesn't
open it. Select it: its panel lists **Unavailable nodes in this submodel**, each with its
problem and the same **Recover settings**, **Reset node** and **Remove node** actions. Once
they're fixed, the submodel opens again.

A copy of a submodel that names a missing original, or that breaks another copy rule,
is unavailable on its own and offers removal. A submodel whose file is missing or can't
be read also offers removal only: restore the file, or remove the submodel.

## Connections

A broken `connect()` line has a **Remove connection** button beside it in the issues list.
This covers a connection to a node that doesn't exist, a repeat of another connection, a
connection written with something other than plain names, and a connection from a Quote
Input that names none of its tables. Removing it deletes that one line; the nodes it joined
stay, and you can connect them again on the canvas once the pipeline loads.

## Problems fixed in the file

Some problems have nothing in the editor to rebuild, and are fixed in the pipeline's file:

- Python that doesn't parse. The editor shows the file and where the error is.
- A pipeline name, description or global constants path that isn't a plain string.
- A statement outside the pipeline's declarations, such as `rate = 1.5` at the bottom of the
  file, which a save would drop.
- Two nodes with the same function name.
- A submodel registered in a form Haute no longer reads, such as one using `definition_id=`.
  Removing it is the only fix in the editor.
- A chained `connect()` holding a broken connection. Write one `connect()` per line, then
  remove the broken one.

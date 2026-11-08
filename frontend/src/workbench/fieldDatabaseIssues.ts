/** Plain-language actions for a field's current database comparison (specs/workbench). */
import type { SchemaColumn, SchemaTable, WorkbenchCatalogueTable, WorkbenchStoreCompareResponse, WorkbenchStoreDifference } from "../api/types"
import { IDENTITY_NAMES, QUOTE_DETAILS_NAME } from "../utils/quoteIdentity"
import { COLUMN_TYPES } from "./columnTypes"

interface FieldDatabaseIssue {
  title: string
  description: string
  action: string
}

const DATABASE_TYPES: Record<string, string> = {
  string: "Text", integer: "Integer", float: "Decimal", boolean: "True/false", date: "Date", datetime: "Date and time",
}

export function fieldDatabaseIssues(
  table: SchemaTable,
  column: SchemaColumn,
  differences: WorkbenchStoreDifference[],
  answer: WorkbenchStoreCompareResponse,
  stored: WorkbenchCatalogueTable | undefined,
): FieldDatabaseIssue[] {
  const renamed = differences.some((each) => each.kind === "renamed")
  const key = column.index !== null || IDENTITY_NAMES.has(column.name)
  const expectedType = COLUMN_TYPES[column.type].label
  const databaseColumn = () => {
    const rowKey = stored?.primary_key.filter((name) => !IDENTITY_NAMES.has(name))
    const source = !renamed ? column.name : column.index !== null && rowKey?.length === 1 ? rowKey[0]
      : answer.renames.find((each) => each.table === table.name && each.column === column.name)?.was
    const found = stored?.columns.find((each) => each.name === source)
    if (found === undefined) throw new Error(`Database comparison refers to an absent column for ${table.name}.${column.name}`)
    return found
  }
  const sides = () => {
    const found = databaseColumn()
    return `Haute: ${column.name} (${expectedType}). Database: ${found.name} (${DATABASE_TYPES[found.kind] ?? found.type}).`
  }
  const addition = answer.changes.find((each) => each.table === table.name &&
    (each.kind === "create table" || (each.kind === "add column" && each.column === column.name)))
  const issues: FieldDatabaseIssue[] = []
  if (addition !== undefined) {
    if (addition.disposition !== "sql") {
      issues.push({ title: "Cannot add this field yet", description: addition.description,
        action: "Correct the field or table name in Haute as described above, then export SQL again." })
    } else if (addition.kind === "create table") {
      issues.push({ title: "Table missing", description: `${table.name} is not in this database.`,
        action: `Use Export SQL, then run the query on this database to create ${table.name}.` })
    } else {
      let action = `Export SQL adds ${column.name}. Run the query on this database.`
      if (renamed) action += ` Copy any existing values from ${databaseColumn().name} manually; the old column stays unchanged.`
      if (key) action += ` Fill every row, make ${column.name} required (NOT NULL), and update the table's primary key to use it.`
      issues.push({ title: "New column needed", description: renamed ? sides() : `${column.name} is not in this database.`, action })
    }
  }
  // A new definition supplies the name, type and text comparison together. The old
  // column is untouched; presenting each diagnostic as another repair would mislead.
  const coveredByAddition = new Set(["missing", "renamed", "type", "collation", "null"])
  for (const difference of differences) {
    if (addition !== undefined && coveredByAddition.has(difference.kind)) continue
    switch (difference.kind) {
      case "missing":
        issues.push({ title: difference.column === null ? "Table missing" : "Column missing",
          description: `${difference.column === null ? table.name : column.name} is not matched in this database.`,
          action: "Check the name in Haute against the database. No addition is available in Export SQL; see SQL details." })
        break
      case "renamed":
        issues.push({ title: "Different column name", description: sides(),
          action: key
            ? `Move any needed values to ${column.name}, fill every row, make it required (NOT NULL), and update the primary key. Export SQL does not change existing columns.`
            : `Move any needed values to ${column.name} manually. Export SQL does not rename columns or copy data.` })
        break
      case "type":
        issues.push({ title: "Different data type", description: sides(),
          action: IDENTITY_NAMES.has(column.name) || table.name === QUOTE_DETAILS_NAME
            ? `Convert this database column to ${expectedType} after checking its existing values. This Haute field has a fixed type; Export SQL does not convert existing columns.`
            : "Change the type in Haute to match, or convert the database column after checking its existing values. Export SQL does not convert existing columns." })
        break
      case "collation":
        issues.push({ title: "Different text comparison", description: "This field needs exact text matching, including upper- and lower-case letters.",
          action: "Apply the collation in SQL details. Export SQL does not change existing columns." })
        break
      case "null":
        issues.push({ title: "Different blank-value rules",
          description: key ? "This key needs a value in every row." : "Haute allows this field to be blank, but the database requires a value.",
          action: key ? "Fill any missing values, then make the database column required (NOT NULL)."
            : "Allow NULL in this column so blank values can be saved." })
        break
      case "key":
        issues.push({ title: "Different row identifier", description: "The database uses a different primary key to identify rows.",
          action: "Update the primary key to match Haute after checking existing rows. Export SQL does not update keys." })
        break
      case "rows":
        issues.push({ title: "Different row layout", description: "Haute and the database use different numbers of rows per quote.",
          action: "Match the Rows setting in Haute, or migrate the table's existing rows and key." })
        break
      case "refers":
        issues.push({ title: "Missing quote link", description: "This table is not linked to quote_details as Haute expects.",
          action: "Add the foreign key shown in SQL details after checking the existing quotes." })
        break
      case "store":
        issues.push({ title: "Internal database field needs repair", description: "A field managed by Haute is missing or has a different definition.",
          action: "Repair the field using the expected definition in SQL details." })
        break
    }
  }
  return issues
}

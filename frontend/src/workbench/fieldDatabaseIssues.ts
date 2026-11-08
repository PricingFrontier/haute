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
  const retainedRequiredSource = renamed && !databaseColumn().nullable
  if (addition !== undefined) {
    if (addition.disposition !== "sql") {
      issues.push({ title: "Cannot add this field yet", description: addition.description,
        action: "Correct the field or table name, then export SQL again." })
    } else if (addition.kind === "create table") {
      issues.push({ title: "Table missing", description: `${table.name} is not in this database.`,
        action: `Run Export SQL on this database to create ${table.name}.` })
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
    if (addition !== undefined && coveredByAddition.has(difference.kind) &&
      !(difference.kind === "null" && retainedRequiredSource)) continue
    switch (difference.kind) {
      case "missing":
        issues.push({ title: difference.column === null ? "Table missing" : "Column missing",
          description: `${difference.column === null ? table.name : column.name} is missing from this database.`,
          action: "Check the database name in SQL details; Export SQL has no addition for it." })
        break
      case "renamed":
        issues.push({ title: "Different column name", description: sides(),
          action: key
            ? `Fill ${column.name}, make it required (NOT NULL), and update the primary key manually.`
            : `Copy values to ${column.name} manually; Export SQL leaves the old column unchanged.` })
        break
      case "type":
        issues.push({ title: "Different data type", description: sides(),
          action: IDENTITY_NAMES.has(column.name) || table.name === QUOTE_DETAILS_NAME
            ? `Convert this database column to ${expectedType} manually after checking its values; Haute has a fixed type.`
            : "Change the type in Haute to match, or convert the data manually. Export SQL does not convert existing columns." })
        break
      case "collation":
        issues.push({ title: "Different text comparison", description: "This field needs exact text matching, including case.",
          action: "Apply the collation in SQL details. Export SQL does not change existing columns." })
        break
      case "null":
        if (retainedRequiredSource) {
          const source = databaseColumn().name
          issues.push({ title: "Old column still requires a value",
            description: `Haute writes ${column.name}, leaving ${source} empty.`,
            action: `Allow NULL in ${source}; Export SQL leaves its NOT NULL constraint unchanged.` })
          break
        }
        issues.push({ title: "Different blank-value rules",
          description: key ? "This key needs a value in every row." : "The database requires a value; Haute allows blanks.",
          action: key ? "Fill any missing values, then make the database column required (NOT NULL)."
            : "Allow NULL in this column so blank values can be saved." })
        break
      case "key":
        issues.push({ title: "Different row identifier", description: "The database uses a different primary key.",
          action: "Check existing rows and update the primary key manually to match Haute." })
        break
      case "rows":
        issues.push({ title: "Different row layout", description: "The number of rows per quote differs.",
          action: "Match the Rows setting in Haute, or migrate the table's existing rows and key." })
        break
      case "refers":
        issues.push({ title: "Missing quote link", description: "The link to quote_details is missing.",
          action: "Check existing quotes and add the foreign key in SQL details." })
        break
      case "store":
        issues.push({ title: "Internal database field needs repair", description: "A Haute-managed field has the wrong definition.",
          action: "Repair its definition as shown in SQL details." })
        break
    }
  }
  return issues
}

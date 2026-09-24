import { describe, expect, it } from "vitest"

import {
  validateUtilityDeleteResponse,
  validateUtilityListResponse,
  validateUtilityReadResponse,
  validateUtilityWriteResponse,
} from "../../generated/api-contracts.utility.validators.mjs"
import {
  validateCatalogListResponse,
  validateSchemaListResponse,
  validateTableListResponse,
  validateWarehouseListResponse,
} from "../../generated/api-contracts.databricks.validators.mjs"
import { expectGeneratedContract } from "../../types/generatedContractValidation"
import { loadUiContractFixture } from "../../testSupport/uiContractFixtures"

// The fixtures are exactly what the backend serializes
// (tests/test_backend_frontend_contracts.py), so each generated validator
// must accept its own module's payloads.
describe("generated response contracts", () => {
  it("accept the backend's utility payloads", () => {
    const listed = expectGeneratedContract(
      "UtilityListResponse",
      validateUtilityListResponse,
      loadUiContractFixture("utility_list_response"),
    )
    const read = expectGeneratedContract(
      "UtilityReadResponse",
      validateUtilityReadResponse,
      loadUiContractFixture("utility_read_response"),
    )
    const written = expectGeneratedContract(
      "UtilityWriteResponse",
      validateUtilityWriteResponse,
      loadUiContractFixture("utility_write_response"),
    )
    const deleted = expectGeneratedContract(
      "UtilityDeleteResponse",
      validateUtilityDeleteResponse,
      loadUiContractFixture("utility_delete_response"),
    )

    expect(listed.files[0]?.module).toBe("helpers")
    expect(read.content).toContain("helper")
    expect(written.import_line).toContain("utility.helpers")
    expect(deleted.module).toBe("helpers")
  })

  it("name the field of a malformed or incomplete utility payload", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("utility_write_response")
    const { error_line: _errorLine, ...withoutErrorLine } = fixture

    expect(() =>
      expectGeneratedContract("UtilityWriteResponse", validateUtilityWriteResponse, {
        ...fixture,
        import_line: 123,
      }),
    ).toThrow("UtilityWriteResponse: invalid contract at /import_line: type")
    // A defaulted field is still always sent, so its absence is drift.
    expect(() =>
      expectGeneratedContract("UtilityWriteResponse", validateUtilityWriteResponse, withoutErrorLine),
    ).toThrow("UtilityWriteResponse: invalid contract at /error_line: required")
    expect(() =>
      expectGeneratedContract("UtilityListResponse", validateUtilityListResponse, {
        files: [{ name: "helpers" }],
      }),
    ).toThrow("UtilityListResponse: invalid contract at /files/0/module: required")
  })

  it("accept complete Databricks listings and reject a missing or mistyped field", () => {
    const warehouses = expectGeneratedContract("WarehouseListResponse", validateWarehouseListResponse, {
      warehouses: [{ id: "w1", name: "Shared", http_path: "/sql/1.0/warehouses/w1", state: "RUNNING", size: "" }],
    })
    const catalogs = expectGeneratedContract("CatalogListResponse", validateCatalogListResponse, {
      catalogs: [{ name: "main", comment: "" }],
    })
    const schemas = expectGeneratedContract("SchemaListResponse", validateSchemaListResponse, {
      schemas: [{ name: "pricing", comment: "quotes" }],
    })
    const tables = expectGeneratedContract("TableListResponse", validateTableListResponse, {
      tables: [{ name: "quotes", full_name: "main.pricing.quotes", table_type: "MANAGED", comment: "" }],
    })

    expect(warehouses.warehouses[0]?.http_path).toBe("/sql/1.0/warehouses/w1")
    expect(catalogs.catalogs[0]?.name).toBe("main")
    expect(schemas.schemas[0]?.comment).toBe("quotes")
    expect(tables.tables[0]?.full_name).toBe("main.pricing.quotes")
    expect(() =>
      expectGeneratedContract("TableListResponse", validateTableListResponse, {
        tables: [{ name: "quotes", full_name: "main.pricing.quotes", comment: "" }],
      }),
    ).toThrow("TableListResponse: invalid contract at /tables/0/table_type: required")
    expect(() =>
      expectGeneratedContract("WarehouseListResponse", validateWarehouseListResponse, {
        warehouses: [{ id: 1, name: "Shared", http_path: "/p", state: "RUNNING", size: "" }],
      }),
    ).toThrow("WarehouseListResponse: invalid contract at /warehouses/0/id: type")
  })
})

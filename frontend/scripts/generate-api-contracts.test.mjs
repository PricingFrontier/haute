import assert from "node:assert/strict";
import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import {
  CONSTANTS_FILENAME,
  GENERATED_TYPES_FILENAME,
  extractContractSchema,
  extractModuleContractSchema,
  run,
  validatorModules,
} from "./generate-api-contracts.mjs";

const sourceDirectory = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../src/generated",
);

test(
  "API contract artifacts are deterministic, self-contained, and check mode is read-only",
  async (t) => {
    const temporaryDirectory = await mkdtemp(path.join(os.tmpdir(), "haute-api-contracts-"));
    t.after(() => rm(temporaryDirectory, { recursive: true, force: true }));
    const schema = JSON.parse(
      await readFile(path.join(sourceDirectory, "api-contracts.schema.json"), "utf8"),
    );
    const expectedClosures = new Map([
      [
        "ExecutionStrategyDiagnosticPayload",
        [
          "ExecutionStrategyBoundaryCollectionPayload",
          "ExecutionStrategyBoundaryPayload",
          "ExecutionStrategyProvenanceCollectionPayload",
          "ExecutionStrategyProvenancePayload",
          "ExecutionStrategyReasonCollectionPayload",
          "ExecutionStrategyReasonPayload",
        ],
      ],
      [
        "ExploreChartsConfig",
        [
          "ChartAxes",
          "ChartAxisConfig",
          "ChartCategory",
          "ChartLegend",
          "ChartSecondaryAxisConfig",
          "ChartSeriesOverride",
          "ChartValueEncoding",
          "ExploreChartConfig",
          "JsonValue",
        ],
      ],
      ["UtilityListResponse", ["UtilityFileItem"]],
    ]);
    for (const [definitionName, expectedDefinitions] of expectedClosures) {
      const extracted = extractContractSchema(schema, definitionName);
      assert.deepEqual(Object.keys(extracted.schema.$defs), expectedDefinitions);
      assert.equal(extracted.reachableDefinitionCount, expectedDefinitions.length + 1);
      assert.equal(Object.hasOwn(extracted.schema.$defs, definitionName), false);
    }

    // A module with several contracts compiles one schema holding every
    // definition they reach, so a shared definition is compiled once.
    const training = validatorModules(schema).find(({ validatorFilename }) =>
      validatorFilename === "api-contracts.training.validators.mjs");
    const trainingSchema = extractModuleContractSchema(schema, training);
    const trainingDefinitions = Object.keys(trainingSchema.$defs);
    assert.deepEqual(trainingDefinitions, [...trainingDefinitions].sort());
    assert.ok(trainingDefinitions.includes("TrainResponse"));
    assert.ok(trainingDefinitions.includes("TrainStatusResponse"));
    assert.equal(
      trainingDefinitions.length,
      new Set([
        ...Object.keys(extractContractSchema(schema, "TrainResponse").schema.$defs),
        ...Object.keys(extractContractSchema(schema, "TrainStatusResponse").schema.$defs),
        "TrainResponse",
        "TrainStatusResponse",
      ]).size,
    );
    assert.deepEqual(
      Object.keys(trainingSchema).sort(),
      ["$defs", "$id", "$schema"],
    );

    await cp(
      path.join(sourceDirectory, "api-contracts.schema.json"),
      path.join(temporaryDirectory, "api-contracts.schema.json"),
    );
    assert.equal(await run({ outputDirectory: temporaryDirectory }), true);
    assert.equal(await run({ check: true, outputDirectory: temporaryDirectory }), true);

    const modules = validatorModules(schema);
    const utility = modules.find(({ validatorFilename }) =>
      validatorFilename === "api-contracts.utility.validators.mjs");
    assert.deepEqual(
      utility?.validators.map(({ exportName }) => exportName),
      [
        "validateUtilityListResponse",
        "validateUtilityReadResponse",
        "validateUtilityWriteResponse",
        "validateUtilityDeleteResponse",
      ],
    );
    for (const module of modules) {
      const validators = await import(
        pathToFileURL(path.join(temporaryDirectory, module.validatorFilename)).href
      );
      for (const { exportName } of module.validators) {
        assert.equal(validators[exportName]({}), false);
        const [error] = validators[exportName].errors ?? [];
        assert.ok(error);
        if (module.allErrors) {
          assert.equal(typeof error.schemaPath, "string");
        } else {
          // An object root misses a property; a list root rejects the object.
          if (error.keyword === "required") {
            assert.equal(typeof error.params.missingProperty, "string");
          } else {
            assert.equal(error.keyword, "type");
          }
          assert.equal(Object.hasOwn(error, "schemaPath"), false);
        }
      }
      if (module.validatorFilename.includes("execution-strategy")) {
        assert.equal(validators.EXECUTION_STRATEGY_SCHEMA_VERSION, 1);
      }
    }
    const utilityValidators = await import(
      pathToFileURL(path.join(temporaryDirectory, "api-contracts.utility.validators.mjs")).href
    );
    assert.equal(
      utilityValidators.validateUtilityWriteResponse({
        status: "ok", name: "helpers", module: "helpers", import_line: "", error: null, error_line: null,
      }),
      true,
    );

    const declarations = await readFile(path.join(temporaryDirectory, GENERATED_TYPES_FILENAME), "utf8");
    // Models are closed; only a field the server declares as an open mapping
    // keeps an index signature, nested under that field.
    assert.doesNotMatch(declarations, /^export interface \w+ \{\n(?:  [^\n]*\n)*?  \[k: string\]: unknown;/m);
    assert.equal(declarations.includes("HauteApiContractRoots"), false);
    // Every serialized response field is required, defaults included.
    assert.match(
      declarations,
      /export interface UtilityWriteResponse \{\n  error: string \| null;\n  error_line: number \| null;\n  import_line: string;\n  module: string;\n  name: string;\n  status: string;\n\}/,
    );

    const artifacts = [
      GENERATED_TYPES_FILENAME,
      CONSTANTS_FILENAME,
      ...modules.flatMap(({ validatorFilename, declarationFilename }) => [
        validatorFilename,
        declarationFilename,
      ]),
    ];
    for (const artifact of artifacts) {
      const filename = path.join(temporaryDirectory, artifact);
      const original = await readFile(filename, "utf8");
      await writeFile(filename, `${original}// stale\n`, "utf8");
      const stale = await readFile(filename, "utf8");
      assert.equal(
        await run({ check: true, outputDirectory: temporaryDirectory, report: false }),
        false,
      );
      assert.equal(await readFile(filename, "utf8"), stale);
      await writeFile(filename, original, "utf8");
    }
  },
);

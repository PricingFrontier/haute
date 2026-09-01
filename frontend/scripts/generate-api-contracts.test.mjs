import assert from "node:assert/strict";
import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import {
  CONSTANTS_FILENAME,
  GENERATED_TYPES_FILENAME,
  VALIDATOR_ARTIFACTS,
  extractContractSchema,
  run,
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
    ]);
    for (const [definitionName, expectedDefinitions] of expectedClosures) {
      const extracted = extractContractSchema(schema, definitionName);
      assert.deepEqual(Object.keys(extracted.schema.$defs), expectedDefinitions);
      assert.equal(extracted.reachableDefinitionCount, expectedDefinitions.length + 1);
      assert.equal(Object.hasOwn(extracted.schema.$defs, definitionName), false);
    }

    await cp(
      path.join(sourceDirectory, "api-contracts.schema.json"),
      path.join(temporaryDirectory, "api-contracts.schema.json"),
    );
    assert.equal(await run({ outputDirectory: temporaryDirectory }), true);
    assert.equal(await run({ check: true, outputDirectory: temporaryDirectory }), true);

    for (const { exportName, validatorFilename } of VALIDATOR_ARTIFACTS) {
      const module = await import(
        pathToFileURL(path.join(temporaryDirectory, validatorFilename)).href
      );
      assert.equal(module[exportName]({}), false);
      const [error] = module[exportName].errors ?? [];
      assert.ok(error);
      if (exportName === "validateExecutionStrategyDiagnostic") {
        assert.equal(module.EXECUTION_STRATEGY_SCHEMA_VERSION, 1);
        assert.equal(error.keyword, "required");
        assert.equal(typeof error.params.missingProperty, "string");
        assert.equal(Object.hasOwn(error, "schemaPath"), false);
      } else {
        assert.equal(typeof error.schemaPath, "string");
      }
    }

    const artifacts = [
      GENERATED_TYPES_FILENAME,
      CONSTANTS_FILENAME,
      ...VALIDATOR_ARTIFACTS.flatMap(({ validatorFilename, declarationFilename }) => [
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

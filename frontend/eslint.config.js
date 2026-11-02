import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

// Job polling lives in src/hooks/jobPollingController.ts (the store-tracked
// controller and the awaited waitForJob), so browser source may not open its
// own unbounded loop or interval.
const POLL_MESSAGE = 'Wait for a job with waitForJob or JobPollingController (hooks/jobPollingController.ts).'
const POLLING_SELECTORS = [
  { selector: 'ForStatement[init=null][test=null][update=null]', message: POLL_MESSAGE },
  { selector: 'WhileStatement[test.type="Literal"][test.value=true]', message: POLL_MESSAGE },
  {
    selector: 'CallExpression[callee.name="setInterval"], CallExpression[callee.property.name="setInterval"]',
    message: POLL_MESSAGE,
  },
]
// One helper per repeated concern; each owning module is exempt from its own ban.
const ERROR_HELPER_NAMES = '/^(errorMessage|errorMsg|errorDetail|requestErrorDetail|previewErrorDetail|gitErrorMessage)$/'
const ERROR_TEXT_BASE_SELECTORS = [
  {
    // A variable of that name holding a string is fine; a function is a copy.
    selector: `FunctionDeclaration[id.name=${ERROR_HELPER_NAMES}], VariableDeclarator[id.name=${ERROR_HELPER_NAMES}][init.type=/^(ArrowFunctionExpression|FunctionExpression)$/]`,
    message: 'Use apiErrorMessage (api/errors.ts) for error text.',
  },
  {
    selector: 'LogicalExpression[left.property.name="detail"][right.property.name="message"]',
    message: 'Use apiErrorMessage (api/errors.ts) for error text.',
  },
]
const ERROR_TEXT_SELECTORS = [
  ...ERROR_TEXT_BASE_SELECTORS,
  {
    selector: 'ConditionalExpression[test.operator="instanceof"][test.right.name="Error"][consequent.property.name="message"]',
    message: 'Use apiErrorMessage (api/errors.ts) for error text.',
  },
]
const FORMAT_SELECTORS = [
  {
    selector: 'FunctionDeclaration[id.name=/^format(Bytes|Memory|Size)$/], VariableDeclarator[id.name=/^format(Bytes|Memory|Size)$/]',
    message: 'Use formatBytes or formatByteSize (utils/formatBytes.ts).',
  },
  {
    selector: 'FunctionDeclaration[id.name=/^format(Duration|Elapsed)$/], VariableDeclarator[id.name=/^format(Duration|Elapsed)$/]',
    message: 'Use formatDuration (utils/formatValue.ts).',
  },
  {
    selector: 'FunctionDeclaration[id.name=/^(formatFixed|formatNumber)$/], VariableDeclarator[id.name=/^(formatFixed|formatNumber)$/]',
    message: 'Use formatFixed or formatNumber (utils/formatValue.ts).',
  },
  {
    // A function, declared or assigned: how long ago is said by one rule.
    selector: 'FunctionDeclaration[id.name=/^(relativeTime|timeAgo|formatRelativeTime)$/], VariableDeclarator[id.name=/^(relativeTime|timeAgo|formatRelativeTime)$/][init.type=/^(ArrowFunctionExpression|FunctionExpression)$/]',
    message: 'Use relativeTime (utils/relativeTime.ts).',
  },
]
const GUARD_SELECTORS = [
  {
    selector: 'FunctionDeclaration[id.name=/^(isRecord|asRecord|isPlainRecord|isPlainObject)$/], VariableDeclarator[id.name=/^(isRecord|asRecord|isPlainRecord|isPlainObject)$/]',
    message: 'Use isPlainObject or expectPlainObject (types/guards.ts), or isObjectLiteral (utils/objectLiteral.ts) for a JSON object literal.',
  },
  {
    selector: 'FunctionDeclaration[id.name="isObjectLiteral"], VariableDeclarator[id.name="isObjectLiteral"]',
    message: 'Use isObjectLiteral (utils/objectLiteral.ts).',
  },
]
// One implementation each of canonical JSON, a YYYY-MM-DD check, the job-status
// vocabulary, and the locale numbers are shown in (the browser's).
const HELPER_SELECTORS = [
  {
    selector: 'FunctionDeclaration[id.name=/^(sortObjectKeys|sortKeys|canonical|canonicalize|stableValue|stableStringify|stableJson)$/], VariableDeclarator[id.name=/^(sortObjectKeys|sortKeys|canonical|canonicalize|stableValue|stableStringify|stableJson)$/][init.type=/^(ArrowFunctionExpression|FunctionExpression)$/]',
    message: 'Use canonicalJson or canonicalValue (utils/canonicalJson.ts).',
  },
  {
    selector: 'FunctionDeclaration[id.name=/^(isRealDate|isValidDate|isDay|isIsoDay|isValidDay)$/], VariableDeclarator[id.name=/^(isRealDate|isValidDate|isDay|isIsoDay|isValidDay)$/][init.type=/^(ArrowFunctionExpression|FunctionExpression)$/]',
    message: 'Use isDay, isTime or isDateTime (utils/isoDate.ts).',
  },
  {
    selector: 'ArrayExpression:has(> Literal[value="memory_limited"]):has(> Literal[value="contract_error"])',
    message: 'Use JOB_STATUS_VALUES, FAILED_JOB_STATUSES or TERMINAL_JOB_STATUSES (api/types.ts).',
  },
  {
    selector: 'CallExpression[callee.property.name=/^toLocale(String|DateString|TimeString)$/][arguments.0.type="Literal"], NewExpression[callee.object.name="Intl"][arguments.0.type="Literal"]',
    message: "Numbers and dates follow the browser's locale: pass undefined.",
  },
]
const SOURCE_FILES = ['src/**/*.{ts,tsx}']
const TEST_FILES = ['src/**/__tests__/**', 'src/**/*.test.{ts,tsx}']
function restrictedSyntax(...groups) {
  return { 'no-restricted-syntax': ['error', ...groups.flat()] }
}

export default defineConfig([
  globalIgnores(['dist', 'playwright-report', 'test-results']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      'no-useless-assignment': 'error',
      'preserve-caught-error': 'error',
      'react-hooks/refs': 'error',
      'react-hooks/set-state-in-effect': 'error',
      // Honour the leading-underscore "intentionally unused" convention
      // for both function args (e.g. `(_key) => ...` callbacks that
      // satisfy a typed interface but ignore the value) and locals
      // (e.g. `function _init(_arg?: unknown) { ... }` stub functions).
      '@typescript-eslint/no-unused-vars': [
        'error',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
        },
      ],
    },
  },
  {
    files: SOURCE_FILES,
    ignores: TEST_FILES,
    rules: restrictedSyntax(POLLING_SELECTORS, ERROR_TEXT_SELECTORS, FORMAT_SELECTORS, GUARD_SELECTORS, HELPER_SELECTORS),
  },
  {
    // The helpers' own homes.
    files: ['src/utils/isoDate.ts', 'src/api/types.ts'],
    rules: restrictedSyntax(POLLING_SELECTORS, ERROR_TEXT_SELECTORS, FORMAT_SELECTORS, GUARD_SELECTORS),
  },
  {
    files: ['src/api/errors.ts'],
    rules: restrictedSyntax(POLLING_SELECTORS, FORMAT_SELECTORS, GUARD_SELECTORS),
  },
  {
    files: ['src/utils/formatBytes.ts', 'src/utils/formatValue.ts', 'src/utils/relativeTime.ts'],
    rules: restrictedSyntax(POLLING_SELECTORS, ERROR_TEXT_SELECTORS, GUARD_SELECTORS),
  },
  {
    files: ['src/types/guards.ts', 'src/utils/objectLiteral.ts'],
    rules: restrictedSyntax(POLLING_SELECTORS, ERROR_TEXT_BASE_SELECTORS, FORMAT_SELECTORS),
  },
  // Exact file/rule debt that predates the blocking severities. New instances
  // elsewhere fail lint; remove each override with its owning-stream fix.
  {
    files: [
      'src/api/responseValidation.ts',
      'src/hooks/ensureInputSnapshots.ts',
      'src/hooks/useDataInputColumns.ts',
      'src/hooks/useGraphCommitController.ts',
      'src/hooks/useNodeDataProfile.ts',
      'src/hooks/usePipelineAPI.ts',
      'src/hooks/useStaleConfigEstimate.ts',
      'src/hooks/useSubmodelBoundaryEditing.ts',
      'src/hooks/useTracing.ts',
      'src/hooks/useWebSocketSync.ts',
      'src/panels/ModellingConfig.tsx',
      'src/panels/editors/ApiInputEditor.tsx',
      'src/panels/editors/ExploreChartsConfig.tsx',
      'src/panels/editors/FrameTableActions.tsx',
      'src/panels/editors/JsonPreview.tsx',
      'src/panels/editors/_ioFormats.ts',
      'src/panels/editors/_shared.tsx',
      'src/panels/editors/apiInputInherit.ts',
      'src/panels/editors/banding/BandingRulesGrid.tsx',
      'src/panels/editors/banding/BreakpointGrid.tsx',
      'src/panels/editors/explorePivots/FilterMemberPicker.tsx',
      'src/panels/editors/polarsSteps/useRenderedSteps.ts',
      'src/panels/editors/rating/TwoWayGrid.tsx',
      'src/panels/explore/ComboChart.tsx',
      'src/panels/explore/ExploreChartsPane.tsx',
      'src/panels/explore/ExploreTableActions.tsx',
      'src/panels/explore/chartConfig.ts',
      'src/panels/modelling/GpuTrainingToggle.tsx',
      'src/panels/modelling/HyperparametersConfig.tsx',
      'src/panels/useNodePanelSession.ts',
      'src/stores/useSettingsStore.ts',
      'src/utils/connectionValidation.ts',
      'src/workbench/ChartBody.tsx',
    ],
    rules: restrictedSyntax(POLLING_SELECTORS, ERROR_TEXT_BASE_SELECTORS, FORMAT_SELECTORS, GUARD_SELECTORS),
  },
  {
    files: ['e2e/persistence/api-input-v2-native.spec.ts'],
    rules: {
      'no-useless-assignment': 'warn',
    },
  },
  {
    files: ['src/types/guards.ts'],
    rules: {
      'preserve-caught-error': 'warn',
    },
  },
  {
    files: ['src/panels/GitPanel.tsx'],
    rules: {
      'react-hooks/refs': 'warn',
    },
  },
  {
    files: [
      'src/components/BranchManager.tsx',
      'src/components/RemotePushControl.tsx',
      'src/hooks/usePipelineAPI.ts',
      'src/hooks/useWebSocketSync.ts',
      'src/panels/editors/RatingStepEditor.tsx',
      'src/panels/GitPanel.tsx',
      'src/panels/NodePanel.tsx',
      'src/panels/OptimiserDataPreview.tsx',
      'src/panels/OptimiserPreview.tsx',
    ],
    rules: {
      'react-hooks/set-state-in-effect': 'warn',
    },
  },
])

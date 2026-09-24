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
const ERROR_HELPER_NAMES = '/^(errorMessage|errorMsg|errorDetail|requestErrorDetail|previewErrorDetail)$/'
const ERROR_TEXT_SELECTORS = [
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
const FORMAT_SELECTORS = [
  {
    selector: 'FunctionDeclaration[id.name=/^format(Bytes|Memory|Size)$/], VariableDeclarator[id.name=/^format(Bytes|Memory|Size)$/]',
    message: 'Use formatBytes or formatByteSize (utils/formatBytes.ts).',
  },
  {
    selector: 'FunctionDeclaration[id.name=/^format(Duration|Elapsed)$/], VariableDeclarator[id.name=/^format(Duration|Elapsed)$/]',
    message: 'Use formatDuration (utils/formatValue.ts).',
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
    rules: restrictedSyntax(POLLING_SELECTORS, ERROR_TEXT_SELECTORS, FORMAT_SELECTORS, GUARD_SELECTORS),
  },
  {
    files: ['src/api/errors.ts'],
    rules: restrictedSyntax(POLLING_SELECTORS, FORMAT_SELECTORS, GUARD_SELECTORS),
  },
  {
    files: ['src/utils/formatBytes.ts', 'src/utils/formatValue.ts'],
    rules: restrictedSyntax(POLLING_SELECTORS, ERROR_TEXT_SELECTORS, GUARD_SELECTORS),
  },
  {
    files: ['src/types/guards.ts', 'src/utils/objectLiteral.ts'],
    rules: restrictedSyntax(POLLING_SELECTORS, ERROR_TEXT_SELECTORS, FORMAT_SELECTORS),
  },
  // Exact file/rule debt that predates the blocking severities. New instances
  // elsewhere fail lint; remove each override with its owning-stream fix.
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
      'src/components/CacheFetchButton.tsx',
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

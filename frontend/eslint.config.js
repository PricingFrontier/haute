import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

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
  // Job polling lives in src/hooks/jobPollingController.ts (the store-tracked
  // controller and the awaited waitForJob), so browser source may not open its
  // own unbounded loop or interval.
  {
    files: ['src/**/*.{ts,tsx}'],
    ignores: ['src/**/__tests__/**', 'src/**/*.test.{ts,tsx}'],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector: 'ForStatement[init=null][test=null][update=null]',
          message: 'Wait for a job with waitForJob or JobPollingController (hooks/jobPollingController.ts).',
        },
        {
          selector: 'WhileStatement[test.type="Literal"][test.value=true]',
          message: 'Wait for a job with waitForJob or JobPollingController (hooks/jobPollingController.ts).',
        },
        {
          selector: 'CallExpression[callee.name="setInterval"], CallExpression[callee.property.name="setInterval"]',
          message: 'Wait for a job with waitForJob or JobPollingController (hooks/jobPollingController.ts).',
        },
      ],
    },
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
    // Its progress interval goes with the component or onto the shared
    // poller in CACHE-S08.
    files: ['src/components/CacheFetchButton.tsx'],
    rules: {
      'no-restricted-syntax': 'warn',
    },
  },
  {
    files: [
      'src/components/CacheFetchButton.tsx',
      'src/panels/GitPanel.tsx',
    ],
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

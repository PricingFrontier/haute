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
])

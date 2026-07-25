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
      // ESLint 10-compatible presets enable these rules by default. Keep them
      // visible but non-blocking during the supply-chain upgrade; promoting
      // them to errors requires a separate state/effect and error refactor.
      'no-useless-assignment': 'warn',
      'preserve-caught-error': 'warn',
      'react-hooks/refs': 'warn',
      'react-hooks/set-state-in-effect': 'warn',
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
])

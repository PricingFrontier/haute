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
// vocabulary, and the locale numbers are shown in (the browser's). Every
// override below keeps them; only their owners are exempt.
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
// haute-ui's component stylesheets sit in Tailwind's components layer, under the
// utilities, so a utility written beside a kit class wins: that is how a caller lays a
// kit element out. A colour utility would win too, over the kit's hover, pressed and
// disabled colours as well, so colour stays the kit's. `.toolbar-number-input` is
// index.css's own class, not the kit's. The check reads the source as written: it sees
// a kit class and a colour class in one string, one template, one class-name call or
// array, or one concatenation, and does not follow a class through a variable.
const KIT_CLASS = String.raw`/(^|\s)(toolbar(?!-number-input)|btn|palette|side-panel|panel-drag-handle)(-[a-z-]+)?(\s|$)/`
const PALETTE = 'slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose'
// One class that sets a colour, behind any variants and either spelling of important:
// a colour utility whose value is a token, a literal or named colour, a palette shade or
// a colour keyword (alone, or with an opacity); an arbitrary property that sets one; or
// one of index.css's own state classes, which are unlayered and so outrank the kit too.
const COLOUR_UTILITY = String.raw`/(^|\s)!?(\S*:)?!?((bg|text|border(-[xytrblse])?|outline|ring|ring-offset|inset-ring|divide|placeholder|shadow|inset-shadow|text-shadow|drop-shadow|fill|stroke|decoration|accent|caret|from|via|to)-(\[(color:|var\(|#|rgb|hsl|oklch|oklab|lab\(|lch\(|color-mix|(red|blue|green|white|black|gr[ae]y|yellow|orange|purple|pink|transparent|currentColor)\])|\(--|(inherit|current|transparent|black|white|(${PALETTE})-\d{2,3})(\s|$|!|\x2f))|\[(background|[a-z-]*color|fill|stroke):|(focus-ring|hover-[a-z-]+)(\s|$))/`
const KIT_COLOUR_MESSAGE = 'Write layout utilities beside a haute-ui class or on a haute-ui component, never a colour: the kit draws its own colours and states (specs/frontend-shared).'
const KIT_TEMPLATE = `TemplateLiteral:has(> TemplateElement[value.cooked=${KIT_CLASS}])`
const KIT_STRING = `:matches(Literal[value=${KIT_CLASS}], ${KIT_TEMPLATE})`
// A colour class in a string that does not itself name a kit class: one that does is
// reported by the first selector, once.
const COLOUR_STRING = `:matches(Literal[value=${COLOUR_UTILITY}]:not([value=${KIT_CLASS}]), TemplateLiteral:not(${KIT_TEMPLATE}):has(> TemplateElement[value.cooked=${COLOUR_UTILITY}]))`
// A string as one member of a class-name call or array, or one operand of a
// concatenation: bare, or up to two `wrapper`s down. Only members are looked at, so a
// kit class and a colour on different elements inside one call are left alone.
const within = (string, wrapper) => `:matches(${string}, ${wrapper}:has(> ${string}), ${wrapper}:has(> ${wrapper}:has(> ${string})))`
const CHOICE = ':matches(LogicalExpression, ConditionalExpression)'
const CONCATENATION = 'BinaryExpression[operator="+"]'
const KIT_MEMBER = within(KIT_STRING, CHOICE)
const COLOUR_MEMBER = within(COLOUR_STRING, CHOICE)
const CLASS_LIST = ':matches(CallExpression, ArrayExpression)'
const KIT_SELECTORS = [
  {
    selector: `Literal[value=${KIT_CLASS}][value=${COLOUR_UTILITY}], ${KIT_TEMPLATE}:has(:matches(TemplateElement[value.cooked=${COLOUR_UTILITY}], Literal[value=${COLOUR_UTILITY}]))`,
    message: KIT_COLOUR_MESSAGE,
  },
  {
    // classNames("toolbar-btn", danger && "bg-...") and [on && "toolbar-btn", "bg-..."].join(" "), in either order.
    selector: `${CLASS_LIST} > ${KIT_MEMBER} ~ ${COLOUR_MEMBER}, ${CLASS_LIST} > ${COLOUR_MEMBER} ~ ${KIT_MEMBER}`,
    message: KIT_COLOUR_MESSAGE,
  },
  {
    // "toolbar-btn " + width + (on ? "bg-..." : "")
    selector: `${CONCATENATION}:not(${CONCATENATION} > ${CONCATENATION}):has(> ${within(KIT_MEMBER, CONCATENATION)}):has(> ${within(COLOUR_MEMBER, CONCATENATION)})`,
    message: KIT_COLOUR_MESSAGE,
  },
  {
    selector: `JSXOpeningElement[name.name=/^(Button|ButtonLink|ToolbarButton|ToolbarColumn|ToolbarLink|ToolbarStatus)$/] > JSXAttribute[name.name="className"] :matches(Literal[value=${COLOUR_UTILITY}], TemplateElement[value.cooked=${COLOUR_UTILITY}])`,
    message: KIT_COLOUR_MESSAGE,
  },
]
// The kit's button classes (toolbar-btn, toolbar-fill, toolbar-action and btn, each with
// any suffix) are written only inside haute-ui. Elsewhere a button is a Button, a
// ButtonLink or a ToolbarButton, so its size and its states are decided once and a new
// button cannot start another copy of them. The names are refused in any string, a
// class name or not.
const KIT_BUTTON_CLASS = String.raw`/(^|\s)(toolbar-(btn|fill|action)|btn)(-[a-z0-9-]+)?(\s|$)/`
const KIT_BUTTON_SELECTORS = [
  {
    selector: `Literal[value=${KIT_BUTTON_CLASS}], TemplateElement[value.cooked=${KIT_BUTTON_CLASS}]`,
    message: "Draw a button with haute-ui's Button, ButtonLink, ToolbarButton or ToolbarLink, and a toolbar's status with ToolbarStatus: the kit's button classes are written only inside it (specs/frontend-shared).",
  },
]
const SOURCE_FILES = ['src/**/*.{ts,tsx}', 'catalogue/**/*.{ts,tsx}']
const TEST_FILES = ['src/**/__tests__/**', 'src/**/*.test.{ts,tsx}', 'catalogue/__tests__/**']
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
    rules: restrictedSyntax(POLLING_SELECTORS, ERROR_TEXT_SELECTORS, FORMAT_SELECTORS, GUARD_SELECTORS, HELPER_SELECTORS, KIT_SELECTORS, KIT_BUTTON_SELECTORS),
  },
  {
    // The helpers' own homes.
    files: ['src/utils/isoDate.ts', 'src/api/types.ts'],
    rules: restrictedSyntax(POLLING_SELECTORS, ERROR_TEXT_SELECTORS, FORMAT_SELECTORS, GUARD_SELECTORS, KIT_SELECTORS, KIT_BUTTON_SELECTORS),
  },
  {
    files: ['src/api/errors.ts'],
    rules: restrictedSyntax(POLLING_SELECTORS, FORMAT_SELECTORS, GUARD_SELECTORS, HELPER_SELECTORS, KIT_SELECTORS, KIT_BUTTON_SELECTORS),
  },
  {
    files: ['src/utils/formatBytes.ts', 'src/utils/formatValue.ts', 'src/utils/relativeTime.ts'],
    rules: restrictedSyntax(POLLING_SELECTORS, ERROR_TEXT_SELECTORS, GUARD_SELECTORS, HELPER_SELECTORS, KIT_SELECTORS, KIT_BUTTON_SELECTORS),
  },
  {
    files: ['src/types/guards.ts', 'src/utils/objectLiteral.ts'],
    rules: restrictedSyntax(POLLING_SELECTORS, ERROR_TEXT_BASE_SELECTORS, FORMAT_SELECTORS, HELPER_SELECTORS, KIT_SELECTORS, KIT_BUTTON_SELECTORS),
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
    rules: restrictedSyntax(POLLING_SELECTORS, ERROR_TEXT_BASE_SELECTORS, FORMAT_SELECTORS, GUARD_SELECTORS, HELPER_SELECTORS, KIT_SELECTORS, KIT_BUTTON_SELECTORS),
  },
  {
    // The kit writes its own classes.
    files: ['src/haute-ui/**'],
    ignores: TEST_FILES,
    rules: restrictedSyntax(POLLING_SELECTORS, ERROR_TEXT_SELECTORS, FORMAT_SELECTORS, GUARD_SELECTORS, HELPER_SELECTORS, KIT_SELECTORS),
  },
  {
    // Buttons that wrote the kit's surface class with sizes of their own before the
    // ban. Moving one onto the kit's button changes it by a pixel or two, so each
    // leaves this list with that choice.
    files: [
      'src/components/PipelineSettingsModal.tsx',
      'src/components/Toast.tsx',
      'src/workbench/WorkbenchView.tsx',
    ],
    rules: restrictedSyntax(POLLING_SELECTORS, ERROR_TEXT_SELECTORS, FORMAT_SELECTORS, GUARD_SELECTORS, HELPER_SELECTORS, KIT_SELECTORS),
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

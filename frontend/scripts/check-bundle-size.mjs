import { gzipSync } from "node:zlib"
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs"
import path from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const staticDir = path.resolve(__dirname, "../../src/haute/static")
const assetsDir = path.join(staticDir, "assets")
const indexHtmlPath = path.join(staticDir, "index.html")

// PivotCharts deliberately add a 195.6 KiB gzip ECharts core chunk beneath the
// lazy Charts pane. Pipeline load recovery adds ~10.5 KiB across the exact
// editor-document validator/adapter, recovery state, live-sync fences, and the
// minimal repair contract. The merged bundle is 1,309.4 KiB; 1,320 KiB keeps
// ~10 KiB headroom while the separate chart-vendor cap below prevents that
// dependency consuming it.
// The engineering-quality roadmap adds ~13.4 KiB across the generated runtime
// contract validators, server-owned editor identities, and extracted graph/job
// controllers. The merged bundle is 1,322.8 KiB; 1,333 KiB preserves the same
// ~10 KiB aggregate headroom without weakening the separate vendor caps.
// Direct node recovery replaced the draft apparatus: the recover action rides
// the existing repair dialog, the transient summary/scoped-save panel is small,
// and the draft dialog with its generated validators is deleted. The complete
// production bundle is 1,338.4 KiB; 1,349 KiB retains the same ~10 KiB
// aggregate headroom. Startup and vendor caps stay unchanged, and the repair
// dialog is explicitly prohibited from startup modulepreloads below.
// The modelling Export pane (model file save, MLflow export receipts, registered
// aliases, reload restore with training lineage) is almost entirely lazy panel
// code; only the small training-handle persistence rides the startup results
// store. The complete production bundle is 1,350.2 KiB; 1,361 KiB retains the
// same ~10 KiB aggregate headroom with the startup and vendor caps unchanged.
// The Polars step builder extends the existing lazy TransformEditor without a
// new dependency. The complete production bundle is 1,374.5 KiB; its ~24.3
// KiB increase from the 1,350.2 KiB baseline gets 1,385 KiB, restoring the
// established ~10 KiB aggregate headroom while the startup and vendor caps
// remain unchanged.
// The GLM terms pane (term and interaction editors mirroring the backend term
// contract, regularisation and solver controls, and the inference-aware result
// views) is lazy modelling-panel code with no new dependency. The complete
// production bundle is 1,388.8 KiB; its ~14.3 KiB increase from the 1,374.5 KiB
// baseline gets 1,399 KiB, restoring the ~10 KiB aggregate headroom with the
// startup and vendor caps unchanged.
// The shared node-output cache adds the data-point surface every consumer reads
// through — cache state and its buttons on the node panel, the banding and
// rating editors' own reads, and the execution-diagnostics indicator — almost
// all of it lazy panel code. The complete production bundle is 1,400.2 KiB;
// 1,410 KiB restores about 10 KiB of aggregate headroom.
// The modelling UI redesign (validation workspace, allocation preview, run
// summary, column selector and pane readiness) is lazy modelling-panel code and
// brings the complete production bundle to 1,412.8 KiB; 1,423 KiB restores
// about 10 KiB of aggregate headroom with the startup and vendor caps unchanged.
// Generated API response validators (API-R03) replace hand-written guards with
// lazy standalone Ajv modules, one per converted module group, loaded with the
// group's first response; the startup bundle shrinks (289.8 KiB). With the
// utility, Databricks, MLflow, modelling and git groups the complete production
// bundle is 1,431.7 KiB; 1,442 KiB restores about 10 KiB of aggregate headroom.
// The training group replaces the hand-written training guards (8.8 KiB) with
// its validators inside the lazy trainGuards chunk (26.9 KiB); a group compiles
// each shared definition once, but the train responses carry the evaluation,
// tuning and execution-metrics contracts. The complete production bundle is
// 1,453.2 KiB; 1,463 KiB restores about 10 KiB of aggregate headroom.
// The explore (pivot and profile) and factors (banding and rating) groups add
// lazy validator modules of 20.4 and 3.7 KiB; about 13 KiB of the explore one
// is the execution-metrics contract the training chunk also carries. The
// complete production bundle is 1,476.5 KiB; 1,487 KiB restores about 10 KiB.
// The io, session and editor groups fit inside that. The optimiser group adds
// a 21.1 KiB lazy module, about 13 KiB of it the execution-metrics contract
// again, while its hand guards leave the startup chunk (286.9 KiB). The
// complete production bundle is 1,500.5 KiB; 1,511 KiB restores about 10 KiB.
// Optimiser validation adds lazy weight only: the Adjustments, Segments and
// Quotes panes (OptimiserPreview 10.0 -> 22.8 KiB), the strict per-mode
// frontier point contracts (optimiser validators 21.6 -> 30.8 KiB) and the
// shared ResultsWorkspace chunk (6.1 KiB, ModellingPreview 16.2 -> 14.2 KiB).
// The complete production bundle is 1,536.4 KiB; 1,547 KiB restores about 10 KiB.
// Polars node clarity grows the lazy step editor chunk (SteppedCodePane
// 23.2 -> 32.9 KiB: column completion and model, the formula box, generated
// code highlighting and the add-step menu) and the entry by 1.2 KiB. The
// complete production bundle is 1,547.4 KiB; 1,558 KiB restores about 10 KiB.
// The strategy diagnostic's projection cause is part of the execution-metrics
// contract, so the four validator modules that carry it (execution, explore,
// training, optimiser) grow together, with the preview warning that names it.
// The complete production bundle is 1,558.7 KiB; 1,569 KiB restores about 10 KiB.
// The assistant rework adds lazy panel code (the build checklist, data-check
// findings on change cards, context chips, the empty state and readiness card),
// and main's connection drop menu adds its lazy menu. The complete production
// bundle is 1,571.1 KiB; 1,581 KiB restores about 10 KiB.
// Global constants add the lazy Constants pane (3.5 KiB), Constant operands
// and constant completion in the step editor (SteppedCodePane +1.1 KiB) and
// the eager core counted below. The complete production bundle is
// 1,586.9 KiB; 1,588 KiB restores about 1 KiB.
// Name collisions add the name-violations banner and revalidation, allocated
// node names, the shared rename validator and the naming context. The complete
// production bundle is 1,589.8 KiB; 1,591 KiB restores about 1.2 KiB.
// Model Scoring's Model file source adds its lazy editor option and file
// inspection panel and the generated ModelFileInspectionResponse validator.
// With both, the complete production bundle is 1,591.3 KiB; 1,593 KiB
// restores about 1.7 KiB.
// The t-boost family adds its lazy Tables result tab, the chart components it
// shares with the EBM Terms tab, and the tboost_tables response parser. The
// complete production bundle, with main's trace-card formulas, is 1,593.5 KiB; 1,594 KiB
// restores about 0.5 KiB.
// Main's bug-fix round (PR #293) and t-boost's validation-fit split pane bring
// it to 1,594.4 KiB; 1,595 KiB, approved, restores about 0.6 KiB.
// Showing EBM terms and t-boost tables as tables first, with a chart on request
// (the shared term view, level table and interaction lines), brings it to
// 1,595.2 KiB; 1,597 KiB, approved, restores about 1.8 KiB.
// The workbench view's lazy chunks (the view with its schema editor and the
// form's operations, 6.4 KiB; the form store; the workbench toolbar; and the
// workbench validators, grown by the form's schema) bring the complete
// production bundle to 1,613.9 KiB; 1,616 KiB restores about 2 KiB.
// The workbench's sheets (the canvas, the components' pictures, the sheet tabs, the
// properties panel and the component palette, all in the lazy view chunk, now
// 12.6 KiB, with the view store's own chunk) bring it to 1,621.4 KiB; 1,623 KiB
// restores about 1.6 KiB.
// The sample typed into the components and priced live (the cells, the pricing
// store and priceSample, all lazy) bring it to 1,623.9 KiB; 1,626 KiB restores
// about 2 KiB.
// Preview (the preview store, the sheet values and their rules, and the Table
// output rows matched by key, all lazy) brings it to 1,627.1 KiB; 1,629 KiB
// restores about 2 KiB.
// The Table's looks and the columns' display settings, then Show when (the
// conditions' rules, their editor in the properties panel and the sheets' eyes, all
// in the lazy workbench chunks) bring it to 1,636.2 KiB; 1,638 KiB restores about
// 1.8 KiB.
// The workbench's Chart (its data and options, its body and its panel section, in the
// lazy workbench chunks, and the pie the shared chart runtime registers for it, 7.1 KiB
// of the lazy vendor-charts chunk) brings it to 1,649.2 KiB; 1,651 KiB restores about
// 1.8 KiB.
// The workbench's Heading (its body, its panel and its options, in the lazy workbench
// chunks) and the sheet's reading order bring it to 1,651.0 KiB; 1,653 KiB restores about
// 2 KiB.
// The sheets' sections (their operations and rules, the section gutter, the section's
// panel, their bars and the palette's Section, and Preview held steady, in the lazy
// workbench chunks) bring it to 1,656.3 KiB; 1,657 KiB restores about 0.7 KiB.
// The test quotes kept in Preview (their panel and store, the API calls and their
// generated validators, all in the lazy workbench chunks) bring it to 1,661.1 KiB;
// 1,663 KiB restores about 1.9 KiB.
// Haute's quote_details table (its columns, the schema editor's lock on it and the cells
// the underwriter app sets) brings it to 1,663.1 KiB; 1,664 KiB restores about 0.9 KiB.
// The Workbench's panes, looks and colours (the Display box and the colour swatches it
// shares with Explore), its views priced as entered, the sheets' widths, the columns'
// defaults and groups, and a Table transposed or under its groups, all in the lazy
// workbench chunks with the initial bundle unchanged, bring it to 1,668.1 KiB; 1,670 KiB
// restores about 1.9 KiB.
// The Files panel (Pipeline files and Local files: the panel and its endpoints with their
// generated validators, in a lazy chunk; the toolbar buttons and the window's file-drop
// guard are all the initial bundle gains) brings it to 1,674.1 KiB; 1,676 KiB restores about
// 1.9 KiB.
// A column's allowed values read from a params file, and a many-row table given a row for
// each of them (the schema editor's Allowed and Rows and the rows read alike on the sheets,
// in the lazy workbench chunks) bring it to 1,676.3 KiB; 1,678 KiB restores about 1.7 KiB.
// The Workbench review's fixes (the in-page question dialog every confirm now goes
// through, a write's capture retried for what it skipped, one pricing context, the form
// following its file, adding and resizing components by keyboard, Tables of outputs
// alone, and the previews of several tables in one run) bring it to 1,681.0 KiB; 1,683 KiB
// restores about 2 KiB.
// A Table's look set as a spreadsheet's (its text, header, lines, frame and each column's
// own look, the panel's Table look and the form's validators for it, in the lazy workbench
// chunks) brings it to 1,685.4 KiB; 1,687 KiB restores about 1.6 KiB.
// Polars 2 in the step editor (the casts a column's type accepts and why another is
// refused, text parsed into dates with a format, membership values typed by the kind of
// number their column holds, a failing step located by its line with a remedy) and the
// row order every join states bring it to 1,688.8 KiB; 1,690 KiB restores about 1.2 KiB.
// A Collection's look set as a Table's (its text, labels, boxes, frame and each field's own
// look), labels aligned apart from values, a Table's row menu, and Show when's groups, "at
// least" a number, conditions on a list's rows and a field's own (the editor, the rules and
// the form's validators for them, all in the lazy workbench chunks, the initial ones as they
// were) bring it to 1,699.6 KiB; 1,701 KiB restores about 1.4 KiB.
// The Databricks Data Output (its warehouse picker, serverless tagged, and target-table
// fields in the lazy editor chunks, the initial ones as they were) brings it to
// 1,701.8 KiB; 1,703 KiB restores about 1.2 KiB.
// The step menus' redesign (the Add step chooser's entries and ranked search, the grouped
// Computed as list with its drafts, and the calculations' icons, in the lazy step editor
// chunk) brings it to 1,705.0 KiB; 1,707 KiB restores about 2 KiB.
// The git restore work (restore and start-a-branch, the deleted branches, Files in use,
// the naming step kept on screen with its refusal, and the Git panel beside a pipeline
// the version lacks; GitPanel 18.0 -> 18.2 KiB, the editor's index 169.7 -> 169.9 KiB)
// brings it to 1,707.1 KiB; 1,709 KiB restores about 2 KiB.
const DEFAULT_MAX_TOTAL_JS_GZIP_KIB = 1709
const DEFAULT_MAX_SINGLE_JS_GZIP_KIB = 650
const DEFAULT_MAX_CHART_VENDOR_JS_GZIP_KIB = 205
// Initial JS is ~240 KiB gzip after the version-control feature merged in. All
// on-demand VC surfaces (comparison view, git panel, the VC modals) are
// React.lazy code-split, so the remaining weight is genuine non-splittable
// initial-path growth (VC api/types/guards/store + multi-frame core). The
// data-io feature added ~0.7 KiB of the same class (fetchIoFormats client
// function + its runtime guard; the editors themselves are lazy chunks).
// The Explore NaN split added ~0.5 KiB of the same class again (nan_count in
// types/guards, the NaN %/NaN columns, and the shared Tooltip + HelpCircle
// icon newly reaching the initial chunk via the Distinct help button).
// The assistant feature added ~0.3 KiB of the same deliberate-eager class: the
// always-visible Toolbar toggle (Bot icon + button + useUIStore selectors)
// and client.ts's postRawStream helper. The panel, store, api module, and
// markdown renderer are all lazy (guarded by App.assistantLazy.test.ts).
// Git readiness hardening added ~0.3 KiB for the always-visible six-state
// indicator, retryable error state, and in-flight status de-duplication. The
// branch-list request coordinator remains lazy.
// Edge Join compatible-target feedback adds ~0.7 KiB of deliberate eager code:
// the always-mounted canvas must validate and announce candidates synchronously
// during a connection gesture. The merged initial bundle is 246.2 KiB; 247 KiB
// retained sub-KiB headroom while still catching accidental eager editor/vendor
// imports. Editable submodel boundaries add ~1.5 KiB of deliberate eager code:
// the always-mounted canvas projects available and mapped inputs, reconciles
// undo/redo state, and commits boundary connection/deletion gestures
// synchronously. The merged initial bundle is 247.7 KiB; 249 KiB retains about
// 1 KiB of headroom without weakening the lazy-editor/vendor checks below.
// Reusable submodel instances add another ~3.3 KiB of deliberate eager core:
// occurrence-aware navigation, runtime targeting, read-only copies, and shared
// boundary edits must all be available on the mounted canvas. The merged initial
// bundle is 251.3 KiB; 253 KiB retains about 1.7 KiB of headroom.
// Hosted durable storage adds ~1.9 KiB of deliberate eager code: the toolbar
// must state at first paint whether the session's work is being stored, how far
// behind publication is, and whether this project is a fork — a chip that
// appeared late would leave the user believing unsaved work was safe. Its
// dialogs (bind, upstream sync, identity) are all lazy. The merged initial
// bundle is 253.1 KiB; 255 KiB restores about 1.9 KiB of headroom.
// Explore Pivot/PivotChart contracts, retained-result state, and pane dispatch
// bring the merged startup bundle to 256.4 KiB without importing the chart
// runtime. 258 KiB preserves a narrow ~1.6 KiB regression tripwire.
// Excel-parity chart formatting adds ~1.6 KiB of deliberate eager state
// contracts: per-node preview/editor pane alignment and configured-card
// tracking in useUIStore, plus the pivot auto-claim registry in
// useNodeResultsStore that keeps chart sources refreshing atomically. The
// chart gallery, formatting editors, and data adapters all stay in lazy
// chunks. The merged initial bundle is 258.0 KiB; 260 KiB restores about
// 2 KiB of headroom.
// Pipeline load recovery adds ~10.5 KiB of deliberate eager core: every load
// and resync must validate and adapt the editor document, publish diagnostics
// and capabilities atomically, and preserve a truthful degraded canvas. The
// repair dialog itself remains small and the chart/editor vendors remain lazy.
// The merged initial bundle is 268.5 KiB; 271 KiB retains ~2.5 KiB headroom.
// Generated structural validation for execution diagnostics, server-owned
// editor identity resolution, and synchronous graph-commit coordination add
// ~8.2 KiB of deliberate eager core. The Explore validator and editor surfaces
// remain lazy. The merged initial bundle is 276.7 KiB; 279 KiB retains ~2.3 KiB
// headroom while continuing to catch accidental eager imports.
// Structural staleness fingerprints and deferred shared-node deletion cleanup
// for the boundary-editing hook add ~2.3 KiB of deliberate eager core: the
// hook runs on the mounted canvas, so the fingerprint and the settle callbacks
// cannot be lazy. The merged initial bundle is 279.0 KiB; 281 KiB retains ~2 KiB
// headroom.
// The collapsed submodel input socket adds ~2.1 KiB of deliberate eager core:
// resolving an incoming frame to a canonical public port, retiring one across
// every occurrence, and projecting the parent bindings and their order so
// history can restore them all run on the mounted canvas. That consumed the
// whole 2 KiB allowance, leaving the ceiling no room to catch the accidental
// eager import it exists for. The merged initial bundle is 281.1 KiB; 283 KiB
// restores about 2 KiB of headroom.
// Reorganizing the toolbar into stacked control groups and surfacing full Git
// error diagnostics with an accessible Tooltip adds ~2.0 KiB of deliberate eager
// UI core to the always-mounted toolbar. The merged initial bundle is 283.1 KiB;
// 285 KiB restores ~1.9 KiB of headroom while keeping startup caps tight.
// The shared node-output cache puts a consumer's data-point state on the always
// mounted node panel — its cache button and availability, and the diagnostics
// indicator that names a refused capture — which is eager by design: it reports
// on data the panel is already showing. The lazy-only preload guard above still
// passes, so nothing was pulled forward by accident. The merged initial bundle
// is 290.2 KiB; 292 KiB restores ~1.8 KiB of headroom on the same terms.
// The modelling validation focus view adds ~0.6 KiB to shared eager components:
// ModalShell's inline (inactive) mode and closed-disclosure focus trap, the
// preview frame's focus and remembered-height options, and the results tab
// appearance. The modelling panes themselves stay lazy. The merged initial
// bundle is 292.3 KiB; 294 KiB restores ~1.7 KiB of headroom.
// The modelling-issues fixes add eager code that cannot wait for a lazy chunk:
// axis ticks formatted together (formatChartTicks, used by the initial
// histogram's value grid), chunk-load recovery in the root error boundary
// (it handles a lazy chunk that failed to load), the expired training-result
// record in the results store, the training poller's progress key and the
// panel's openNode wiring. The expired-result panel itself stays lazy. The
// merged initial bundle is 294.2 KiB; 296 KiB restores ~1.8 KiB of headroom.
// The trace panel (every step card, detail and derivation tree, ~20 KiB) now
// loads lazily, fetched when a trace request starts; the always-needed request
// surface (TraceStatePanel), trace store state and canvas projection stay eager.
// The merged initial bundle is 279.5 KiB; 281 KiB keeps ~1.5 KiB of headroom.
// Later work brought the initial bundle to 281.6 KiB. The assistant's
// running-turn chrome then adds 0.9 KiB of deliberate eager code, because it
// acts while the panel chunk is not loaded: the canvas pill that stops a turn,
// the toolbar's progress and unseen-outcome states, the data preview's "Ask
// the assistant to fix" action and their UI-store mirror. The panel, its store
// and its API module stay lazy (App.assistantLazy.test.ts). The merged initial
// bundle is 282.5 KiB; 284 KiB restores ~1.5 KiB of headroom.
// The connection drop menu adds ~0.6 KiB of deliberate eager core: creating the
// chosen node and its edge runs in the canvas edge handlers on release, beside
// the empty-canvas hit-test and the menu's type list. The menu itself stays
// lazy. With both, the merged initial bundle is 283.8 KiB; 285 KiB restores
// ~1.2 KiB of headroom.
// Global constants add ~1.7 KiB of deliberate eager core: every build of an
// execution request carries the valid constants (utils/globalConstants.ts),
// save refuses invalid drafts, the store holds the drafts and the document
// parser reads them. The pane's editing helpers and the reads analysis stay
// lazy (utils/globalConstantsEditing.ts). The merged initial bundle is
// 285.7 KiB; 287 KiB restores ~1.3 KiB of headroom.
// Name collisions add eager core: the document's name violations (parser,
// status store fence, banner, revalidation hook) and the naming context every
// node creation and rename sends. The merged initial bundle is 287.6 KiB;
// 289 KiB restores ~1.4 KiB of headroom.
// The workbench view adds ~3.8 KiB of deliberate eager code: the view switcher
// at the bottom of the palette, the host in App that hides and fences the
// pipeline editor while the view shows, the status store's active view, the
// form routes' client functions and the keystroke-target helpers both shortcut
// hooks share. The view, its toolbar, the schema editor, the form store and
// the form's generated validator stay lazy (App.workbenchLazy.test.ts). The
// merged initial bundle is 291.4 KiB; 293 KiB restores ~1.6 KiB of headroom.
// The workbench's Heading adds four of lucide's icons (its palette mark and its three
// alignment toggles), which land in the eager vendor-ui chunk as every lucide icon does;
// the Heading itself stays in the lazy workbench chunks. The merged initial bundle is
// 293.0 KiB; 295 KiB restores ~2 KiB of headroom.
// The Workbench review's fixes add ~2.1 KiB of deliberate eager code: the question store
// that replaces every window.confirm, the git store's capture of what a write skipped
// (`uncaptured`, `POST /api/git/capture`), the WebSocket's count of form-file changes
// (`formChanges`) and the preview parser's `port_previews`. Nothing lazy became eager
// (the modulepreload check below). The merged initial bundle is 296.3 KiB; 298 KiB
// restores ~1.7 KiB of headroom.
// The step menus give each Add column calculation an icon (Calculator, Split, SquareSigma,
// FoldVertical, Divide, Link2, Equal, Hash, CaseSensitive, CalendarDays), which land in
// the eager vendor-ui chunk as every lucide icon does; the step editor itself stays lazy.
// The merged initial bundle is 298.6 KiB; 300 KiB restores ~1.4 KiB of headroom.
const DEFAULT_MAX_INITIAL_JS_GZIP_KIB = 300

// Chunks that should only be fetched when their preview or editor is needed.
// If one appears as a startup modulepreload, the app has likely
// reintroduced an eager import path even if the initial gzip budget still fits.
export const LAZY_ONLY_MODULEPRELOAD_CHUNK_PREFIXES = [
  "ensureInputSnapshots",
  "WorkbenchView",
  "WorkbenchToolbar",
  "TracePanel",
  "ModellingPreview",
  "PipelineRepairDialog",
  "CodeMirrorEditor",
  "UtilityPanel",
  "vendor-codemirror",
  "vendor-layout",
  "vendor-charts",
  "chartRuntime",
  "ExploreChartsPane",
  "TransformEditor",
  "ModelScoreEditor",
  "BandingEditor",
  "RatingStepEditor",
  "OutputEditor",
  "ExternalFileEditor",
  "ApiInputEditor",
  "LiveSwitchEditor",
  "ScenarioExpanderEditor",
  "OptimiserApplyEditor",
  "ConstantEditor",
  "SubmodelEditor",
  "ColumnsTab",
  "ModellingConfig",
  "OptimiserConfig",
  "_shared",
  "useMlflowBrowser",
  "useStaleConfigEstimate",
  "ColumnTable",
  "ToggleButtonGroup",
  "EditorLabel",
  "banding",
  "trainGuards",
]

function fail(message) {
  console.error(message)
  process.exitCode = 1
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error)
}

function errorCode(error) {
  return error && typeof error === "object" && "code" in error ? error.code : undefined
}

export function formatBundleAssetReadError(
  error,
  {
    staticDir: staticDirPath = staticDir,
    assetsDir: assetsDirPath = assetsDir,
    indexHtmlPath: indexHtmlFilePath = indexHtmlPath,
  } = {},
) {
  const message = errorMessage(error)
  if (errorCode(error) === "ENOENT") {
    if (!existsSync(staticDirPath)) {
      return `Bundle output directory not found at ${staticDirPath}. Run 'npm run build' first.`
    }
    if (!existsSync(assetsDirPath)) {
      return `Bundle assets directory not found at ${assetsDirPath}. Run 'npm run build' first.`
    }
    if (!existsSync(indexHtmlFilePath)) {
      return `Bundle index HTML not found at ${indexHtmlFilePath}. Run 'npm run build' first.`
    }
    return `Bundle file referenced by the bundle check was not found under ${staticDirPath}: ${message}`
  }

  return `Failed to read bundle assets under ${staticDirPath}: ${message}`
}

export function formatKiB(bytes) {
  return `${(bytes / 1024).toFixed(1)} KiB`
}

function parseBudgetKiB(envName, defaultValue) {
  const raw = process.env[envName]
  if (raw == null || raw === "") return defaultValue
  const parsed = Number(raw)
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${envName} must be a positive number of KiB, got ${JSON.stringify(raw)}.`)
  }
  return parsed
}

function readAttributeMap(tag) {
  const attrs = new Map()
  for (const match of tag.matchAll(/([\w:-]+)\s*=\s*(["'])(.*?)\2/g)) {
    attrs.set(match[1].toLowerCase(), match[3])
  }
  return attrs
}

function assetNameFromReference(reference) {
  const withoutQuery = reference.split(/[?#]/, 1)[0]
  const assetMarker = "/assets/"
  const assetIndex = withoutQuery.lastIndexOf(assetMarker)
  if (assetIndex >= 0) return withoutQuery.slice(assetIndex + assetMarker.length)
  if (withoutQuery.startsWith("assets/")) return withoutQuery.slice("assets/".length)
  return path.posix.basename(withoutQuery)
}

function pushUnique(array, value) {
  if (!array.includes(value)) array.push(value)
}

export function parseInitialJsAssetNames(html) {
  const initial = []

  for (const match of html.matchAll(/<script\b[^>]*>/gi)) {
    const attrs = readAttributeMap(match[0])
    if (attrs.get("type") !== "module") continue
    const src = attrs.get("src")
    if (src && assetNameFromReference(src).endsWith(".js")) {
      pushUnique(initial, assetNameFromReference(src))
    }
  }

  for (const match of html.matchAll(/<link\b[^>]*>/gi)) {
    const attrs = readAttributeMap(match[0])
    if (attrs.get("rel") !== "modulepreload") continue
    const href = attrs.get("href")
    if (href && assetNameFromReference(href).endsWith(".js")) {
      pushUnique(initial, assetNameFromReference(href))
    }
  }

  return initial
}

export function parseModulepreloadJsAssetNames(html) {
  const preloads = []

  for (const match of html.matchAll(/<link\b[^>]*>/gi)) {
    const attrs = readAttributeMap(match[0])
    if (attrs.get("rel") !== "modulepreload") continue
    const href = attrs.get("href")
    if (href && assetNameFromReference(href).endsWith(".js")) {
      pushUnique(preloads, assetNameFromReference(href))
    }
  }

  return preloads
}

function isLazyOnlyModulepreloadChunk(assetName) {
  return LAZY_ONLY_MODULEPRELOAD_CHUNK_PREFIXES.some((prefix) =>
    assetName === `${prefix}.js` || assetName.startsWith(`${prefix}-`),
  )
}

export function collectJsAssets(directory) {
  return readdirSync(directory)
    .filter((name) => name.endsWith(".js"))
    .map((name) => {
      const filePath = path.join(directory, name)
      const content = readFileSync(filePath)
      return {
        name,
        rawBytes: statSync(filePath).size,
        gzipBytes: gzipSync(content).length,
      }
    })
    .sort((a, b) => b.gzipBytes - a.gzipBytes)
}

/**
 * @param {{
 *   html: string,
 *   jsAssets: Array<{ name: string, rawBytes: number, gzipBytes: number }>,
 *   budgetsKiB: {
 *     maxInitialJsGzipKiB: number,
 *     maxTotalJsGzipKiB: number,
 *     maxSingleJsGzipKiB: number,
 *     maxChartVendorJsGzipKiB?: number,
 *   },
 * }} input
 */
export function evaluateBundleBudgets({
  html,
  jsAssets,
  budgetsKiB,
}) {
  if (jsAssets.length === 0) {
    throw new Error("No JavaScript assets found.")
  }

  const initialAssetNames = parseInitialJsAssetNames(html)
  if (initialAssetNames.length === 0) {
    throw new Error("No initial JavaScript assets found in index.html.")
  }

  const assetByName = new Map(jsAssets.map((asset) => [asset.name, asset]))
  const initialAssets = initialAssetNames.map((name) => {
    const asset = assetByName.get(name)
    if (!asset) {
      throw new Error(`Initial JS asset "${name}" referenced by index.html was not found.`)
    }
    return asset
  })

  const totalGzipBytes = jsAssets.reduce((sum, asset) => sum + asset.gzipBytes, 0)
  const initialGzipBytes = initialAssets.reduce((sum, asset) => sum + asset.gzipBytes, 0)
  const largest = jsAssets[0]
  const chartVendorAsset = jsAssets.find(
    (asset) => asset.name === "vendor-charts.js" || asset.name.startsWith("vendor-charts-"),
  )
  const failures = []

  for (const assetName of parseModulepreloadJsAssetNames(html)) {
    if (isLazyOnlyModulepreloadChunk(assetName)) {
      failures.push(
        `Lazy-only JS chunk "${assetName}" must not be modulepreloaded by index.html.`,
      )
    }
  }

  if (initialGzipBytes > budgetsKiB.maxInitialJsGzipKiB * 1024) {
    failures.push(
      `Initial JS gzip size ${formatKiB(initialGzipBytes)} exceeds budget ` +
        `${budgetsKiB.maxInitialJsGzipKiB} KiB.`,
    )
  }

  if (totalGzipBytes > budgetsKiB.maxTotalJsGzipKiB * 1024) {
    failures.push(
      `Total JS gzip size ${formatKiB(totalGzipBytes)} exceeds budget ` +
        `${budgetsKiB.maxTotalJsGzipKiB} KiB.`,
    )
  }

  if (largest.gzipBytes > budgetsKiB.maxSingleJsGzipKiB * 1024) {
    failures.push(
      `Largest JS chunk ${largest.name} is ${formatKiB(largest.gzipBytes)}, ` +
        `above budget ${budgetsKiB.maxSingleJsGzipKiB} KiB.`,
    )
  }

  if (
    chartVendorAsset &&
    budgetsKiB.maxChartVendorJsGzipKiB != null &&
    chartVendorAsset.gzipBytes > budgetsKiB.maxChartVendorJsGzipKiB * 1024
  ) {
    failures.push(
      `Chart vendor JS chunk ${chartVendorAsset.name} is ${formatKiB(chartVendorAsset.gzipBytes)}, ` +
        `above budget ${budgetsKiB.maxChartVendorJsGzipKiB} KiB.`,
    )
  }

  return {
    failures,
    initialAssets,
    initialGzipBytes,
    chartVendorAsset,
    jsAssets,
    largest,
    totalGzipBytes,
  }
}

function run() {
  const budgetsKiB = {
    maxInitialJsGzipKiB: parseBudgetKiB(
      "HAUTE_BUNDLE_MAX_INITIAL_GZIP_KIB",
      DEFAULT_MAX_INITIAL_JS_GZIP_KIB,
    ),
    maxTotalJsGzipKiB: parseBudgetKiB(
      "HAUTE_BUNDLE_MAX_TOTAL_GZIP_KIB",
      DEFAULT_MAX_TOTAL_JS_GZIP_KIB,
    ),
    maxSingleJsGzipKiB: parseBudgetKiB(
      "HAUTE_BUNDLE_MAX_SINGLE_GZIP_KIB",
      DEFAULT_MAX_SINGLE_JS_GZIP_KIB,
    ),
    maxChartVendorJsGzipKiB: parseBudgetKiB(
      "HAUTE_BUNDLE_MAX_CHART_VENDOR_GZIP_KIB",
      DEFAULT_MAX_CHART_VENDOR_JS_GZIP_KIB,
    ),
  }

  let jsAssets
  let html
  try {
    jsAssets = collectJsAssets(assetsDir)
    html = readFileSync(indexHtmlPath, "utf8")
  } catch (error) {
    fail(formatBundleAssetReadError(error))
    return
  }

  let result
  try {
    result = evaluateBundleBudgets({ html, jsAssets, budgetsKiB })
  } catch (error) {
    fail(error instanceof Error ? error.message : String(error))
    return
  }

  console.log("JavaScript bundle gzip sizes:")
  for (const asset of result.jsAssets) {
    console.log(`  ${asset.name}: ${formatKiB(asset.gzipBytes)} gzip (${formatKiB(asset.rawBytes)} raw)`)
  }
  console.log(`  total: ${formatKiB(result.totalGzipBytes)} gzip`)
  if (result.chartVendorAsset) {
    console.log(
      `  chart vendor: ${formatKiB(result.chartVendorAsset.gzipBytes)} gzip ` +
        `(budget ${budgetsKiB.maxChartVendorJsGzipKiB} KiB)`,
    )
  }
  console.log("")
  console.log("Initial JavaScript gzip sizes:")
  for (const asset of result.initialAssets) {
    console.log(`  ${asset.name}: ${formatKiB(asset.gzipBytes)} gzip`)
  }
  console.log(
    `  initial total: ${formatKiB(result.initialGzipBytes)} gzip ` +
      `(budget ${budgetsKiB.maxInitialJsGzipKiB} KiB)`,
  )

  for (const message of result.failures) {
    fail(message)
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  run()
}

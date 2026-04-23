# GLM Integration Design — RustyStats in Haute

## 1. Overview

Add GLM (Generalised Linear Model) training to Haute's modelling pipeline using [RustyStats](https://github.com/PricingFrontier/rustystats) as the fitting engine. Users can train GLMs alongside CatBoost GBMs from the same training node UI.

### Key Decisions (from brainstorm)

| Decision | Choice | Rationale |
|---|---|---|
| **Factor config UX** | Visual builder + JSON textarea, bidirectional sync | Matches Haute's code-GUI philosophy. Atelier generates dict JSON for paste-in; visual toggles for quick models |
| **Family/link** | Replaces task/loss radio when GLM selected | Pricing analysts think in families (Poisson, Gamma), not tasks |
| **Regularization** | Dedicated UI section (None/Ridge/Lasso/Elastic Net) | First-class GLM concept, not an obscure param |
| **Results panel** | Algorithm-aware (coefficients/relativities for GLM) | Coefficient table is THE output for GLM users |
| **Serialization** | Native RustyStats binary only (`.rsglm`) | RustyStats installed anyway; simpler, safer, actual trained model deployed |
| **MLflow** | Pyfunc wrapper around native binary | One serialization format = thin wrapper is sufficient |
| **Interactions** | Simple 2-factor UI + full support via JSON | Visual: two dropdowns + checkbox per row; complex interactions via JSON paste |
| **Data exploration** | Not in Haute (Atelier's job) | Haute only needs fit diagnostics to verify model quality |
| **Complement/credibility** | Deferred entirely | Advanced feature, no UI needed yet |

---

## 2. Architecture

```
Atelier (interactive GLM workbench)        Haute (pipeline engine)
─────────────────────────────────          ────────────────────────
Iterative GLM development          ──►     Paste dict API JSON into config
  │                                              │
  │                                        Train from dict spec
  │                                              │
Export dict API JSON                       RustyStats fits GLM
  │                                              │
  └──────────────────────────────────────► Save .rsglm model file
                                                 │
                                           MODEL_SCORE node loads .rsglm
                                           (via ScoringModel wrapper)
```

- **Atelier** — standalone interactive GLM workbench (separate tool)
- **Haute** — trains GLMs from a stored dict spec, scores with fitted models
- **No MLflow requirement** for basic workflow — model saved to `models/` as `.rsglm`
- **MLflow optional** — log experiment + register model when `mlflow_experiment` is set

---

## 3. Config JSON Schema

The GLM training node stores its config in `config/model_training/<name>.json`, same as CatBoost. The `algorithm` field determines which config keys are relevant.

### 3.1 GLM Config Example

```json
{
  "algorithm": "glm",
  "target": "claim_count",
  "weight": "exposure",
  "offset": "log_exposure",
  "exclude": ["quote_id", "policy_id"],
  "family": "poisson",
  "link": null,
  "var_power": 1.5,
  "intercept": true,
  "terms": {
    "driver_age": { "type": "bs", "df": 5, "monotonicity": "decreasing" },
    "vehicle_age": { "type": "linear", "monotonicity": "increasing" },
    "area": { "type": "categorical" },
    "region": { "type": "categorical" },
    "brand": { "type": "target_encoding", "prior_weight": 1.0 },
    "engine_size": { "type": "linear" }
  },
  "interactions": [
    {
      "factors": ["driver_age", "region"],
      "include_main": true
    }
  ],
  "regularization": null,
  "alpha": 0.0,
  "l1_ratio": 0.0,
  "split": {
    "strategy": "random",
    "validation_size": 0.2,
    "holdout_size": 0.1,
    "seed": 42
  },
  "metrics": ["gini", "poisson_deviance"],
  "row_limit": null,
  "mlflow_experiment": null,
  "model_name": null
}
```

### 3.2 Key Differences from CatBoost Config

| Field | CatBoost | GLM |
|---|---|---|
| `algorithm` | `"catboost"` | `"glm"` |
| `task` | `"regression"` / `"classification"` | Not used — implied by `family` |
| `loss_function` | `"RMSE"` / `"Poisson"` / etc. | Not used — `family` determines loss |
| `params` | `{iterations, depth, ...}` | Not used — GLM has dedicated fields |
| `family` | N/A | `"poisson"` / `"gamma"` / `"tweedie"` / etc. |
| `link` | N/A | `null` (canonical) or explicit override |
| `var_power` | N/A | Tweedie variance power (1.0–2.0) |
| `terms` | N/A | Dict API spec: `{feature: {type, ...params}}` |
| `interactions` | N/A | `[{factors: [...], include_main: bool}]` |
| `regularization` | N/A | `null` / `"ridge"` / `"lasso"` / `"elastic_net"` |
| `intercept` | N/A | `true` / `false` (default `true`) |

### 3.3 Supported Families

| Family | Canonical Link | Typical Use |
|---|---|---|
| `gaussian` | `identity` | Linear regression |
| `poisson` | `log` | Claim frequency |
| `gamma` | `log` | Claim severity |
| `tweedie` | `log` | Pure premium (`var_power` configurable) |
| `binomial` | `logit` | Binary outcomes |
| `quasipoisson` | `log` | Overdispersed counts |
| `quasibinomial` | `logit` | Overdispersed binary |
| `negbinomial` | `log` | Overdispersed counts (theta auto-estimated) |

### 3.4 Supported Term Types

| Type | Description | Key Params |
|---|---|---|
| `linear` | Raw continuous variable | `monotonicity` |
| `categorical` | Dummy encoding | `levels` (optional) |
| `bs` | B-spline (flexible smoothing) | `df`, `k`, `degree`, `monotonicity` |
| `ns` | Natural spline (linear extrapolation) | `df`, `k`, `degree`, `monotonicity` |
| `target_encoding` | Ordered target encoding | `prior_weight` |
| `expression` | Computed feature | `expr`, `monotonicity` |

### 3.5 Interaction Format

Stored in config:
```json
{
  "interactions": [
    { "factors": ["driver_age", "region"], "include_main": true },
    { "factors": ["vehicle_age", "brand"], "include_main": false }
  ]
}
```

Translated to RustyStats format at fit time:
```python
# Each interaction uses the term type from the main terms dict
interactions = [
    {
        "driver_age": terms["driver_age"],  # inherits type from terms
        "region": terms["region"],
        "include_main": True,
    },
]
```

---

## 4. Backend Implementation

### 4.1 `GLMAlgorithm(BaseAlgorithm)` — `src/haute/modelling/_rustystats.py`

New module implementing the `BaseAlgorithm` interface.

```python
class GLMAlgorithm(BaseAlgorithm):
    """RustyStats GLM implementation."""

    def fit(
        self,
        train_df: pl.DataFrame,
        features: list[str],
        cat_features: list[str],
        target: str,
        weight: str | None,
        params: dict[str, Any],
        task: str,
        on_iteration: IterationCallback | None = None,
        eval_df: pl.DataFrame | None = None,
        offset: str | None = None,
        monotone_constraints: dict[str, int] | None = None,
        feature_weights: dict[str, float] | None = None,
    ) -> FitResult:
        """Fit a GLM using RustyStats dict API."""
        ...

    def predict(
        self, model: Any, df: pl.DataFrame, features: list[str],
    ) -> np.ndarray:
        """Generate predictions using fitted GLM."""
        ...

    def feature_importance(self, model: Any) -> list[dict[str, Any]]:
        """Return absolute coefficient magnitudes as importance."""
        ...

    def save(self, model: Any, path: Path) -> None:
        """Save model using RustyStats native binary serialization."""
        ...
```

#### 4.1.1 `fit()` Implementation

The `fit()` method receives GLM-specific config via the `params` dict (terms, family, link, regularization, interactions, etc.). The standard `BaseAlgorithm.fit()` signature is reused — GLM-specific fields are packed into `params` by `TrainingJob`.

```python
def fit(self, train_df, features, cat_features, target, weight, params, task,
        on_iteration=None, eval_df=None, offset=None, **kwargs) -> FitResult:
    import rustystats as rs

    # Extract GLM-specific config from params
    terms = params.get("terms", {})
    family = params.get("family", "gaussian")
    link = params.get("link", None)
    var_power = params.get("var_power", 1.5)
    intercept = params.get("intercept", True)
    interactions_config = params.get("interactions", [])
    regularization = params.get("regularization", None)
    alpha = params.get("alpha", 0.0)
    l1_ratio = params.get("l1_ratio", 0.0)

    # If no terms specified, auto-generate from features
    if not terms:
        terms = _auto_terms(features, cat_features)

    # Build RustyStats interactions list
    rs_interactions = _build_interactions(interactions_config, terms)

    # Build GLM
    builder = rs.glm_dict(
        response=target,
        terms=terms,
        data=train_df,
        family=family,
        link=link,
        var_power=var_power,
        offset=offset,
        weights=weight,
        intercept=intercept,
        interactions=rs_interactions or None,
    )

    # Fit with regularization if specified.  sklearn's internal CV for
    # alpha search is hard-coded to 5 folds — the matching sklearn default
    # — because it's a numerical implementation detail, not a user knob.
    fit_kwargs = {}
    if regularization:
        fit_kwargs["regularization"] = regularization
        fit_kwargs["cv"] = 5
        if alpha > 0:
            fit_kwargs["alpha"] = alpha
        if regularization == "elastic_net":
            fit_kwargs["l1_ratio"] = l1_ratio

    if on_iteration:
        on_iteration(0, 1, {})  # GLM fits fast, just signal start

    result = builder.fit(**fit_kwargs)

    if on_iteration:
        on_iteration(1, 1, {})  # Signal completion

    # Build loss history (GLM doesn't iterate like GBM, so just final metrics)
    loss_history = [{
        "iteration": 1.0,
        "train_deviance": float(result.deviance),
    }]

    return FitResult(
        model=result,
        best_iteration=result.iterations,
        loss_history=loss_history,
    )
```

#### 4.1.2 `_auto_terms()` — Fallback When No Terms Specified

If a user selects GLM but doesn't configure terms (e.g., quick first fit), auto-generate terms from the feature list:

```python
def _auto_terms(features: list[str], cat_features: list[str]) -> dict:
    terms = {}
    cat_set = set(cat_features)
    for f in features:
        if f in cat_set:
            terms[f] = {"type": "categorical"}
        else:
            terms[f] = {"type": "linear"}
    return terms
```

#### 4.1.3 `predict()`, `feature_importance()`, `save()`

```python
def predict(self, model, df, features) -> np.ndarray:
    return model.predict(df).flatten()

def feature_importance(self, model) -> list[dict[str, Any]]:
    # Use absolute coefficient values as importance proxy
    names = model.feature_names
    coefs = np.abs(model.coefficients)
    pairs = sorted(zip(names, coefs), key=lambda x: x[1], reverse=True)
    return [{"feature": n, "importance": float(v)} for n, v in pairs]

def save(self, model, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Use .rsglm extension for RustyStats models
    model_bytes = model.to_bytes()
    with open(path, "wb") as f:
        f.write(model_bytes)
```

#### 4.1.4 GLM-Specific Diagnostics Methods (Optional)

```python
def coefficients_table(self, model) -> list[dict[str, Any]]:
    """Return full coefficient table with SEs, z-stats, p-values."""
    coef_df = model.coef_table()  # Returns Polars DataFrame
    return coef_df.to_dicts()

def relativities(self, model) -> list[dict[str, Any]]:
    """Return exp(coef) relativities table."""
    rel_df = model.relativities()
    return rel_df.to_dicts()

def fit_statistics(self, model) -> dict[str, float]:
    """Return GLM fit statistics."""
    return {
        "deviance": float(model.deviance),
        "null_deviance": float(model.null_deviance()),
        "aic": float(model.aic()),
        "bic": float(model.bic()),
        "log_likelihood": float(model.llf()),
        "df_model": float(model.df_model),
        "df_residual": float(model.df_resid),
        "n_obs": float(model.nobs),
        "iterations": float(model.iterations),
        "converged": 1.0 if model.converged else 0.0,
    }

def diagnostics(self, model, data, cat_features) -> dict[str, Any]:
    """Run RustyStats built-in diagnostics."""
    diag = model.diagnostics(
        data=data,
        categorical_factors=cat_features,
        continuous_factors=[f for f in model.feature_names if f not in cat_features],
    )
    return diag.to_dict()
```

### 4.2 Algorithm Registry Update — `_algorithms.py`

```python
ALGORITHM_REGISTRY: dict[str, type[BaseAlgorithm]] = {
    "catboost": CatBoostAlgorithm,
    "glm": GLMAlgorithm,  # ← new
}
```

The lazy import pattern keeps RustyStats optional:
```python
# At bottom of _algorithms.py
try:
    from haute.modelling._rustystats import GLMAlgorithm
    ALGORITHM_REGISTRY["glm"] = GLMAlgorithm
except ImportError:
    pass  # RustyStats not installed — GLM unavailable
```

### 4.3 TrainingJob Changes — `_training_job.py`

#### 4.3.1 GLM-Specific Params Packing

The `_train_model()` method currently packs CatBoost-specific params. For GLM, the terms/family/interactions config needs to be forwarded via `fit_params`:

```python
def _train_model(self, split_result, features, cat_features, on_iteration, _report):
    # ... existing code ...

    fit_params = {**self.params}

    if self.algorithm == "glm":
        # Pack GLM-specific config into params for GLMAlgorithm.fit()
        # These come from the node config JSON
        # (terms, family, link, interactions, regularization, etc.)
        # Already in self.params — no transformation needed
        pass
    else:
        # CatBoost: resolve loss function
        resolved_loss = resolve_loss_function(self.loss_function, self.task, self.variance_power)
        if resolved_loss:
            fit_params["loss_function"] = resolved_loss
```

#### 4.3.2 Pool Bypass for GLM

GLM doesn't use CatBoost Pools. The `_train_model()` method currently builds pools. For GLM, pass DataFrames directly:

```python
if self.algorithm == "glm":
    # GLM: pass DataFrames directly (no Pool conversion)
    fit_result = algo.fit(
        train_df, features, cat_features,
        self.target, self.weight, fit_params, self.task,
        on_iteration=on_iteration,
        offset=self.offset,
    )
else:
    # CatBoost: build pools (existing code)
    ...
```

#### 4.3.3 File Extension

```python
def _save_artifacts(self, train_result):
    ext_map = {"catboost": ".cbm", "glm": ".rsglm"}
    ext = ext_map.get(self.algorithm, ".model")
    model_path = Path(self.output_dir) / f"{self.name}{ext}"
    train_result.algo.save(train_result.model, model_path)
    return model_path
```

#### 4.3.4 GLM-Specific Diagnostics in `_compute_metrics()`

After computing standard metrics (gini, deviance, etc.), compute GLM-specific outputs:

```python
# In _compute_metrics(), after standard metric computation:
glm_coefficients: list[dict[str, Any]] = []
glm_relativities: list[dict[str, Any]] = []
glm_fit_statistics: dict[str, float] = {}

if hasattr(algo, "coefficients_table"):
    try:
        glm_coefficients = algo.coefficients_table(model)
    except Exception:
        pass
if hasattr(algo, "relativities"):
    try:
        glm_relativities = algo.relativities(model)
    except Exception:
        pass
if hasattr(algo, "fit_statistics"):
    try:
        glm_fit_statistics = algo.fit_statistics(model)
    except Exception:
        pass
```

### 4.4 TrainResult Extensions

Add GLM-specific fields to `TrainResult`:

```python
@dataclass
class TrainResult:
    # ... existing fields ...

    # GLM-specific (empty for CatBoost)
    glm_coefficients: list[dict[str, Any]] = field(default_factory=list)
    glm_relativities: list[dict[str, Any]] = field(default_factory=list)
    glm_fit_statistics: dict[str, float] = field(default_factory=dict)
    glm_regularization_path: dict[str, Any] | None = None
```

### 4.5 Model Loading for Scoring — `_mlflow_io.py`

Add RustyStats model loading alongside CatBoost:

```python
def load_local_model(path: str | Path, task: str = "regression") -> ScoringModel:
    path = Path(path)
    if path.suffix == ".rsglm":
        return _load_rustystats_model(path)
    elif path.suffix == ".cbm":
        return _load_catboost_model(path, task)
    else:
        raise ValueError(f"Unknown model format: {path.suffix}")

def _load_rustystats_model(path: Path) -> ScoringModel:
    import rustystats as rs
    with open(path, "rb") as f:
        model = rs.GLMModel.from_bytes(f.read())
    return ScoringModel(model, flavor="rustystats")
```

### 4.6 ScoringModel Updates

The `ScoringModel` wrapper needs to handle RustyStats predict:

```python
class ScoringModel:
    def predict(self, df: pl.DataFrame, features: list[str] | None = None) -> np.ndarray:
        if self.flavor == "rustystats":
            return self._model.predict(df).flatten()
        elif self.flavor == "catboost":
            # existing CatBoost predict path
            ...
```

### 4.7 MLflow Integration — `_mlflow_log.py`

For MLflow logging, the model file (`.rsglm`) is logged as an artifact. A thin pyfunc wrapper enables serving:

```python
class _RustyStatsPyfunc(mlflow.pyfunc.PythonModel):
    """Thin wrapper for serving RustyStats models via MLflow."""

    def load_context(self, context):
        import rustystats as rs
        model_path = context.artifacts["model_path"]
        with open(model_path, "rb") as f:
            self._model = rs.GLMModel.from_bytes(f.read())

    def predict(self, context, model_input, params=None):
        if isinstance(model_input, pl.DataFrame):
            return self._model.predict(model_input)
        # Pandas input from MLflow serving
        import polars as pl
        df = pl.from_pandas(model_input)
        return self._model.predict(df)
```

---

## 5. Frontend Implementation

### 5.1 Algorithm Selection Gateway — `ModellingConfig.tsx`

Add GLM button alongside CatBoost in the gateway:

```tsx
// Gateway: pick algorithm
if (!algorithm) {
  return (
    <div className="px-4 py-3 space-y-3">
      <label>Select Algorithm</label>
      <button onClick={() => onUpdate("algorithm", "catboost")}>
        <div className="text-xs font-semibold">CatBoost</div>
        <div className="text-[11px]">Gradient boosting — handles categoricals natively, fast GPU training</div>
      </button>
      <button onClick={() => onUpdate("algorithm", "glm")}>
        <div className="text-xs font-semibold">GLM</div>
        <div className="text-[11px]">Generalised linear model — interpretable coefficients, regulatory-friendly</div>
      </button>
    </div>
  )
}
```

### 5.2 Config Panel Routing

When `algorithm === "glm"`, show GLM-specific config panels instead of CatBoost ones:

```tsx
// ModellingConfig.tsx
if (algorithm === "glm") {
  return (
    <div className="px-4 py-3 space-y-4">
      <GLMTargetConfig ... />
      <GLMFactorConfig ... />
      <GLMRegularizationConfig ... />
      <SplitAndMetricsConfig ... />  {/* Shared */}
      <TrainingActionsAndResults ... />  {/* Shared */}
    </div>
  )
}
// else: existing CatBoost panels
```

### 5.3 `GLMTargetConfig` — Family, Link, Target, Weight, Offset

Replaces `TargetAndTaskConfig` when GLM is selected.

```
┌─────────────────────────────────────────┐
│ TARGET & WEIGHT                         │
│ Target column    [claim_count     ▼]    │
│ Weight column    [exposure        ▼]    │
│ Offset column    [log_exposure    ▼]    │
│                                         │
│ FAMILY                                  │
│ [poisson] [gamma] [tweedie] [gaussian]  │
│ [binomial] [quasipoisson] [negbinomial] │
│                                         │
│ LINK FUNCTION                           │
│ [auto (log)] [identity] [logit] ...     │
│                                         │
│ ☐ Variance power (Tweedie only)         │
│ ═══════●═══════  1.50                   │
│                                         │
│ ☑ Intercept                             │
│                                         │
│ METRICS                                 │
│ [Gini] [Poisson Dev.] [AIC] [BIC]      │
└─────────────────────────────────────────┘
```

**Metrics for GLM**: Add `aic`, `bic`, `deviance` to the metric buttons. These are computed from `glm_fit_statistics` (not from the generic metric registry), but displayed alongside standard metrics.

### 5.4 `GLMFactorConfig` — The Core Panel

#### 5.4.1 Visual Builder (Top Half)

Per-factor rows showing each included feature with its term type:

```
┌─────────────────────────────────────────┐
│ FACTORS (12 of 15)                      │
│                                         │
│ driver_age   [bs       ▼] df=5 ▼ ↓mon  │
│ vehicle_age  [linear   ▼]       ↑mon    │
│ area         [categor. ▼]               │
│ region       [categor. ▼]               │
│ brand        [tgt_enc  ▼] pw=1.0        │
│ engine_size  [linear   ▼]               │
│ postcode     [─exclude─]          ⊘     │
│ ...                                     │
│                                         │
│ INTERACTIONS                    [+ Add] │
│ [driver_age ▼] × [region ▼] ☑ main  🗑  │
│ [vehicle_age▼] × [brand  ▼] ☑ main  🗑  │
│                                         │
├─────────────────────────────────────────┤
│ DICT API (JSON)                   [↕]   │
│ ┌─────────────────────────────────────┐ │
│ │ {                                   │ │
│ │   "driver_age": {                   │ │
│ │     "type": "bs",                   │ │
│ │     "df": 5,                        │ │
│ │     "monotonicity": "decreasing"    │ │
│ │   },                                │ │
│ │   "vehicle_age": {                  │ │
│ │     "type": "linear",               │ │
│ │     "monotonicity": "increasing"    │ │
│ │   },                                │ │
│ │   ...                               │ │
│ │ }                                   │ │
│ └─────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

#### 5.4.2 Visual Builder Row Detail

Each factor row:

| Element | Behaviour |
|---|---|
| **Name** | Column name (monospace) |
| **Type dropdown** | `linear` / `categorical` / `bs` / `ns` / `target_encoding` / `expression` / `exclude` |
| **Type-specific params** | Inline, shown only when relevant: `df` (number input for bs/ns), `monotonicity` (↑/↓/─ toggle), `prior_weight` (number for TE), `expr` (text for expression) |
| **Exclude** | Selecting "exclude" type removes from terms, adds to exclude list |

Default type assignment:
- String/Categorical columns → `categorical`
- Numeric columns → `linear`

#### 5.4.3 Bidirectional Sync

**Visual → JSON**: On any visual change (type dropdown, param edit), regenerate the JSON in the textarea.

**JSON → Visual**: On textarea blur (same pattern as CatBoost params), parse JSON and update the visual builder rows. Invalid JSON shows a red border + error message (same as existing CatBoost param editor).

Implementation: Single source of truth is the config's `terms` dict. Both the visual builder and JSON textarea are views of this dict. Changes from either side call `onUpdate("terms", newTerms)`.

#### 5.4.4 Interactions Section

Below the factor list:

```tsx
<InteractionsList
  interactions={interactions}           // from config
  availableFactors={includedFeatures}   // factors with terms
  onAdd={() => onUpdate("interactions", [...interactions, { factors: ["", ""], include_main: true }])}
  onRemove={(idx) => onUpdate("interactions", interactions.filter((_, i) => i !== idx))}
  onChange={(idx, updated) => onUpdate("interactions", interactions.map((x, i) => i === idx ? updated : x))}
/>
```

Each interaction row:
- Two `<select>` dropdowns populated from `includedFeatures`
- "Include main effects" checkbox (default: true)
- Delete button

The interactions are stored separately from `terms` in the config, and included in the JSON textarea below the terms (full config view).

### 5.5 `GLMRegularizationConfig`

Collapsible section:

```
┌─────────────────────────────────────────┐
│ ▶ REGULARIZATION                        │
│                                         │
│ [None] [Ridge] [Lasso] [Elastic Net]    │
│                                         │
│ (when Ridge/Lasso/Elastic Net selected):│
│ Alpha  [Auto (CV)] / [Manual: 0.1    ]  │
│ CV folds  [5]                           │
│ Selection  [min] [1se]                  │
│                                         │
│ (when Elastic Net):                     │
│ L1 ratio ═══════●═══════  0.50          │
└─────────────────────────────────────────┘
```

### 5.6 GLM Results Display

#### 5.6.1 TrainResult Type Extension (Frontend)

Add GLM-specific fields to the `TrainResult` type in `useNodeResultsStore.ts`:

```typescript
export type TrainResult = {
  // ... existing fields ...

  // GLM-specific
  glm_coefficients?: {
    feature: string
    coefficient: number
    std_error: number
    z_value: number
    p_value: number
    significance: string  // "***", "**", "*", ""
  }[]
  glm_relativities?: {
    feature: string
    relativity: number
    ci_lower: number
    ci_upper: number
  }[]
  glm_fit_statistics?: Record<string, number>
  glm_regularization_path?: {
    alphas: number[]
    deviances: number[]
    selected_alpha: number
    n_nonzero: number
  }
}
```

#### 5.6.2 Results Panel — Algorithm-Aware

The `ModellingPreview` component (shown in the bottom preview panel) detects algorithm from the result and renders accordingly.

**GLM Results Layout**:

```
┌─────────────────────────────────────────┐
│ Coefficient Table                       │
│ ┌──────────┬────────┬──────┬──────┬───┐│
│ │ Feature  │ Coef   │ SE   │ z    │   ││
│ ├──────────┼────────┼──────┼──────┼───┤│
│ │ Intercept│ -2.341 │ 0.05 │-46.8 │***││
│ │ area[B]  │  0.182 │ 0.03 │ 6.1  │***││
│ │ area[C]  │  0.341 │ 0.04 │ 8.5  │***││
│ │ driver_  │ -0.015 │ 0.01 │ -2.1 │ * ││
│ │ ...      │        │      │      │   ││
│ └──────────┴────────┴──────┴──────┴───┘│
│                                         │
│ Relativities (exp(coef))                │
│ ┌──────────┬──────────┬────────────────┐│
│ │ Feature  │ Relativ. │ 95% CI         ││
│ ├──────────┼──────────┼────────────────┤│
│ │ area[B]  │   1.200  │ [1.13, 1.27]   ││
│ │ area[C]  │   1.406  │ [1.30, 1.52]   ││
│ │ ...      │          │                ││
│ └──────────┴──────────┴────────────────┘│
│                                         │
│ Fit Statistics                          │
│ AIC: 12,345  BIC: 12,890               │
│ Deviance: 8,901  Null Dev: 14,567      │
│ Observations: 50,000  Converged: Yes    │
│                                         │
│ ──── Shared Diagnostics ────            │
│ [Metrics] [Double Lift] [A/E] [Lorenz]  │
│ [Residuals] [Actual vs Predicted]       │
└─────────────────────────────────────────┘
```

**Shared components** (reused from CatBoost): Double lift chart, A/E per feature, Lorenz curve, residuals histogram, actual vs predicted scatter, metrics summary.

**GLM-only components** (new): Coefficients table, relativities table, fit statistics card.

**CatBoost-only components** (hidden for GLM): Loss curve (iteration-by-iteration), SHAP summary, feature importance (LossFunctionChange), PDP.

### 5.7 Model Card — `_model_card.py`

Extend the HTML model card to include GLM-specific sections:

- Coefficients table with significance codes
- Relativities table
- Fit statistics (AIC, BIC, deviance, null deviance)
- Standard diagnostics (double lift, A/E, Lorenz, residuals) — shared

### 5.8 RAM Estimation — `_ram_estimate.py`

GLM has lower memory overhead than CatBoost (no tree-building). Adjust the overhead multiplier:

```python
# CatBoost needs ~3x for Pool + tree building
CATBOOST_OVERHEAD = 3.0
# GLM needs ~1.5x for design matrix construction
GLM_OVERHEAD = 1.5
```

Frontend: Use `GLM_OVERHEAD` when `algorithm === "glm"` (or receive it from the backend estimate).

---

## 6. File Changes Summary

### 6.1 New Files

| File | Purpose |
|---|---|
| `src/haute/modelling/_rustystats.py` | `GLMAlgorithm(BaseAlgorithm)` implementation |
| `frontend/src/panels/modelling/GLMTargetConfig.tsx` | Family, link, target, weight, offset, metrics |
| `frontend/src/panels/modelling/GLMFactorConfig.tsx` | Factor visual builder + JSON textarea + interactions |
| `frontend/src/panels/modelling/GLMRegularizationConfig.tsx` | Regularization toggle + params |
| `frontend/src/panels/modelling/GLMResultsDisplay.tsx` | Coefficients table, relativities, fit stats |

### 6.2 Modified Files

| File | Change |
|---|---|
| `src/haute/modelling/_algorithms.py` | Register `GLMAlgorithm` in `ALGORITHM_REGISTRY` (lazy import) |
| `src/haute/modelling/_training_job.py` | GLM param packing, pool bypass, file extension, GLM diagnostics in `_compute_metrics()`, new `TrainResult` fields |
| `src/haute/modelling/_model_card.py` | GLM-specific model card sections |
| `src/haute/_mlflow_io.py` | `.rsglm` loading in `load_local_model()`, RustyStats `ScoringModel` flavor |
| `src/haute/routes/modelling.py` | Pass GLM config through to TrainingJob |
| `frontend/src/panels/ModellingConfig.tsx` | GLM algorithm button in gateway, route to GLM panels |
| `frontend/src/stores/useNodeResultsStore.ts` | GLM-specific fields on `TrainResult` type |
| `frontend/src/panels/modelling/TrainingActionsAndResults.tsx` | Algorithm-aware RAM overhead multiplier |
| `frontend/src/utils/nodeTypes.ts` | Update description to "Train a CatBoost or GLM model" |

### 6.3 Test Files

| File | Purpose |
|---|---|
| `tests/test_rustystats_algorithm.py` | Unit tests for `GLMAlgorithm` (fit, predict, save, feature_importance, diagnostics) |
| `tests/test_glm_training_job.py` | Integration tests for `TrainingJob(algorithm="glm", ...)` |
| `tests/test_glm_model_loading.py` | Tests for `.rsglm` load/save round-trip, `ScoringModel` predict |
| `frontend/src/panels/modelling/GLMFactorConfig.test.tsx` | Visual builder, JSON sync, interaction rows |
| `frontend/src/panels/modelling/GLMTargetConfig.test.tsx` | Family/link selection |
| `frontend/src/panels/modelling/GLMResultsDisplay.test.tsx` | Coefficients table, relativities rendering |

---

## 7. Implementation Phases

### Phase 1 — Backend Core (GLMAlgorithm)

1. Create `src/haute/modelling/_rustystats.py` with `GLMAlgorithm`
2. Implement `fit()`, `predict()`, `feature_importance()`, `save()`
3. Implement `coefficients_table()`, `relativities()`, `fit_statistics()`
4. Register in `ALGORITHM_REGISTRY` (lazy import)
5. Update `_training_job.py` for GLM pool bypass and param packing
6. Update `_save_artifacts()` for `.rsglm` extension
7. Add GLM fields to `TrainResult`
8. Add `_compute_metrics()` GLM diagnostics
9. Write `test_rustystats_algorithm.py` and `test_glm_training_job.py`

### Phase 2 — Model Loading & Scoring

1. Update `_mlflow_io.py` for `.rsglm` loading
2. Update `ScoringModel` for RustyStats predict
3. Add MLflow pyfunc wrapper for serving
4. Write `test_glm_model_loading.py`

### Phase 3 — Frontend Config Panels

1. Add GLM button to algorithm gateway
2. Build `GLMTargetConfig` (family, link, target, weight, offset)
3. Build `GLMFactorConfig` (visual builder + JSON textarea + bidirectional sync)
4. Build interaction rows in `GLMFactorConfig`
5. Build `GLMRegularizationConfig`
6. Route `ModellingConfig` to GLM panels when `algorithm === "glm"`
7. Write frontend tests

### Phase 4 — Frontend Results & Diagnostics

1. Build `GLMResultsDisplay` (coefficients table, relativities, fit stats)
2. Update `ModellingPreview` for algorithm-aware rendering
3. Extend `TrainResult` type in store
4. Update model card for GLM sections
5. Update RAM estimation for GLM overhead
6. Write frontend tests

---

## 8. Edge Cases & Validation

| Case | Handling |
|---|---|
| RustyStats not installed | GLM button hidden in gateway; `ALGORITHM_REGISTRY` doesn't include `"glm"` |
| Empty terms dict | Auto-generate from features (linear for numeric, categorical for string) |
| Invalid JSON in terms textarea | Red border + error message, don't update config (same as CatBoost params) |
| Feature in terms but not in data | RustyStats raises error at fit time; surface in training error display |
| Feature in interactions but not in terms | Validation: highlight in UI, block training |
| Regularization with no features surviving | Show warning with non-zero feature count from result |
| Model convergence failure | `result.converged` is False; show warning in results |
| Tweedie without var_power | Default to 1.5 (standard insurance default) |
| Mixed exclude/terms state | Visual builder shows excluded features greyed out; terms dict only contains included features |

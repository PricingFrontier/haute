# Model Training

You want to train a machine learning model from your pipeline data. The Model Training
node supports three gradient-boosted tree families (CatBoost, XGBoost and LightGBM),
t-boost (gradient boosting whose model is exactly a set of rating tables), the Explainable
Boosting Machine (EBM, from InterpretML), and the GLM (generalised linear model, via
RustyStats). You choose the family when you add the node, and it stays fixed:
a different family is a new node. Every run records reproducible evaluation evidence and
saves a native model plus its feature contract. A completed result can then be logged to MLflow
as a candidate run and scored by a [Model Scoring](model-score.md) node.

!!! note "What is MLflow?"
    [MLflow](https://mlflow.org) is an open-source platform for tracking experiments and storing models. If you're new to MLflow, the key concepts are: an **experiment** groups related training runs, a **run** is a single training attempt with its metrics and parameters, and the **model registry** stores production-ready models by name and version.

This node takes a single input and produces no downstream data: it is a terminal node.
Training in the editor keeps the model and its evaluation artifacts with that training
result; logging it to MLflow and saving it to a project file are explicit actions in the
node's **EXPORT** pane.

## Choosing the model family

A new Model Training node opens on **Select algorithm**, with one button per family:

| Button | Description on the button |
|---|---|
| **CatBoost** | Gradient boosting - handles categoricals natively, fast GPU training |
| **EBM** | Explainable boosting - additive shape functions and pairwise interactions you can read directly |
| **GLM** | Generalised linear model - interpretable coefficients, regulatory-friendly |
| **LightGBM** | Gradient boosting - fast leaf-wise trees with native categoricals and early stopping on CPU |
| **t-boost** | Rating-table boosting - gradient boosting whose model is exactly a set of rating tables |
| **XGBoost** | Gradient boosting - histogram trees with native categoricals and early stopping on CPU |

Choosing a tree, t-boost or EBM family fills in its starting parameters (see
[The PARAMETERS pane](#the-parameters-pane)), and every family starts from a default split: a random split with seed 42, a 20% holdout validation set and no test
set. The panel then shows six panes, **TARGET**, **FEATURES**, **PARAMETERS**, **SPLIT**,
**TRAIN** and **EXPORT**, described below in that order. A pane whose settings stop the model
training shows a warning mark on its tab, and the **TRAIN** tab shows a running indicator
while a model trains. The node has no **COLUMNS** tab.

### Model families

| | CatBoost | XGBoost | LightGBM | t-boost | EBM | GLM |
|---|---|---|---|---|---|---|
| Tasks | Regression, binary classification | Regression, binary classification | Regression, binary classification | Regression, binary classification | Regression, binary classification | Regression |
| Losses | RMSE, MAE, Poisson, Tweedie, Logloss, CrossEntropy | RMSE, MAE, Poisson, Gamma, Tweedie, Logloss | RMSE, MAE, Poisson, Gamma, Tweedie, Logloss | RMSE, Poisson, Gamma, Tweedie, Logloss | RMSE, Poisson, Gamma, Tweedie, Logloss | **Family** and **Link Function** |
| Round budget (in **Parameters JSON**) | `iterations` | `num_boost_round` | `num_iterations` | `n_trees` (required ceiling) | `max_rounds` (required) | — |
| Early stopping | `early_stopping_rounds` | `early_stopping_rounds` | `early_stopping_round` | On the validation rows, each bag at its own round; else on its own holdout | Never | — |
| Final refit (when refitting) | Validation-weighted round count | Validation-weighted round count | Validation-weighted round count | Winning parameters, unchanged | Winning `max_rounds`, unchanged | Same settings |
| Tuning | Yes | Yes | Yes | Yes | Yes (`max_rounds` searchable) | No |
| Monotone constraints | Yes | Yes, except with MAE | Yes, except with MAE | Yes | Yes, not on an interaction | Per term |
| Feature weights | Yes (pipeline file only) | No | No | No | No | No |
| Interactions | Learned by trees | Learned by trees | Learned by trees | Learned, up to `max_interaction_order` features per table | Chosen count or explicit pairs | Explicit cards |
| Offset | Baseline | `base_margin` | `init_score` (added at prediction) | Exposure (Poisson, Gamma, Tweedie); added to the raw score (RMSE) | `init_score` | Offset term |
| Trace explanation | SHAP values | Native contributions | Native contributions | Table values (an interaction is one table) | Term scores (an interaction is one term) | Term contributions |
| Model file | `.cbm` | `.ubj` | `.lgbm` | `.tboost` | `.ebm` | `.rsglm` |
| Compute | CPU, optional GPU | CPU, optional CUDA GPU | CPU | CPU | CPU, one thread | CPU |

Tree families train with `HAUTE_TRAINING_THREADS` threads (default: every logical CPU).
On macOS, XGBoost and LightGBM need Homebrew's `libomp` (`brew install libomp`).

## The TARGET pane

The pane opens with the chosen family (for example **Algorithm: CatBoost**). For CatBoost,
XGBoost, LightGBM, t-boost and EBM it has three sections; the GLM's version is described in
[GLM target](#glm-target) below.

**Target and objective**

| Field | What it does |
|---|---|
| **Target column** | The column the model predicts. Pick it from the searchable list (**Select target…** until you do). Training is blocked with **Select a target column.** until it is set. |
| **Objective** | The training loss, one button per loss the family supports (see the table above). Choosing a loss also sets the task (regression or classification) and switches on that loss's usual metrics; clicking the selected loss again clears it. Training is blocked until a loss is chosen, because an unset loss would silently train under the library default. MAE is not offered for an EBM. |
| **Variance power** | Shown for the **Tweedie** loss: a slider with a number box beside it, between 1 (Poisson) and 2 (Gamma), exclusive. A new Tweedie selection starts at 1.5. A saved value outside that range shows **Saved variance power must be greater than 1 and less than 2.** |
| **Positive class** | Shown for a classification loss when the target is not Boolean: the label the model predicts the probability of. Predictions above 0.5 are labelled with it. For a numeric target the label reads **Positive class (only if the labels are not 0/1)**; for text labels it is required and shows **Choose which label is the positive class.** until set. |

Binary classification needs exactly two target classes. A Boolean or 0/1 target is
positive at `True`/`1`; any other pair needs a **Positive class**. Every family labels a
prediction positive when its positive-class probability is above 0.5.

**Weight and offset**

| Field | What it does |
|---|---|
| **Weight column (optional)** | A numeric column that weights each row in the loss (for example exposure). **None** leaves rows unweighted. |
| **Offset column (optional)** | A numeric column the model folds in through its link function. Under a log link (a log-link GLM, or a `Poisson`, `Gamma` or `Tweedie` loss) it is a strictly positive exposure multiplier: 2× exposure gives 2× the expected count, and null, zero, or negative values are refused when training and when scoring. Under any other link it is added to the prediction. A constant column of 1 is the unit basis under a log link. The offset column must be present when the model scores. Different from the weight, which weights the loss. The info icon beside the label repeats this. |

One column plays one role: the target is never offered as the weight or offset, and the
weight and offset lists hold numeric columns only.

**Metrics**

The evaluation metrics, as toggle buttons: **Gini**, **RMSE**, **MAE**, **MSE**, **R²**,
**Poisson Deviance**, **Tweedie Deviance**, **Gamma Deviance**, **AUC** and **Logloss**.
Only the metrics that suit the chosen loss can be switched on: regression losses offer the
first eight, classification losses **AUC** and **Logloss**. Hovering over a disabled metric
says why (for example **AUC is not available with Poisson**, or **Choose an objective to
enable compatible metrics**).

!!! tip "Choosing a metric"
    For frequency models (Poisson), use Poisson deviance. For severity models, use Gamma deviance with a Gamma loss or Tweedie deviance with a Tweedie loss. For general regression, RMSE or Gini are common choices. For classification, use AUC or Logloss.

### GLM target { #glm-target }

A GLM's **TARGET** pane reads **Algorithm Rustystats** and has these controls:

| Field | What it does |
|---|---|
| **Target column** | As above. |
| **Family** | The distribution: **Poisson** (claim frequency), **Gamma** (claim severity), **Tweedie** (pure premium), **Gaussian** (linear regression), **Binomial** (binary outcomes), **Quasi-Poisson** (overdispersed counts), **Quasi-Binomial** (overdispersed binary outcomes) or **Neg. Binomial** (overdispersed counts with an explicit theta). Hover over a button for its use. Choosing a family resets the link to the family's canonical one and switches on its usual metrics. Training is blocked until a family is chosen, because an unset family would silently train a Gaussian model. |
| **Link Function** | **auto (...)** uses the family's canonical link: log for Poisson, Quasi-Poisson, Gamma, Tweedie and Negative Binomial; logit for Binomial and Quasi-Binomial; identity for Gaussian. The other buttons are the links RustyStats supports for the family: Gaussian, Poisson, Quasi-Poisson, Gamma, Tweedie and Negative Binomial accept log or identity, and Binomial and Quasi-Binomial accept logit, log or identity. |
| **Variance power (1.0=Poisson, 2.0=Gamma)** | Tweedie only, and required: click **Set variance power (required for Tweedie)** to start at 1.5, then adjust the slider between 1 and 2. **Estimate from data** profiles it on the node's training data. |
| **Dispersion theta (required for Neg. Binomial)** | Negative Binomial only: a positive dispersion (variance = mean + mean²/theta). RustyStats refuses to fit without it. Type a value or click **Estimate from data**, which profiles the likelihood on the node's training data. A value of 0 or less shows **Theta must be greater than 0.** |
| **Weight column (optional)**, **Offset column (optional)** | As above. |
| **Intercept** | Whether to fit an intercept. Ticked by default. |
| **Metrics** | **Gini**, **RMSE**, **MAE**, **Poisson Dev.**, **Tweedie Dev.**, **R²**, **AUC** and **Logloss**. |

If an estimate fails, the pane shows **Estimation failed:** and the reason.

## The FEATURES pane

For CatBoost, XGBoost, LightGBM and EBM the pane lists every column that could be a
feature, with a count of how many are included and excluded. Every feature starts
unticked on a new node: tick the ones the model should learn from. Columns that already have a
role (target, weight, offset, and the group or date column the split uses) are left out
of the list and are never features.

- **Search features** narrows the list by name.
- **All**, **Included** and **Excluded** filter the list by membership; each shows its count.
- **Include all** and **Exclude all** tick or clear every listed feature.
- The tick box beside each feature includes or excludes it.
- **Monotonicity** sets a constraint on an included numeric feature: **↓** decreasing, **−**
  no constraint (the default) and **↑** increasing. The buttons are disabled for a
  non-numeric feature (**Monotonicity is only available for numeric features.**) and for an
  excluded one (**Include this feature to set monotonicity.**).
- A ticked column that is no longer upstream shows as **<name> - not found** with a
  button to remove it.

Feature selection is explicit: Haute trains on exactly the columns you tick, and a column
that appears upstream later stays unticked until you tick it. Until at least one feature is
ticked, the pane shows **Tick at least one feature on the Features pane.** and training is
blocked. If you later give a ticked column a role (for example make it the target), it stops
being a feature while it has that role and is ticked again once it no longer does. Leave ID
columns, dates and columns derived from the target unticked to prevent data leakage. The
training result records the final ordered feature set, the retained metadata, and every
excluded column with its reason.

Haute validates feature selection before collecting training data, so a missing, invalid,
or unsuitable feature fails clearly before a large collection begins. See
[Execution Strategy](../execution-strategy.md) for reading execution diagnostics.

XGBoost and LightGBM refuse monotone constraints with the MAE loss: training is blocked
with a message asking you to remove them here or choose another loss. XGBoost's
absolute-error objective re-fits each leaf after the tree is built, which breaks the
constraint.

**Pairwise interactions (EBM)**

An EBM adds a **Pairwise interactions** section. Every included feature is a main effect;
interactions add two-feature terms, each kept as one term in results and explanations. A
monotone-constrained feature cannot take part in an interaction.

- **Let EBM choose** (the default): **Up to** a number of interactions (10 to start), the
  strongest pairs EBM finds on the training rows. 0 means main effects only.
- **Choose pairs**: fix the pairs yourself. **Add interaction** adds a row with two feature
  lists; a monotone feature is listed but disabled, marked **(monotone)**. A pair needs two
  different features, and a pair listed twice is refused.

### GLM terms and interactions { #glm-features }

A GLM's features are its terms and interaction cards; the feature tick boxes and
monotonicity buttons above do not apply to it.

The **FEATURES** list shows how many columns are in the model. Above it:

- **Search features** narrows the list by name.
- **Fit all with defaults** adds a term with its default fit for every column that has none.
- **Remove all terms** clears every term and interaction, after asking **Remove every term
  and interaction from this model?**
- **In model only** hides columns with no term.
- **Builder** and **JSON** switch between the cards and a JSON editor for the whole terms
  dictionary (saved when you leave the box).

Each column's dtype decides which fits it offers:

| Column dtype | Fits | Default |
|---|---|---|
| Float, Decimal | **Linear**, **B-spline**, **Nat. spline**, **Monotone spline**; numeric expressions | Linear |
| Integer | The float fits plus **Categorical**, **Target enc.** and **Frequency enc.** | Linear |
| Boolean | **Categorical**, **Target enc.**, **Frequency enc.** | Categorical |
| String, Categorical, Enum | **Categorical**, **Target enc.**, **Frequency enc.** | Categorical |

Dates, times, durations, lists, structs, and binary columns cannot be fitted; the pane hides
them and says how many it hid. Target, weight, offset, fold, identifier, and split-key
columns cannot be terms, expression columns, or interaction factors.

**Add term** on a column adds its default fit. Clicking it again on a numeric column adds an
**Expression** term (starting at `column ** 2`); on an integer or categorical column it adds
a second encoding. When a column has every fit it supports, the button is disabled and says
why. Each term card has:

| Field | What it does |
|---|---|
| **Fit type** | How the term is fitted (the fits in the table above). |
| **df mode**, **df** | Spline fits: **Auto** leaves the degrees of freedom to RustyStats, which chooses the smoothing (the Summary lists each smooth term's effective degrees of freedom); **Fixed** takes a **df**, an integer up to 20 and at least degree + 1 for B-splines or at least 2 for natural and monotone splines. |
| **Degree** | B-spline and monotone spline: the polynomial degree (3 unless set). |
| **Advanced** | Spline fits: **Basis size (k)** (Auto only), **Interior knots** (Fixed only; they replace df) and **Boundary knots**. Set only one of df, k and knots. Categorical fits: **Reference level** and **Levels** (below). Target encoding: **Permutations**, from 1 to 100 (default 4). |
| **Monotonicity** | Linear, B-spline, monotone spline and expression terms: increasing or decreasing. |
| **Reference level**, **Levels** | A categorical fit shares the intercept with its first level in sorted order. **Reference level** chooses that baseline level; **Levels** fits indicators only for the listed levels. They replace each other, and the rest (and levels unseen at scoring) share the intercept. Training refuses a reference or listed level the data does not contain and lists the observed labels. Quote numeric labels exactly as the column holds them, for example `"2"` for an integer column (or `"2.0"` if it also has nulls). |
| **Prior weight**, **Weight** | Target encoding: **Auto** leaves the prior weight to RustyStats; **Fixed** takes a non-negative **Weight**. |
| **Name**, **Expression** | Expression terms: the term's name and its expression. Expressions have one of the forms `x`, `x ** n`, `x + y`, `x - y`, `x * y`, or `x / y`, where `y` is a column or a number. Columns in an expression must be numeric and named with letters, digits, and underscores, starting with a letter or underscore; compute logs and other transforms in an upstream Transform node. |

Terms that cannot be fitted (a role column, a column no longer upstream, an unsupported
dtype, or an entry with no fit type) are listed under **Unresolved terms** with the reason.
Fix or remove them before training. Training is also blocked until at least one feature has
a term (**Add a term to at least one feature.**).

**INTERACTIONS**

**Add interaction** adds a card. Pick at least two features with the feature lists and
**+ feature**, then choose the card's **Fit type**:

- **Product** fits the features' product. Each feature fits as its override on the card
  (**Linear**, **Categorical**, **B-spline**, **Nat. spline** or **Target enc.**) if you set
  one; otherwise as its main-effect term, when it has exactly one that interactions honour;
  otherwise as its dtype default.
- **Target enc.** and **Frequency enc.** encode the combination of the raw feature values
  (for example brand and region). Joint encodings accept integer, boolean, and categorical
  features, and target encoding takes **Prior weight** and **Permutations**.

A monotone spline, a monotone linear or B-spline term, a categorical term with **Levels** or
a **Reference level**, a frequency encoding, or several main-effect terms for one feature
cannot be used inside an interaction, so the card asks for an explicit fit. Categorical fits
apply only over a categorical main effect, and linear, spline, and target-encoding fits never
do. A categorical feature may use **Target enc.** when every other feature is **Linear**;
this always adds the target-encoded main effect, even with **Include main effects** off.
Different cards may fit the same feature with different local splines.

**Include main effects** adds a main effect for each feature that has none, using the fit its
cards agree on; the feature list tags that feature "Main effect from Interaction N". Cards
that would add different main effects for one feature are refused together: add a main term
for it, or give the cards the same fit. Features with a term keep it. Product, target-encoded,
and frequency-encoded interactions can coexist over the same factors; a duplicate within one
mode shows **Duplicate interaction: another card fits these features the same way.**

## The PARAMETERS pane

For CatBoost, XGBoost, LightGBM, t-boost and EBM, **Parameter strategy** chooses between
**Fixed parameters** and **Tune parameters**. The GLM's version is described in
[GLM penalty and solver](#glm-parameters) below.

**Fixed parameters**

**Parameters JSON** holds the family's fixed parameters as a JSON object. A new node starts
from:

| Family | Starting **Parameters JSON** |
|---|---|
| CatBoost | `iterations` 1000, `learning_rate` 0.05, `depth` 6, `l2_leaf_reg` 3, `early_stopping_rounds` 50, `one_hot_max_size` 10 |
| XGBoost | `num_boost_round` 1000, `eta` 0.1, `max_depth` 6, `early_stopping_rounds` 50 |
| LightGBM | `num_iterations` 1000, `learning_rate` 0.05, `num_leaves` 31, `early_stopping_round` 50 |
| t-boost | `n_trees` 4000, `max_interaction_order` 3 (every other setting is t-boost's own recommended recipe) |
| EBM | `max_rounds` 2000, `learning_rate` 0.02, `interactions` 10 |

Invalid JSON, or a key Haute sets itself, shows **Parameters JSON:** with the reason under
the box and blocks training. Haute sets the objective, threads, seed, offset and categorical
handling: choose the loss in the **TARGET** pane and monotonicity in the **FEATURES** pane.
For CatBoost, GPU training is set in the **TRAIN** pane rather than here.

??? info "CatBoost parameters"
    Any CatBoost constructor parameter can go in **Parameters JSON**, except the ones Haute
    sets. The starting keys are:

    | Key | Description |
    |---|---|
    | `iterations` | Maximum boosting rounds; tuning uses this as its ceiling |
    | `depth` | Tree depth |
    | `learning_rate` | Step-size shrinkage: smaller values are slower but often more accurate |
    | `l2_leaf_reg` | L2 regularisation of leaf values |
    | `early_stopping_rounds` | Stop a validation fit when its metric stops improving |
    | `one_hot_max_size` | Categorical columns with up to this many levels are one-hot encoded; above it CatBoost uses target statistics, which are much slower to train. The **TRAIN** pane's run summary shows the resulting **Categorical encoding**. |

??? info "XGBoost parameters"
    XGBoost trains CPU histogram trees. **Parameters JSON** accepts `num_boost_round`,
    `early_stopping_rounds`, `eta`, `max_depth`, `max_leaves`, `grow_policy`,
    `min_child_weight`, `gamma`, `max_delta_step`, `subsample`, `colsample_bytree`,
    `colsample_bylevel`, `colsample_bynode`, `lambda`, `alpha`, `max_bin`,
    `max_cat_to_onehot` and `max_cat_threshold`. Aliases such as `learning_rate` or
    `n_estimators` are refused with the canonical name; Haute sets the objective, threads,
    seed, categorical handling and offset. Categories are encoded against the levels the
    model was trained on, so a value the model never saw fails instead of scoring as
    missing.

??? info "LightGBM parameters"
    LightGBM trains CPU leaf-wise trees. **Parameters JSON** accepts `num_iterations`,
    `early_stopping_round`, `learning_rate`, `num_leaves`, `max_depth`, `min_data_in_leaf`,
    `min_sum_hessian_in_leaf`, `feature_fraction`, `bagging_fraction`, `bagging_freq`,
    `lambda_l1`, `lambda_l2`, `min_gain_to_split`, `max_bin`, `max_cat_to_onehot`,
    `max_cat_threshold`, `cat_smooth`, `cat_l2` and `min_data_per_group`. LightGBM itself
    silently accepts conflicting aliases, so Haute refuses every alias (for example
    `num_boost_round` or `eta`). A fit that runs out of useful splits before its round
    budget records `native_exhaustion` as its stopping reason. LightGBM cannot apply
    monotone constraints with the MAE loss.

??? info "t-boost parameters"
    A t-boost model is gradient boosting on symmetric trees whose fitted model is exactly a
    set of rating tables: one table per main effect and per interaction, each coupling at
    most `max_interaction_order` features (3 to start). The prediction is a base value plus
    one value from each table, with no approximation, so the tables you read in the result
    are the model that scores. Early stopping fits the model: when a fit has validation rows
    (from the **SPLIT** pane), every bagged fit (`n_bags`) stops at its own best round on
    them (`early_stopping_rounds`), and the validation rows never train, prune or shape the
    tables. Untick **Refit on training + validation** to publish exactly that early-stopped
    model. A fit without validation rows, such as the refit on training plus validation,
    holds out a share of its own rows to stop on (`validation_fraction`); no round count is
    carried over from the validation fit. t-boost also prunes tables that do not improve its
    own held-out deviance (`prune`). `n_trees` is the ceiling every fit stops early within,
    and is required. When the **SPLIT** pane groups rows by an entity column, t-boost's own
    holdouts keep each entity on one side. A fit gives the same model whatever the number of
    threads. The **Loss** view draws each round's training and early-stopping deviance,
    averaged over the bags still boosting.

    **Parameters JSON** also accepts `learning_rate`, `lambda_`, `max_depth`,
    `max_interaction_order`, `max_bin`, `min_data_in_leaf`, `min_sum_hessian_in_leaf`,
    `min_split_gain`, `l1_leaf`, `path_smooth`, `colsample_bytree`, `subsample`, `n_bags`,
    `bag_subsample`, `validation_fraction`, `early_stopping_rounds`,
    `early_stopping_adaptive`, `leaf_refine_steps`, `interaction_gain_hurdle`, `prune`,
    `prune_se_rule`, `prune_n_folds`, `prune_min_stability`, `cat_smooth`,
    `cat_min_data_per_group` and `cat_direct_max_levels`. t-boost checks their values itself
    when training starts.

    Under Poisson, Gamma or Tweedie the offset is an exposure: the model learns a rate and
    scores the rate times the offset. Under RMSE the offset is added to the prediction as it
    is. Logloss takes no offset, and MAE and CrossEntropy are not available. A categorical
    value the model never saw, such as a make that only appears in the validation rows or
    arrives after training, never fails: t-boost scores it in its pooled rare level, like the levels too thin to model alone (the default level on a feature where nothing was pooled). t-boost scores numeric features as 32-bit floats. The `.tboost` file is t-boost's own JSON model with
    Haute's record in its metadata, so it describes its own inputs and offset, and plain
    t-boost (0.8 or later) can read it too.

??? info "EBM parameters"
    An Explainable Boosting Machine is a sum of one learned shape per feature plus chosen
    pairwise interactions, so the model is readable term by term. EBM never stops early:
    every fit trains on its own training rows only (validation rows never enter an EBM
    fit), for exactly `max_rounds` rounds, which is required (training is blocked until it
    is a positive whole number). **Parameters JSON** also accepts `learning_rate`,
    `interactions`, `max_bins`, `max_interaction_bins`, `min_samples_leaf`, `min_hessian`,
    `max_leaves`, `smoothing_rounds`, `interaction_smoothing_rounds`, `greedy_ratio`,
    `cyclic_progress`, `reg_alpha`, `reg_lambda`, `max_delta_step`, `gain_scale`,
    `min_cat_samples`, `cat_smooth` and `missing`.

    `interactions` is either a count (EBM picks up to that many of the strongest pairs) or
    a list of feature pairs; the **FEATURES** pane's **Pairwise interactions** section sets
    it for you. A monotone-constrained feature cannot take part in an interaction. MAE is
    not available.

    An `.ebm` file is loaded only with the feature contract saved beside it, and only
    under the exact `interpret-core` version that contract records; upgrading
    `interpret-core` past a minor version means retraining.

**Tune parameters**

Every family except the GLM can tune a bounded search over the validation plan set in the
**SPLIT** pane. Choosing **Tune parameters** hides **Parameters JSON**, ticks **Refit on
training + validation** in the **SPLIT** pane (tuning requires the refit) and shows:

- A note saying whether a test set is held out during tuning (**No test set is reserved.
  Reserve one in Split for an independent evaluation.** otherwise), with **Review split →**,
  and the fit budget, for example **101 total fits: 20 trials × 5 validation fits + 1 final
  fit.**
- **Trial count**: how many trials to run, from 5 to 50 (20 to start). Trial zero is always
  your current fixed parameters, so the search must beat the model you would otherwise
  train.
- **Seed**: the sampler's seed (42 to start).
- **Selection metric**: the metric that picks the winner, from the metrics switched on in
  the **TARGET** pane (the first of them to start).
- **Search space JSON**: the values to search, keyed by the family's own parameter names.
  It starts from a small family-specific search: CatBoost `depth`, `learning_rate` and
  `l2_leaf_reg`; XGBoost `max_depth`, `eta` and `lambda`; LightGBM `num_leaves`,
  `learning_rate` and `min_data_in_leaf`; t-boost `learning_rate`,
  `max_interaction_order` and `lambda_`; EBM `max_rounds`, `learning_rate` and
  `interactions`.

For an ordinary search entry, list every candidate value directly. Values keep their JSON
type, so the engine receives numbers, strings, or Booleans exactly as written. A conditional
entry uses `{"choices": [...], "when": {...}}` instead (see **In the pipeline file** below).
Haute rejects unknown fields, lists outside two through fifty distinct finite values, invalid
or cyclic conditions, and keys Haute owns, such as a tree family's round budget,
loss/objective, device, threads, callbacks, write directories, or random seed. Training is
blocked until tuning is complete: 5–50 trials, a selection metric, a non-empty search space,
and at most 200 trial-validation fits in total.

Tuning needs holdout validation or cross-validation and evaluates every trial in turn with a
deterministic seeded sampler. The selected parameters are refitted once on all development
data; the test set, when there is one, is then evaluated once. A tree family refits with the
winner's validation-weighted round count (the **final tree count**); an EBM refits with the
winner's `max_rounds` unchanged, and t-boost with the winner's parameters unchanged (the
refit stops early on its own holdout). Choosing **Fixed parameters** again turns tuning off.

### GLM penalty and solver { #glm-parameters }

A GLM has no tuning. Its **PARAMETERS** pane holds the penalty and the solver:

| Field | What it does |
|---|---|
| **Regularization** | **None** (the default), **Ridge**, **Lasso** or **Elastic Net**. It cannot be combined with automatically smoothed splines: the pane says which splines conflict and asks you to set **Fixed** df on them or turn regularisation off. |
| **Penalty** | With regularisation: **Cross-validated** chooses the penalty strength on held-out folds of the training data; **Fixed** uses the alpha you enter. |
| **Folds** | Cross-validated penalty: the number of folds, from 2 to 20 (5 to start). |
| **Selection rule** | Cross-validated penalty: **Minimum deviance** (the default) or **One standard error** (the largest penalty within one standard error). |
| **Alpha** | Fixed penalty: a positive number (1 to start). |
| **L1 ratio** | Elastic Net only, and required: a slider from 0 (ridge) to 1 (lasso), 0.5 to start. |
| **Solver** | Opens the solver settings: **Maximum iterations** (1 to 10000) and **Tolerance** (between 0 and 1); leave them blank for RustyStats' defaults. **Robust standard errors** is **Off** or **HC0**, **HC1**, **HC2** or **HC3** heteroskedasticity-robust standard errors. They cannot be combined with regularisation, monotonicity constraints, or automatically smoothed splines, whose standard errors are not valid; the pane names the conflict. |

A cross-validated penalty also records a seed (42) so the same data selects the same
penalty; it has no field in the pane.

## The SPLIT pane

The **SPLIT** pane decides which rows the model is trained, validated and tested on. It
separates three roles:

- **Development data** is available for model selection and the final refit.
- **Validation data** estimates candidate settings: a holdout validation set,
  cross-validation, or no validation.
- An optional **test set** remains unseen until model selection is complete and is
  evaluated exactly once.

| Field | What it does |
|---|---|
| **Row limit** | Trains on a seeded random sample of at most this many rows. Blank (**All rows**) uses every row. |
| **Split strategy** | **Random split** (the default), **Group split** or **Time-based split**. |
| **Group column** | Group split: the column whose rows must stay together, such as a customer, policyholder, household, or claim identifier. |
| **Date column** | Time-based split: the date the split boundaries apply to. |
| **Validation strategy** | **Holdout validation** (the default), **Cross-validation** or **No validation**. |
| **Validation set (%)** | Holdout validation with a random or group split: the share of source rows held out, above 0 and below 100 (20 to start). |
| **Validation starts** | Holdout validation with a time-based split: rows from this date on form the validation set. |
| **Fold count** | Cross-validation: from 2 to 10 folds (5 to start). With a time-based split the folds use an expanding window. |
| **Refit on training + validation** | Holdout validation only, ticked by default: refits the final model on all development rows. Untick it to keep the one model trained on the training rows during validation, with no second fit; without a test set, its diagnostics are then labelled as validation diagnostics. Cross-validation and no validation always perform their final fit, and tuning requires the refit, so the box is ticked and locked while **Tune parameters** is on. |
| **Test set (%)** | Random or group split: the share of source rows reserved as the test set, from 0 (no test set) to below 100. |
| **Test starts** | Time-based split: rows from this date on form the test set; leave it blank for none. |

Above the strategies, a bar shows the **Target allocation** of training, validation and test
rows. Once the split settings are complete and the pipeline's rows can be counted, it becomes
the **Exact allocation**: the planned development and test row counts and the range of
training and validation rows per fit. A time-based split shows **Awaiting preview** until the
counts arrive.

How the strategies split the rows:

- **Random split**: classification is stratified by the target; regression is seeded but
  unstratified. Every requested partition must contain rows.
- **Group split**: the planner assigns complete groups while balancing row counts, so rows
  for the same entity never appear in different partitions. The group column is kept as
  split metadata and is not offered as a feature.
- **Time-based split**: explicit date boundaries, and a later date never appears in a
  validation fit's training data. Equal dates always stay together; null or invalid dates
  fail with an actionable error.

With **No validation**, Haute performs one final fit and tuning is unavailable. The pane shows
the reason when the split cannot be used: **Validation and test must total below 100% so
training retains some rows.**, **Validation must start before the test set.**, or **Complete
the split settings: split strategy, validation strategy, and required group/date fields.**

## The TRAIN pane

**Run summary** lists what will train: **Model** (family and loss), **Target**, **Inputs**
(the feature count), **Categorical encoding** (CatBoost with categorical features),
**Evaluation** (the validation strategy and the allocation), **Fit budget** (the total fits,
and for a cross-validated GLM penalty the internal folds each fit uses) and **Compute**
(**CPU** or **GPU (CUDA)**).

**GPU training**

CatBoost trains on a GPU when its **GPU training** box is ticked. XGBoost trains on an
NVIDIA GPU when its **GPU training** box is ticked; the box names the device once the server
has found one. LightGBM, t-boost, EBM and the GLM train on the CPU only.

Haute installs XGBoost's CPU-only build, so XGBoost trains on the CPU with no extra
setup. GPU training is an optional extra: if you want it, run `haute gpu-setup` once in the
project (see
[Installing Haute](../../getting-started/installing-haute.md#xgboost-gpu-training)) and
restart `haute serve`. Until the server can train on a CUDA GPU, the XGBoost box is
disabled and says why.

XGBoost never falls back to the CPU silently. A GPU training request fails before the fit
when no GPU can be used, and fails afterwards if XGBoost trained somewhere else; nothing is
saved in either case. The run summary shows **GPU (CUDA)** and the result's fit evidence
records the device used (for example `cuda:0`). Before launch, Haute estimates the GPU memory
the data needs and refuses a job that does not fit.

A GPU model is saved for CPU scoring, so it scores the same in every deployment,
including ones with only the CPU build. A GPU fit is a different model from a CPU fit with
the same settings: XGBoost's device algorithm finds different splits, so the predictions
differ. Switching the device marks the last result as out of date.

**Memory estimate**

Before training, the pane estimates the RAM the data needs (**Estimating dataset size...**
while it counts):

- **Dataset fits in memory** shows the **Source rows**, the **Est. training RAM** and the
  **Available RAM** (and, for a GPU fit, **Est. GPU VRAM** against the **GPU VRAM** free).
- **Will downsample** means the data would not fit: training uses a seeded random sample of
  rows capped at a limit that fits, shown as **Training rows**, and the result records the
  downsampling. A **Row limit** you set in the **SPLIT** pane still applies when it is lower.
- **Row count not proven** appears when the data passes through a join that has no key
  contract: the pane shows the upper bound instead of a verdict, and an **Open "…"** button
  for each such join. Declaring the join many-to-one bounds the rows by its base input.
- **Memory estimate unavailable** appears when the rows or columns reaching the node cannot
  be known before it runs.

When a GPU fit would need more memory than the GPU has free, the pane says so and suggests
selecting the CPU or reducing rows or features.

**Training**

- **Train Model** (or **Tune & Train** while tuning is on) trains the model. While Haute
  collects the data the button reads **Preparing training data...**; then it shows the
  progress message. If anything blocks training, a **Complete before training** list names
  each problem with a **Go to …** link to the pane that fixes it.
- **Cancel training** stops a run in progress.
- While a CatBoost, XGBoost or LightGBM model trains, the pane draws its loss curve live,
  for every fit in the run: each validation fit, cross-validation fold and tuning trial,
  then the final fit. Each fit starts a fresh chart whose axes are set from its first round:
  rounds from 0 to the fit's round budget, and loss from 0 to a little over the starting
  loss, so the curve fills in as the fit trains. A tuned run also shows the trial and fold
  it is on and the best objective so far.
- **Config changed since last training** appears when you change a training setting after
  training, with **Re-train** to train again.

A finished run shows **Model trained - results in preview panel below**; a failed or
cancelled run shows **Training failed** or **Training cancelled** with the reason.

## The EXPORT pane

The **EXPORT** pane acts on the node's last completed training result. Nothing is logged or
saved automatically. Both actions stay disabled until the model has been trained and while a
new training run is in progress (**Training is running - export is available when it
completes.**). If you change training settings after training, the pane warns **Training
settings changed since this model was trained. Exports use the last trained model.**

**MLFLOW LOGGING**

| Field | What it does |
|---|---|
| MLflow destination | **Databricks**, **MLflow server** or **Local folder** (the default, the project's local MLflow folder). A remote that is not configured is greyed out, and clicking it opens MLflow settings instead. A node keeps its choice even when other destinations are configured later. |
| **Experiment path** | The MLflow experiment the run is logged into. On Databricks it is a workspace folder path; on an MLflow server or local folder it is a plain name. Leave it blank to use the default shown in the box: the node's name, or `/Shared/haute/<node name>` on Databricks. |

- **Log run to MLflow** logs the run. If the chosen destination is unavailable, the pane says
  why with a **Configure MLflow** link. If the destination cannot be reached or rejects its
  credentials, the pane shows why with **Test connection in MLflow settings**, and nothing is
  logged anywhere else instead.
- After a log, the pane shows **Last logged to …** with **Open run**, and the button becomes
  **Log again**. Logging a result that is already logged asks first
  (**Log as a new run** or **Cancel**) and creates a new run.
- If a log fails because the response never arrived, **Retry** repeats the same log rather
  than risking a duplicate run.

A logged run follows Haute's candidate-run contract so a separate promotion process can
find and compare it: tags such as `haute.node_id`, `haute.trained_at` and
`haute.evaluation_plan_sha256`, metrics named by evaluation set (`final_test_gini`,
`development_gini`, `selection_gini_mean`), and the model, feature contract and evaluation
evidence as artifacts.

!!! note "Registering and promoting models"
    Haute logs candidate runs but never registers a trained model. Registering a run in the MLflow model registry, and promoting it (for example after comparing it with the current champion and moving an alias), is a separate process outside Haute. A [Model Scoring](model-score.md) node can then load the registered version or alias, or score a logged run directly.

**MODEL FILE**

**FILENAME OR PATH** chooses where **Save model to file** writes a copy of the trained model
and its feature contract, the same way a Data Output writes a file: type a name or pick a
file. A bare filename saves in the project's `models/` folder, paths are relative to the
project root, and the model's extension (`.cbm`, `.ubj`, `.lgbm`, `.ebm` or `.rsglm`, by
family) is added if you leave it off. The pane shows the **Destination:** before you save,
refuses an extension that does not match the model format, and asks before replacing a file
that already exists (**Replace existing file**). After saving it shows where the model and
its **Feature contract** went. Keep an `.ebm` file's feature contract beside it: the model
cannot load without it.

The pane remembers where the result was last logged and saved, including after you reload
the page while the server still holds the training result. After a server restart the pane
says the result is no longer available and asks you to train again.

## Reading the result

The finished result opens in a results panel under the canvas, with these tabs; a tab
appears only when the result has something to show in it:

- **Summary**: the metrics, evaluation and tuning evidence described below.
- **Coefficients** and **Relativities** (GLM): each term's estimate and uncertainty, and
  its effect relative to a baseline of 1.
- **Terms** (EBM): each main effect's shape and each interaction's score table.
- **Tables** (t-boost): the model's rating tables.
- **Loss**: training and validation loss across iterations. After a holdout validation fit
  and a refit, it draws the validation fit and says which fit it is.
- **Lift**: how well predictions separate lower and higher outcomes.
- **Residuals**: prediction errors and actual against predicted.
- **Features**: feature importance, with a button per measure. **Prediction** is the
  model's own importance; **Loss** (CatBoost) is how much the loss worsens without each
  feature, which can be negative, and features are ranked by its size; **SHAP** (CatBoost, XGBoost, LightGBM, t-boost) is each feature's mean absolute SHAP
  value. **SHAP beeswarm** shows the top 20 of those features, one dot per sampled row:
  how far right or left a dot sits is how much that row's value pushed its prediction up
  or down (on the model's link scale), and its colour runs from blue for a low value to
  red for a high one. A categorical feature's dots have no value order and take one
  colour; point at a dot to see its row's value. SHAP is computed on a sample of up to
  5,000 diagnostics rows, and the beeswarm draws 2,000 of them.
- **AvE**: actual against expected across each feature's groups, with exposure.
- **PDP**: partial dependence, how predictions change as one feature varies.
- **SHAP curves** (CatBoost, XGBoost, LightGBM, t-boost): for each feature, the average SHAP value
  of the sampled rows in each band of its values (up to 20 bands) or in each of its 30
  most common levels, with a shaded range from the 10th to the 90th percentile and the
  rows with a missing value shown on their own. For a Poisson, Gamma or Tweedie loss the
  curve reads as a relativity around 1.0, like a GLM's relativities; for other losses it
  shows the average SHAP value around 0. Unlike PDP, which sets every row to the same
  value, it only uses the values the rows really have.

In the **AvE**, **PDP**, **SHAP curves** and **SHAP beeswarm** charts, point at a bin, point,
band, level or dot, or move to it with the Tab key, and the line under the chart shows its
exact values; a closed table under each chart lists every value. In **Terms**, point at a
bin to see its score.

The Summary keeps model-selection evidence distinct from final performance:

- **Test metrics** are the performance on the untouched test set, when one was reserved.
- **Test diagnostics**, **Validation diagnostics** or **Training diagnostics** are named
  after the rows they were evaluated on. Without a test set they are usually training
  diagnostics, which are in-sample performance and are marked as such.
- When a validation fit ran and no test set was reserved, the Summary leads with a
  **Validation, N rows** card (**Validation (K-fold mean), N rows** under
  cross-validation): the out-of-sample metrics used to select the model. The collapsed
  results bar shows the same metrics.
- **Candidate selection** holds the holdout or cross-validation metrics used to compare
  fixed and tuned candidates. Cross-validation summaries are weighted by the number of
  validation rows in each fit.
- A tuned run shows the baseline, winning trial, improvement, selected parameters,
  final tree count (tree families) or the winning round budget (EBM), and exact total
  fit count. **Use best as fixed parameters** copies the winning parameters into the
  node's **Parameters JSON** and turns tuning off, after asking.
- An EBM's **Terms** view shows each main effect's shape (including the score for
  missing values) and each interaction's score table, as additive scores on the model's
  link scale (log for Poisson, Gamma and Tweedie; log-odds for Logloss): the prediction is
  the intercept plus every term's score. They are the model itself, not SHAP values.
- A t-boost result's **Tables** view lists the model's rating tables, most important
  first, with the model's **base** value. The prediction is the base plus one value from
  every table (for Poisson, Gamma and Tweedie, the base times one relativity from every
  table, times the exposure when the model has an offset), so the tables are the model
  itself. Under those log-link losses the view shows relativities, with **Link scale** to
  switch to the additive log-scale values; for RMSE and Logloss it shows link-scale values
  (log-odds for Logloss). A main effect is drawn over its cells: a bar per group of levels
  (levels t-boost cannot tell apart share a cell, and missing values are their own level),
  or a step line over numeric ranges, each range including its upper bound, with the value
  for missing numbers stated above it. A two-feature table is a value grid; for a table of
  three or more features, **Rows** and **Columns** choose the grid's features and a list
  per remaining feature chooses its cell. Point at a cell to see its value and its
  training mass: the sum of weight times exposure over the training rows in it (the
  exposure is the offset under Poisson, Gamma and Tweedie; an RMSE offset does not count),
  which is the exposure when the weight or offset is the exposure. A factored effect, one too large
  for a dense table, is listed by name and importance only. **Importance** is each table's
  share of the model's variance, and the **SHAP** importances and curves are t-boost's
  exact Shapley values (an interaction shared equally among its features).
- A GLM shows its fit statistics, the penalty actually applied (with the folds, rule,
  and seed when it was cross-validated), and each automatic spline's effective degrees
  of freedom. Standard errors and p-values are valid only for an unpenalised,
  unconstrained fit without automatic splines; otherwise the **Coefficients** view
  shows dashes and says why. Relativities (exponentiated coefficients) exist only for
  log-link models, and a relativity too large to compute is reported as a diagnostic
  error naming the terms.

The model, feature contract, evaluation plan, results and report, and the optional tuning
plan, trials and report are published together as that training result's own files, so a
later training run never changes what an earlier result exports. MLflow logging attaches
the same evidence and selected final parameters to one final run.

## Example

A Poisson claim-frequency GLM with an exposure offset:

1. Add a Model Training node, connect your training data to it and click **GLM**.
2. In the **TARGET** pane, set **Target column** to `claim_count`, click **Poisson** under
   **Family** (the link stays **auto (log)**), and set **Offset column (optional)** to
   `exposure`.
3. In the **FEATURES** pane, click **Add term** on `driver_age` and set its **Fit type** to
   **B-spline** with **df mode** **Fixed** and **df** 5. Add `vehicle_age` as **Linear** with
   decreasing monotonicity, and `area` as **Categorical** with **Reference level** `urban`
   (under **Advanced**).
4. Under **INTERACTIONS**, click **Add interaction**, pick `driver_age` and `area`, leave
   **Fit type** on **Product**, set `driver_age`'s fit on the card to **Linear** and `area`'s
   to **Categorical**, and keep **Include main effects** ticked. `area` needs its own fit
   because its main effect's reference level does not apply inside an interaction.
5. In the **PARAMETERS** pane, choose **Ridge** with a **Cross-validated** penalty,
   **Folds** 5 and **Selection rule** **Minimum deviance**.
6. In the **TRAIN** pane, check the run summary and click **Train Model**. The results panel
   opens with the **Summary**, **Coefficients** and **Relativities** tabs.

??? note "In the pipeline file"
    The node's settings are stored in a JSON sidecar, `config/model_training/<node name>.json`,
    which the pipeline's `.py` file names in the node's decorator:
    `@pipeline.modelling(config="config/model_training/<node name>.json")`.

    | Setting in the editor | Stored as |
    |---|---|
    | **Select algorithm** | `algorithm`: `"catboost"`, `"xgboost"`, `"lightgbm"`, `"tboost"`, `"ebm"` or `"glm"` |
    | **Target column** | `target` |
    | **Objective** | `loss_function` (the loss name, for example `"Poisson"`), and `task`: `"regression"` or `"classification"` |
    | **Variance power** (tree, t-boost and EBM Tweedie) | `variance_power` |
    | **Positive class** | `positive_class` (a Boolean, an integer or a string label) |
    | **Weight column (optional)** | `weight` |
    | **Offset column (optional)** | `offset` |
    | **Metrics** | `metrics`: `"gini"`, `"rmse"`, `"mae"`, `"mse"`, `"r2"`, `"auc"`, `"logloss"`, `"poisson_deviance"`, `"tweedie_deviance"`, `"gamma_deviance"` |
    | Feature tick boxes | `feature_columns` (the ticked columns; tree, t-boost and EBM families) |
    | **Monotonicity** | `monotone_constraints`, a map of column to `-1` or `1` (tree, t-boost and EBM families) |
    | **Pairwise interactions** | `params.interactions` (EBM) |
    | **Parameters JSON** | `params` |
    | **Tune parameters** | `tuning` (absent while **Fixed parameters** is chosen) |
    | **Row limit** | `row_limit` |
    | **SPLIT** pane | `evaluation` (see below) |
    | **Refit on training + validation** | `refit_on_development` (absent means ticked) |
    | **GPU training** (CatBoost) | `params.task_type`: `"GPU"` |
    | **GPU training** (XGBoost) | `device`: `"gpu"` (absent means CPU) |
    | MLflow destination | `mlflow_destination`: `"databricks"` or `"server"`; absent for the local folder |
    | **Experiment path** | `mlflow_experiment` (absent or blank uses the default) |
    | **FILENAME OR PATH** | `model_export_path` |
    | **Family** (GLM) | `family`: `"gaussian"`, `"poisson"`, `"quasipoisson"`, `"binomial"`, `"quasibinomial"`, `"gamma"`, `"tweedie"` or `"negbinomial"` |
    | **Link Function** (GLM) | `link`: `"identity"`, `"log"` or `"logit"`; blank for the canonical link |
    | **Variance power** (GLM Tweedie) | `var_power` |
    | **Dispersion theta** (GLM) | `theta` |
    | **Intercept** (GLM) | `intercept` (defaults to true) |
    | Term cards (GLM) | `terms`, a map of term name to term spec |
    | Interaction cards (GLM) | `interactions`, a list of cards |
    | **Regularization** (GLM) | `regularization`: `"ridge"`, `"lasso"` or `"elastic_net"`; absent for **None** |
    | **Penalty**, **Alpha** (GLM) | `alpha`: a positive number fixes the penalty; absent or 0 cross-validates it |
    | **Folds**, **Selection rule** (GLM) | `cv_folds` (2 to 20), `cv_selection` (`"min"` or `"1se"`) |
    | **L1 ratio** (GLM) | `l1_ratio` |
    | **Maximum iterations**, **Tolerance** (GLM) | `max_iter`, `tol` |
    | **Robust standard errors** (GLM) | `robust_standard_errors`: `"HC0"`, `"HC1"`, `"HC2"` or `"HC3"` |

    These keys have no editor control:

    | Key | What it does |
    |---|---|
    | `name` | Names the training artifacts; training uses the node's id when it is absent. |
    | `feature_weights` | CatBoost feature weights. |
    | `fold_column`, `id_columns` | Columns kept as training metadata rather than features. |
    | `output_dir` | Where training writes its working files (`outputs` by default). |
    | `cv_seed` | The GLM penalty's cross-validation seed; the editor writes 42 when you choose a regularisation. |
    | `evaluation.seed` | The split seed; the editor writes 42. |
    | `evaluation.validation.window` | `"expanding"` for time-based cross-validation; the editor sets it. |
    | `categorical_levels` | Declared category levels for categorical feature columns. |

    `feature_columns` and `monotone_constraints` apply to the tree and EBM
    families (`feature_weights` to CatBoost only) and are ignored for a GLM, whose features
    are its terms and interaction factors. A node file with the removed `exclude` key is
    refused when the pipeline loads; list the features in `feature_columns` instead.

    **Evaluation.** Every node has one versioned `evaluation` object. The retired top-level
    `split` and `cross_validation` fields are not accepted. A random split reserving 20% of
    source rows as a test set, with five validation folds, then one refit on all development
    rows:

    ```json
    {
      "evaluation": {
        "schema_version": 1,
        "strategy": "random",
        "seed": 42,
        "test": {"size": 0.2},
        "validation": {"method": "cross_validation", "fold_count": 5}
      }
    }
    ```

    A group split uses `"strategy": "group"` with a `group_column`, and a time-based split
    uses `"strategy": "temporal"` with a `date_column` and date boundaries, for example
    `"test": {"start": "2025-01-01"}`. Fractions are source-relative numbers from 0
    (inclusive) to 1 (exclusive). The `validation` shapes are:

    | Shape | Behaviour |
    |---|---|
    | `{"method": "none"}` | No candidate validation; perform one final fit. Tuning is unavailable. |
    | `{"method": "single", "size": 0.2}` | One random or group validation fit using a source-relative fraction. |
    | `{"method": "single", "start": "2024-07-01"}` | One time-based validation fit at an explicit boundary. |
    | `{"method": "cross_validation", "fold_count": 5}` | Two to ten random or group validation fits. |
    | `{"method": "cross_validation", "fold_count": 5, "window": "expanding"}` | Two to ten expanding time-based validation fits. |

    **Tuning.** `trial_count` includes the baseline and must be 5–50; total trial-validation
    fits may not exceed 200. A search entry is either a list of values or a conditional
    entry:

    ```json
    {
      "tuning": {
        "schema_version": 1,
        "trial_count": 20,
        "seed": 42,
        "metric": "gini",
        "search_space": {
          "depth": [4, 6, 8, 10],
          "learning_rate": [0.01, 0.03, 0.05, 0.1, 0.2],
          "grow_policy": ["SymmetricTree", "Depthwise"],
          "min_data_in_leaf": {
            "choices": [10, 25, 50, 100],
            "when": {"grow_policy": ["Depthwise"]}
          }
        }
      }
    }
    ```

    **GLM terms and interactions.** GLM settings sit directly on the node config, not inside
    `params`. The example above is stored as:

    ```json
    {
      "algorithm": "glm",
      "task": "regression",
      "target": "claim_count",
      "offset": "exposure",
      "family": "poisson",
      "terms": {
        "driver_age":   { "type": "bs", "df": 5 },
        "vehicle_age":  { "type": "linear", "monotonicity": "decreasing" },
        "area":         { "type": "categorical", "reference": "urban" }
      },
      "interactions": [
        { "factors": ["driver_age", "area"], "specs": {"driver_age": {"type": "linear"}, "area": {"type": "categorical"}}, "include_main": true }
      ],
      "intercept": true,
      "regularization": "ridge",
      "cv_folds": 5,
      "cv_selection": "min",
      "cv_seed": 42
    }
    ```

    A native term is keyed by the column it fits; an `"expression"` term
    (`{"type": "expression", "expr": "age ** 2"}`) or a second encoding
    (`{"type": "frequency_encoding", "variable": "area"}`) is keyed by a name that is not a
    column. Term types are `linear`, `categorical`, `bs`, `ns`, `ms`, `target_encoding`,
    `frequency_encoding` and `expression`; spline settings are `df`, `degree`, `k`, `knots`
    and `boundary_knots`, categorical settings `reference` and `levels`, target-encoding
    settings `prior_weight` and `n_permutations`, and `monotonicity` is `"increasing"` or
    `"decreasing"`. An interaction has `factors` (two or more columns), `include_main`,
    optional per-feature `specs`, and `encoding`: absent for **Product**, or
    `"target_encoding"` or `"frequency_encoding"`:

    ```json
    {
      "factors": ["brand", "region"],
      "encoding": "target_encoding",
      "include_main": false
    }
    ```

**See also:**

- [Model Scoring](model-score.md)  - to score data with your trained model
- [Execution Strategy](../execution-strategy.md)  - how training data is collected and diagnosed

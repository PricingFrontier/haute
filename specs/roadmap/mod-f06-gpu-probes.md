# MOD-F06 GPU probes — 23 September 2026

Evidence for the GPU capability of the model-family expansion, since delivered
and retired from the [modelling roadmap](modelling.md). This is a dated record; the
[modelling specification](../modelling/high-level.md#model-families) is the
behaviour authority.

## Method

[`mod-f06-gpu-probes.py`](../../scripts/benchmarks/mod-f06-gpu-probes.py) fits real models on an NVIDIA
GeForce RTX 4070 Laptop GPU (8 GiB, driver 616.56) with 200,000 training and
50,000 validation rows × 21 features (20 numeric, one 30-level categorical), and
re-scores every GPU-trained XGBoost artifact in a second interpreter that has
only `xgboost-cpu`. It ran twice:

| Run | Platform | Results |
|---|---|---|
| Windows | Windows 11, Python 3.11 | [`mod-f06-gpu-probes.json`](../../scripts/benchmarks/mod-f06-gpu-probes.json) |
| Linux | Ubuntu under WSL2, Python 3.12 | [`mod-f06-gpu-probes-linux.json`](../../scripts/benchmarks/mod-f06-gpu-probes-linux.json) |

Both runs used XGBoost 3.2.0 (the full wheel, CUDA 12.9) and LightGBM 4.7.0.

## XGBoost (CUDA)

| Objective | Device peak (Windows / Linux) | GPU model scored on CPU build, max relative difference | GPU vs CPU fit, max relative difference |
|---|---|---|---|
| `reg:squarederror` | 104 / 132 MiB | 0 | 0.35 |
| `reg:absoluteerror` | 100 / 102 MiB | 0 | 0.22 |
| `count:poisson` | 88 / 102 MiB | 3.0e-8 | 0.16 |
| `reg:gamma` | 88 / 102 MiB | 1.3e-7 | 0.44 |
| `reg:tweedie` | 88 / 102 MiB | 3.0e-8 | 0.12 |
| `binary:logistic` | 102 / 102 MiB | 1.2e-7 | 0.14 |

Findings:

- Every objective Haute offers trains on the device, with categorical splits,
  a `base_margin` offset, sample weights and early stopping.
- **XGBoost never refuses a missing GPU.** A `cuda:7` fit on this one-GPU
  machine trains without error (`missing_device_fails: false`). With no visible
  GPU (`CUDA_VISIBLE_DEVICES=-1`), a `device="cuda"` fit trains on the CPU and
  only logs a warning; `test_a_hidden_gpu_fails_the_fit_instead_of_training_on_the_cpu`
  pins Haute's refusal of that case. A trained booster's
  `save_config()["learner"]["generic_param"]["device"]` records the device it
  really used, so Haute checks that value after the fit.
- **CPU serving holds.** A GPU-trained `.ubj` scores on `xgboost-cpu` within
  float32 rounding of the GPU's own prediction.
- **GPU and CPU fits are different models.** The same settings and seed give a
  maximum relative prediction difference of 12–44%, and different early-stopping
  rounds. The device therefore belongs in the training identity.
- A progress callback that raises stops a device fit at its next round
  (`callback_cancels_device_fit`), so cancellation works unchanged.
- Device memory peaked at 88–132 MiB, dominated by the CUDA context and
  allocator pools; Haute's estimate adds a 256 MiB base to a per-value and
  per-row term.
- Speed at this size is similar on Windows (0.9–3.3 s on either device). The
  WSL2 CPU fits were 29–99 s against 1.5–3.1 s on the GPU.
- The full `xgboost` wheel is about 139 MB on Windows, and on Linux it also
  pulls `nvidia-nccl-cu12`. It installs the same `xgboost` import package as
  `xgboost-cpu`, so one must be uninstalled before the other is installed.

The dogfooded `haute gpu-setup` command swapped `xgboost-cpu` 3.2.0 for
`xgboost` 3.2.0 in a fresh project environment. In that environment
`tests/test_xgboost_gpu.py` passes on the RTX 4070 with every real-device test
running. Those tests cover:

- every Haute loss under a monotone constraint;
- CPU serving of the saved Haute artifact. Its predictions equal the fitted model's
  before serialization, and match the native booster predicting on the GPU within
  float32 rounding. With `HAUTE_XGBOOST_CPU_PYTHON` pointing at the project's
  `xgboost-cpu` interpreter, as in this run, they are also identical when scored
  through Haute on the CPU-only install;
- a hidden GPU failing the fit instead of training on the CPU.

Writing the per-loss test exposed that `reg:absoluteerror` breaks monotone
constraints on the CPU as well as the GPU (it re-fits each leaf after the tree
is built; scored curves dipped by up to 55 on three CPU seeds). XGBoost
now refuses monotone constraints under `MAE`, as LightGBM already did.

## LightGBM

| Backend | Windows wheel | Linux wheel |
|---|---|---|
| `device_type="gpu"` (OpenCL) | "GPU Tree Learner was not enabled in this build." | Built in; "No OpenCL device found" under WSL2 |
| `device_type="cuda"` | "CUDA Tree Learner was not enabled in this build." | "CUDA Tree Learner was not enabled in this build." |

No published LightGBM wheel trains on CUDA. The only GPU route is OpenCL on
Linux, and it needs a vendor OpenCL runtime that this hardware probe could not
exercise. LightGBM GPU training is therefore not offered.

## EBM

InterpretML's EBM has no GPU training path. EBM stays CPU-only.

# Getting Started: Reproducible Local Demo

This walkthrough demonstrates AGF-Orchestrator's planning and governance posture without using credentials, a hosted model, or a third-party provider. It is intended for evaluators and first-time contributors.

## 1. Install AGF-Orchestrator

From a checkout of this repository:

```bash
python -m pip install -e .
python -m pip install pytest ruff
```

Confirm the project is healthy:

```bash
pytest
ruff check .
```

## 2. Create a disposable target repository

Use a temporary directory so the demo cannot affect a real project:

```bash
DEMO_DIR="$(mktemp -d)"
cd "$DEMO_DIR"
git init -b main
git config user.name "AGF Demo"
git config user.email "agf-demo@example.invalid"
printf 'def add(a, b):\n    return a + b\n' > calculator.py
printf 'from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n' > test_calculator.py
git add calculator.py test_calculator.py
git commit -m "Initial demo repository"
```

AGF preflight expects an `origin` remote. A local bare repository is sufficient; no network is required:

```bash
ORIGIN_DIR="$(mktemp -d)/agf-demo-origin.git"
git init --bare "$ORIGIN_DIR"
git remote add origin "$ORIGIN_DIR"
git push -u origin main
```

## 3. Generate a deterministic plan

From the AGF-Orchestrator checkout (or with the installed CLI available), run:

```bash
agf-orchestrator plan \
  --repository "$DEMO_DIR" \
  --goal "Add subtraction support with tests" \
  --output "$DEMO_DIR/../agf-demo-plan.json"
```

Planning is read-only. AGF should inspect repository context and emit a plan artifact without modifying the target repository.

Verify that the target is unchanged:

```bash
cd "$DEMO_DIR"
git status --short
```

The output should be empty.

## 4. Inspect the plan as governance evidence

Open the generated JSON:

```bash
python -m json.tool "$DEMO_DIR/../agf-demo-plan.json"
```

The exact schema may evolve before 1.0, but the artifact should make the proposed work explicit rather than turning a high-level goal directly into an unbounded mutation.

## 5. Exercise fail-closed behavior

AGF should refuse or escalate when required repository context is ambiguous or missing rather than inventing authority. For example, remove the configured remote in the disposable repository and attempt planning again:

```bash
cd "$DEMO_DIR"
git remote remove origin
agf-orchestrator plan \
  --repository "$DEMO_DIR" \
  --goal "Add subtraction support with tests" \
  --output "$DEMO_DIR/../agf-demo-invalid-plan.json"
```

The command should return a non-zero/preflight failure instead of silently proceeding with incomplete repository identity.

Restore the remote if you want to continue experimenting:

```bash
git remote add origin "$ORIGIN_DIR"
```

## 6. What this demo proves — and what it does not

This demo proves that a fresh user can install the project, run its regression suite, create a local governed target, generate a deterministic plan, and observe fail-closed repository preflight without credentials or network access.

It does **not** grant live execution, delivery, merge, deployment, or provider authority. Those capabilities have separate requirements and are intentionally not activated by this walkthrough.

For the wider execution and delivery model, continue with [EXECUTION_MODEL.md](EXECUTION_MODEL.md), [AUTONOMOUS_DIRECTOR.md](AUTONOMOUS_DIRECTOR.md), and the [repository README](https://github.com/AcTweeteR/AGF-Orchestrator#readme).

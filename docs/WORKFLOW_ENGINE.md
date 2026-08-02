# Workflow Engine

The workflow is a provider-neutral state transition across accountable stages:

**Issue → Planning → Architecture → Implementation → Review → Compliance → Release**

The Issue stage captures intent and constraints. Planning decomposes the request. Architecture establishes boundaries and decisions. Implementation produces the change. Review evaluates quality against criteria. Compliance evaluates conformance with AGF and applicable policy. Release confirms all gates and records delivery.

## Transition discipline

Each stage has an entry condition, an accountable role, required evidence, and an exit decision. A failed gate returns the task to the stage that can resolve it, with findings attached. An unresolved conflict escalates rather than being silently overridden. A task cannot skip a stage unless an explicit AGF rule authorizes the omission and the decision is recorded.

The [TASK_MODEL.md](TASK_MODEL.md) defines durable task states; this document defines the workflow stages that produce those state transitions. The [REVIEW_PIPELINE.md](REVIEW_PIPELINE.md) defines the independent gates.

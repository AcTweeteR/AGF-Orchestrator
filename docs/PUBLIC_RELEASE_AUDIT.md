# Public release history audit

AGF-Orchestrator must not be made public solely on the basis of a current-tree secret search. Public release requires an audit of reachable Git history.

The CI job checks out full history (`fetch-depth: 0`) and runs:

```bash
python tools/audit_public_history.py
```

The audit fails closed if the checkout is shallow. It inspects reachable Git objects for common private-key and provider-token formats, high-entropy secret assignments, user home-directory paths, sensitive credential filenames, and unexpectedly large blobs.

A PASS is release evidence, not proof that disclosure risk is mathematically impossible. Maintainers must still review known project-specific sensitive identifiers and repository settings before changing visibility.

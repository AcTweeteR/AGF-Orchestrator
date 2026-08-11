"""Static guardrails for the single consequential authority path."""

import ast
from pathlib import Path

SRC = Path(__file__).parents[1] / "src" / "agf_orchestrator"


def _calls_with_attribute(tree: ast.AST, attribute: str) -> list[tuple[str, int]]:
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == attribute:
                calls.append((attribute, node.lineno))
    return calls


def test_legacy_resolution_is_only_called_by_canonical_resolver():
    bypasses = []
    for path in SRC.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for _, line in _calls_with_attribute(function, "_resolve_legacy"):
                allowed = path.name == "authority_context.py" or (
                    path.name == "policy_authority.py"
                    and function.name == "_verify_constitution"
                )
                if not allowed:
                    bypasses.append(f"{path.name}:{line}")
    assert bypasses == [], f"legacy authority bypasses detected: {bypasses}"


def test_public_authority_resolvers_delegate_to_canonical_path():
    for name, class_name in (
        ("constitution.py", "ConstitutionAuthority"),
        ("policy_authority.py", "PolicyAuthority"),
    ):
        tree = ast.parse((SRC / name).read_text(encoding="utf-8"), filename=name)
        methods = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "resolve"
        }
        assert "resolve" in methods, f"{class_name}.resolve is missing"
        source = ast.unparse(methods["resolve"])
        assert "resolve_authority" in source


def test_provider_runtime_contains_no_owner_private_key_access():
    provider = (SRC / "provider_intelligence.py").read_text(encoding="utf-8")
    assert "owner.key" not in provider
    assert "_owner_key" not in provider


def test_legacy_hmac_signing_is_not_called_by_runtime_consumers():
    for path in SRC.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert not _calls_with_attribute(tree, "sign_state")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and path.name != "provider_intelligence.py":
                assert not any(
                    keyword.arg == "signing_key" for keyword in node.keywords
                )


def test_runtime_has_no_public_authority_mutation_methods():
    source = (SRC / "authority_generation.py").read_text(encoding="utf-8")
    assert "def activate(" not in source
    assert "def save_prepared(" not in source


def test_runtime_never_calls_owner_generation_mutations():
    for path in SRC.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert not _calls_with_attribute(tree, "_save_prepared_owner_controlled")
        assert not _calls_with_attribute(tree, "_activate_owner_controlled")

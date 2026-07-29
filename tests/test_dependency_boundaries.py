"""Enforces the required dependency direction:

    Streamlit/UI -> application/query interfaces -> deterministic domain policy -> storage

Prohibited: application -> Streamlit, application -> luxor_desk_* UI
module, storage -> UI, domain -> Streamlit, ReviewInterpreter -> storage
writes.

Uses Python's ast module to inspect actual import statements (not
string-grepping, not trusting docstrings) -- see the module docstring
of each file under test for why this class of guard exists.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _imported_module_names(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


_LUXOR_DESK_UI_MODULE_PREFIXES = ("luxor_desk", "luxor_ops", "luxor_console", "luxor_demo", "luxor_prototype")


def test_application_py_imports_no_streamlit():
    names = _imported_module_names(REPO_ROOT / "src" / "dispatcher" / "application.py")
    assert "streamlit" not in names


def test_application_py_imports_no_luxor_desk_ui_module():
    names = _imported_module_names(REPO_ROOT / "src" / "dispatcher" / "application.py")
    for prefix in _LUXOR_DESK_UI_MODULE_PREFIXES:
        assert not any(n.startswith(prefix) for n in names), f"application.py must not import {prefix}*"


def test_app_models_py_imports_no_streamlit():
    names = _imported_module_names(REPO_ROOT / "src" / "dispatcher" / "app_models.py")
    assert "streamlit" not in names


def test_action_names_py_imports_nothing_from_this_project():
    """action_names.py is a leaf module -- enum.Enum only -- so nothing
    that imports it can form a cycle back through storage.py or the
    application layer."""
    names = _imported_module_names(REPO_ROOT / "src" / "dispatcher" / "action_names.py")
    assert names <= {"enum", "__future__"}


def test_clock_py_imports_no_streamlit_and_no_third_party_time_library():
    names = _imported_module_names(REPO_ROOT / "src" / "dispatcher" / "clock.py")
    assert "streamlit" not in names
    assert "arrow" not in names and "pendulum" not in names and "dateutil" not in names


def test_source_adapters_py_imports_no_streamlit_and_no_luxor_desk_ui_module():
    names = _imported_module_names(REPO_ROOT / "src" / "dispatcher" / "source_adapters.py")
    assert "streamlit" not in names
    for prefix in _LUXOR_DESK_UI_MODULE_PREFIXES:
        assert not any(n.startswith(prefix) for n in names)


def test_adapters_py_imports_no_streamlit_and_no_luxor_desk_ui_module():
    names = _imported_module_names(REPO_ROOT / "src" / "dispatcher" / "adapters.py")
    assert "streamlit" not in names
    for prefix in _LUXOR_DESK_UI_MODULE_PREFIXES:
        assert not any(n.startswith(prefix) for n in names)


def test_review_interpreter_py_imports_no_streamlit_and_no_luxor_desk_ui_module():
    names = _imported_module_names(REPO_ROOT / "src" / "dispatcher" / "review_interpreter.py")
    assert "streamlit" not in names
    for prefix in _LUXOR_DESK_UI_MODULE_PREFIXES:
        assert not any(n.startswith(prefix) for n in names)


def test_review_interpreter_cannot_import_storage_write_functions():
    """Structural guard: no implementation of ReviewInterpreter in this
    file may import storage.py at module scope (the only place a write
    capability could enter) -- DeterministicRulesReviewInterpreter's own
    local import of dispatcher.parser (not storage) is the one and only
    permitted lazy import in this file, and is read-only (extract_job())."""
    source = (REPO_ROOT / "src" / "dispatcher" / "review_interpreter.py").read_text(encoding="utf-8")
    assert "from .storage import" not in source
    assert "from . import storage" not in source
    assert "import dispatcher.storage" not in source


def test_storage_py_imports_no_streamlit_and_no_luxor_desk_ui_module():
    names = _imported_module_names(REPO_ROOT / "src" / "dispatcher" / "storage.py")
    assert "streamlit" not in names
    for prefix in _LUXOR_DESK_UI_MODULE_PREFIXES:
        assert not any(n.startswith(prefix) for n in names)


def test_all_src_dispatcher_modules_avoid_streamlit_at_module_scope():
    """Broad sweep: every module under src/dispatcher/ that belongs to
    this round's architecture layer (application/domain/storage) must
    never import streamlit at module scope (a local, function-body
    import would still show up here via ast.walk, so this also catches
    lazily-imported Streamlit dependencies).

    quo_ingest.py and simulator.py are excluded: both predate this
    round's DispatcherService pattern and embed an old render function
    directly (app.py's original "Quo Inbox (live, read-only)" tab and
    the fake-inbox simulator UI) -- a known, disclosed pre-existing
    architectural inconsistency, out of scope for this pass (fixing it
    means touching app.py's UI, which "do not redesign the UI"
    forbids). See docs/ARCHITECTURE_VERIFICATION_131ABCE.md."""
    dispatcher_dir = REPO_ROOT / "src" / "dispatcher"
    known_legacy_exceptions = {"quo_ingest.py", "simulator.py"}
    offenders = []
    for path in dispatcher_dir.rglob("*.py"):
        if path.name in known_legacy_exceptions:
            continue
        names = _imported_module_names(path)
        if "streamlit" in names:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], f"Files under src/dispatcher/ importing streamlit: {offenders}"


_FORBIDDEN_DIRECT_STORAGE_CALLS = frozenset({
    "mark_acknowledged", "mark_copied", "mark_whatsapp_opened", "mark_dispatched_manually",
    "mark_quo_pending_message_actioned", "save_audit_entry", "record_technician_update",
    "mark_company_update_prepared", "mark_company_update_sent_manually", "pass_or_cancel_job",
    "record_job_closing", "mark_company_closing_prepared", "mark_company_closing_sent_manually",
    "complete_job",
})


def _actual_storage_calls(path: Path) -> set:
    """AST-based (not substring search, which false-positives on
    docstrings/comments that merely *mention* a function name): finds
    real `storage.<name>(...)` call sites."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "storage"
        ):
            calls.add(node.func.attr)
    return calls


def test_ui_uses_dispatcher_service_for_write_actions():
    """luxor_desk_shared.py's write-action handlers must go through
    DispatcherService -- direct storage.mark_*/save_audit_entry/
    record_*/pass_or_cancel_job/complete_job calls are not permitted
    there anymore (see the 131abce refactor)."""
    calls = _actual_storage_calls(REPO_ROOT / "luxor_desk_shared.py")
    forbidden_found = calls & _FORBIDDEN_DIRECT_STORAGE_CALLS
    assert not forbidden_found, f"luxor_desk_shared.py calls storage.* directly, bypassing DispatcherService: {forbidden_found}"


def test_luxor_desk_read_model_performs_no_operational_writes():
    """Same guard tests/test_luxor_desk_real.py's
    test_no_second_persistence_layer already applies to schema/SQL --
    this additionally checks it never calls any of the operational
    write functions directly (it may still call read-only storage
    functions like get_job_by_id, list_technicians, etc.)."""
    calls = _actual_storage_calls(REPO_ROOT / "luxor_desk_read_model.py")
    forbidden_write_calls = _FORBIDDEN_DIRECT_STORAGE_CALLS | {"mark_first_seen", "mark_luxor_prepared"}
    forbidden_found = calls & forbidden_write_calls
    assert not forbidden_found, f"luxor_desk_read_model.py calls operational write function(s): {forbidden_found}"

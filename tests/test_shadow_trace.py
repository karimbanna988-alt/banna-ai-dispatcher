"""Tests for dispatcher.shadow_trace -- the read-only reconstruction of
end-to-end job traces from WhatsApp observations.

The module's whole value is that it links messages ONLY by exact ticket
and refuses to assert a chain it cannot prove, so most of these tests
pin what it must NOT claim.
"""
from dispatcher.shadow_trace import (
    CONFIDENCE_AMBIGUOUS, CONFIDENCE_EXACT, CONFIDENCE_STRONG, CONFIDENCE_UNMATCHED,
    STAGE_ORDER, ShadowTrace, TraceHop, actionable_traces, build_traces,
)


def hop(ticket, group, role, ts, direction="incoming", confirmed=True, group_id=None):
    return TraceHop(
        ticket=ticket, group_name=group, group_id=group_id or f"{group}@g.us",
        role=role, direction=direction, timestamp=str(ts), confirmed_group=confirmed,
    )


def test_a_ticket_seen_in_only_one_group_is_not_a_trace():
    traces = build_traces([hop("T1", "Intake", "COMPANY_JOB_INTAKE", 100)])
    assert traces[0].confidence == CONFIDENCE_UNMATCHED
    assert traces[0].stages_covered == []
    assert actionable_traces(traces) == []


def test_two_messages_in_the_same_group_are_still_not_a_trace():
    """Repeats within one group must never look like a chain."""
    traces = build_traces([
        hop("T1", "Intake", "COMPANY_JOB_INTAKE", 100),
        hop("T1", "Intake", "COMPANY_JOB_INTAKE", 200),
    ])
    assert traces[0].confidence == CONFIDENCE_UNMATCHED


def test_intake_then_dispatch_is_an_exact_trace():
    """The real observed shape: a job arrives from the company, then the
    dispatcher forwards the same ticket to a technician group."""
    traces = build_traces([
        hop("JORT-RARJ", "Augusta - Adam", "COMPANY_JOB_INTAKE", 1786397924),
        hop("JORT-RARJ", "Sebai Augusta Dispatch", "TECH_DISPATCH", 1786397946, direction="outgoing"),
    ])
    t = traces[0]
    assert t.confidence == CONFIDENCE_EXACT
    assert t.stages_covered == ["COMPANY_JOB_INTAKE", "TECH_DISPATCH"]
    assert t.is_complete is False


def test_full_four_stage_chain_is_exact_and_complete():
    traces = build_traces([
        hop("T1", "Intake", "COMPANY_JOB_INTAKE", 100),
        hop("T1", "TechDisp", "TECH_DISPATCH", 200, direction="outgoing"),
        hop("T1", "TechClose", "TECH_CLOSINGS", 300),
        hop("T1", "CoClose", "COMPANY_CLOSINGS", 400, direction="outgoing"),
    ])
    t = traces[0]
    assert t.confidence == CONFIDENCE_EXACT
    assert t.stages_covered == STAGE_ORDER
    assert t.is_complete is True


def test_same_ticket_at_the_same_stage_in_two_groups_is_ambiguous_not_collapsed():
    """A reassignment after a decline is real and important, but it is not
    one clean linear chain -- it must surface for a human, never be
    silently flattened."""
    traces = build_traces([
        hop("T1", "TechA", "TECH_DISPATCH", 100),
        hop("T1", "TechB", "TECH_DISPATCH", 200),
    ])
    t = traces[0]
    assert t.confidence == CONFIDENCE_AMBIGUOUS
    assert t.is_complete is False
    assert any("reassignment" in n for n in t.notes)


def test_out_of_order_stages_are_strong_not_exact():
    """A closing observed before the dispatch cannot be asserted as a
    clean progression."""
    traces = build_traces([
        hop("T1", "TechClose", "TECH_CLOSINGS", 100),
        hop("T1", "TechDisp", "TECH_DISPATCH", 200),
    ])
    t = traces[0]
    assert t.confidence == CONFIDENCE_STRONG
    assert any("out of the expected order" in n for n in t.notes)


def test_a_hop_in_an_unmapped_group_is_flagged_and_never_silently_trusted():
    """Real observed case: ticket JOBW-ZZ3D moved through a group that is
    still UNMAPPED_WORK. The trace is still useful evidence, but must say
    so rather than implying the group's role is established."""
    traces = build_traces([
        hop("JOBW-ZZ3D", "Ashraf Charlotte Dispatch", None, 1786399006, direction="outgoing", confirmed=False),
        hop("JOBW-ZZ3D", "Ashraf Charlotte Closings", "TECH_CLOSINGS", 1786409636),
    ])
    t = traces[0]
    assert t.confidence in (CONFIDENCE_STRONG, CONFIDENCE_EXACT)
    assert any("not yet confirmed" in n for n in t.notes)
    assert t.is_complete is False


def test_hops_are_ordered_by_timestamp_regardless_of_input_order():
    traces = build_traces([
        hop("T1", "CoClose", "COMPANY_CLOSINGS", 400),
        hop("T1", "Intake", "COMPANY_JOB_INTAKE", 100),
        hop("T1", "TechDisp", "TECH_DISPATCH", 200),
    ])
    assert [h.group_name for h in traces[0].hops] == ["Intake", "TechDisp", "CoClose"]


def test_a_missing_timestamp_does_not_crash_ordering():
    traces = build_traces([
        TraceHop("T1", "A", "a@g.us", "COMPANY_JOB_INTAKE", "incoming", None, True),
        hop("T1", "B", "TECH_DISPATCH", 200),
    ])
    assert len(traces[0].hops) == 2


def test_different_tickets_never_merge_into_one_trace():
    """Timing proximity must never link two messages -- only an exact
    shared ticket does."""
    traces = build_traces([
        hop("T1", "Intake", "COMPANY_JOB_INTAKE", 100),
        hop("T2", "TechDisp", "TECH_DISPATCH", 101),
    ])
    assert len(traces) == 2
    assert all(t.confidence == CONFIDENCE_UNMATCHED for t in traces)


def test_actionable_traces_filters_out_single_group_tickets():
    traces = build_traces([
        hop("T1", "Intake", "COMPANY_JOB_INTAKE", 100),
        hop("T2", "Intake", "COMPANY_JOB_INTAKE", 100),
        hop("T2", "TechDisp", "TECH_DISPATCH", 200),
    ])
    actionable = actionable_traces(traces)
    assert [t.ticket for t in actionable] == ["T2"]


def test_module_has_no_write_or_io_capability():
    """Structural: this module must stay a pure derivation over rows it is
    handed -- no connection, no cursor, no SQL, so it can never persist a
    derived guess as business truth."""
    import pathlib
    import dispatcher.shadow_trace as mod
    source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    for forbidden in ("execute(", "INSERT", "UPDATE ", "DELETE", "commit(", "sqlite3", "open("):
        assert forbidden not in source, f"shadow_trace must remain pure -- found {forbidden!r}"

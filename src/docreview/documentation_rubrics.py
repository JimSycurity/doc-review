"""Document roles and focused review criteria; no transport or workflow actions."""

from __future__ import annotations

from dataclasses import dataclass

TYPES: dict[str, str] = {
    "readme": "Introduce a project and route readers to starting instructions",
    "human_instructions": "Tell people how to perform and recover an operation",
    "reference": "Describe scoped technical interfaces and their constraints precisely",
    "agent_instructions": "Set scoped agent rules and route to focused guidance",
    "plan": "Sequence work toward an outcome with dependencies and completion criteria",
    "policy": "Define applicable requirements, ownership and exceptions",
    "design": "Explain proposed behavior, interfaces, choices and open questions",
    "analysis": "Evaluate evidence to reach bounded conclusions",
    "report": "Record results and their evidence for an identified audience",
    "adr": "Record an architectural decision and its consequences",
    "context": "Define stable vocabulary, relationships and boundaries",
    "index": "Route readers to the right documents for their tasks",
}


@dataclass(frozen=True)
class Criterion:
    """A single judgment plus its explicit local interpretation."""

    instructions: str
    levels: tuple[str, ...] = ()
    positive: bool = True
    weight: float = 1.0


COMMON = {
    "context_sufficient": Criterion(
        "Does the supplied context identify this document's intended reader task?"
    ),
    "type_fit": Criterion(
        "Does the document serve the supplied intended document type?"
    ),
    "audience_clear": Criterion("Is the document's intended reader clear?"),
    "purpose_coherent": Criterion("Does the document have a coherent primary purpose?"),
    "scope_fit": Criterion("Does each major section serve that primary purpose?"),
    "detail_fit": Criterion(
        "Is the level of detail appropriate for the intended reader?"
    ),
    "self_consistent": Criterion(
        "Are the statements within the document mutually consistent?"
    ),
    "findable": Criterion(
        "Can the reader find the guidance needed for their stated task?"
    ),
    "split_needed": Criterion(
        "Does the document combine substantial sections for unrelated reader tasks?",
        positive=False,
    ),
    "selective_reading": Criterion(
        "How well does the organization support selective reading?",
        (
            "Distinct reader tasks are mixed without useful navigation",
            "Tasks have sections, but finding one answer requires scanning unrelated tasks",
            "Readers can reach the needed material without reading unrelated detail",
        ),
    ),
}
PLAIN_ENGLISH = Criterion(
    "How clear is the human-facing explanation for its intended reader?",
    (
        "The explanation is dominated by unexplained jargon",
        "Unexplained jargon makes parts of the explanation hard for the intended reader",
        "The intended reader can understand the explanation without translating jargon",
    ),
)

TYPE_CHECKS = {
    "reference": {
        "reference_scope": "Is the coverage of the reference clearly bounded?",
        "interface_precision": "Are the documented interfaces described precisely?",
        "constraint_scope": "Are operational constraints stated in their applicable scope?",
        "interface_status": "Can readers distinguish existing interfaces from proposed ones?",
    },
    "readme": {
        "project_purpose": "Does the opening explain what the project does?",
        "getting_started": "Can a new user find how to get started?",
        "entrypoint_depth": "Does detailed contributor guidance stay in its owning documents?",
    },
    "human_instructions": {
        "prerequisites": "Are the operation's prerequisites clear?",
        "usable_steps": "Can the intended reader follow the procedure?",
        "expected_outcome": "Does the procedure explain how to recognize success?",
        "recovery": "Does the procedure explain what to do if the operation fails?",
        "maintained_guidance": "Does the document keep individual execution history separate from reusable instructions?",
    },
    "agent_instructions": {
        "instruction_scope": "Is the scope of the instructions clear?",
        "actionable_rules": "Can an agent determine what actions the rules require?",
        "instruction_authority": "Is instruction precedence clear?",
        "focused_routing": "Does detailed task guidance live in linked focused documents?",
    },
    "plan": {
        "outcomes": "Are the intended outcomes explicit?",
        "dependencies": "Are prerequisites identified before dependent work?",
        "completion": "Are completion criteria stated?",
    },
    "policy": {
        "applicability": "Is it clear where the policy applies?",
        "requirements": "Are its requirements unambiguous?",
        "exceptions": "Does the policy explain how exceptions are handled?",
        "ownership": "Is responsibility for the policy clear?",
    },
    "design": {
        "problem": "Does the design state the problem being solved?",
        "behavior": "Is the proposed behavior clear?",
        "interfaces": "Are the boundaries between affected components clear?",
        "tradeoffs": "Are the important tradeoffs explained?",
        "open_questions": "Are unresolved decisions identifiable?",
        "proposal_status": "Can readers distinguish proposed behavior from current behavior?",
        "summary": "Does the opening explain the main point in plain English?",
    },
    "analysis": {
        "question": "Is the question being investigated clear?",
        "evidence": "Are the conclusions supported by supplied evidence?",
        "limits": "Are limits on the conclusions explicit?",
        "summary": "Does the summary explain the findings in plain English?",
    },
    "report": {
        "results": "Are the reported results clear?",
        "evidence": "Is the evidence for the reported results identifiable?",
        "limits": "Are the limits of the reported results explicit?",
        "summary": "Does the summary explain the results in plain English?",
    },
    "adr": {
        "decision": "Is the specific decision explicit?",
        "alternatives": "Are the considered alternatives identifiable?",
        "consequences": "Are the consequences explained?",
        "status": "Is the decision's status clear?",
    },
    "context": {
        "vocabulary": "Are the shared terms defined clearly?",
        "relationships": "Are the important relationships explained?",
        "boundaries": "Are the domain boundaries clear?",
        "stable_context": "Does the document keep temporary work status outside stable context?",
    },
    "index": {
        "routing": "Does each entry explain when to read its destination?",
        "grouping": "Are destinations grouped by reader task?",
        "index_depth": "Does the index link to guidance instead of repeating its contents?",
    },
}


def criteria_for(
    document_type: str, has_context: bool, has_references: bool
) -> dict[str, Criterion]:
    """Add only criteria whose external comparison material is supplied."""
    criteria = dict(COMMON)
    criteria.update(
        {key: Criterion(text) for key, text in TYPE_CHECKS[document_type].items()}
    )
    if document_type != "agent_instructions":
        criteria["plain_english"] = PLAIN_ENGLISH
    if has_context:
        criteria["governing_consistency"] = Criterion(
            "Is the document consistent with the supplied governing guidance?"
        )
    if has_references:
        criteria["canonical_ownership"] = Criterion(
            "Does the document preserve the supplied references as the owners of their detailed guidance?"
        )
    return criteria

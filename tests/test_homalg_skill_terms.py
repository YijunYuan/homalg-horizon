"""Keep bundled instructions consistent with the enforced HomAlg contracts."""
from pathlib import Path
import re

from archon_horizon.subagents.compile import load_descriptors

ROOT = Path(__file__).resolve().parents[1] / "src/archon_horizon"
ADAPTED = "horizon blueprint-conventions blueprint-integrity graph-traceability hgraph restart-module formalization-review definition-quality api-composition mathlib-conventions mathlib-orientation source-fidelity honesty references transcription-fidelity subagents lean-check project-git".split()


def test_no_conflicting_upstream_policy_in_packaged_guidance():
    forbidden = re.compile(r"first source of truth|Duplicate of a Mathlib/project concept|Search for an existing canonical abstraction before introducing a duplicate|author and maintain the blueprint", re.I)
    files = list((ROOT / "skills").rglob("SKILL.md")) + list((ROOT / "subagents/descriptors").glob("*.md"))
    for path in files:
        assert not forbidden.search(path.read_text()), str(path)


def test_adapted_skills_share_identical_homalg_contract():
    blocks = []
    for name in ADAPTED:
        text = (ROOT / "skills" / name / "SKILL.md").read_text()
        section = text.split("## HomAlgDB rules\n", 1)[1].split("<!-- End HomAlgDB rules -->", 1)[0].strip()
        blocks.append(section)
        for term in ("P-1", "P-2", "AGENTS.md", "horizon check", "write scope", "Cited_", "needs-adjudication", "homalg dispute open", "early-review-ok", "hgit", "<root>/.git"):
            assert term in section, (name, term)
    assert len(set(blocks)) == 1


def test_node_writer_replaces_generated_blueprint_writer():
    descriptors = load_descriptors(ROOT / "subagents/descriptors")
    assert not descriptors["blueprint"].default_enabled
    node = descriptors["node-writer"]
    assert node.default_enabled and not node.read_only
    assert "HomAlg/<K>/**" in node.write_domain
    assert "literature/<K>/reviews/**" in node.write_domain
    assert "disjoint" in node.prompt_body
    assert "never edit generated" in node.prompt_body.lower()


def test_independent_review_requires_explicit_policy_and_actual_provenance():
    files = [(ROOT / "skills" / name / "SKILL.md") for name in ADAPTED]
    files += list((ROOT / "subagents/descriptors").glob("*.md"))
    for path in files:
        text = path.read_text()
        if "## HomAlgDB rules" not in text:
            continue
        assert "horizon/review-policy.yaml" in text, str(path)
        assert "--author-session" in text, str(path)
        assert "Actual author/reviewer sessions must differ" in text, str(path)
        assert "Escalate unresolved mathematical or source disagreements" in text, str(path)
        assert "cannot change this policy themselves" in text, str(path)


def test_source_and_auxiliary_definitions_are_distinguished():
    for relative in ("skills/definition-quality/SKILL.md", "subagents/descriptors/definition-quality-reviewer.md"):
        body = (ROOT / relative).read_text()
        assert "A parallel definition of a Mathlib concept is expected here" in body
        assert "only to `Aux_`" in body
    for relative in ("skills/api-composition/SKILL.md", "subagents/descriptors/api-composition-reviewer.md"):
        assert "mathlib-bypass-statement" in (ROOT / relative).read_text()

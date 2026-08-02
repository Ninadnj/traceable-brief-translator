"""Persistent Streamlit application for the six-section dossier."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import streamlit as st
from pydantic import ValidationError

from compoundx.external_sources import ExternalSourcePackError
from compoundx.model_client import (
    ModelCallError,
    ModelConfigurationError,
    OpenAIModelClient,
)
from compoundx.pipeline import OutputExistsError, run_translation
from compoundx.section_review import (
    ALL_SECTIONS,
    LIST_SECTIONS,
    NARRATIVE_SECTIONS,
    FinalReviewManifest,
    ListItemReview,
    ListItemReviewStatus,
    ListSectionReview,
    NarrativeSectionReview,
    SectionReviewAction,
    SectionReviewArtifact,
    SectionReviewLifecycleError,
    SectionReviewOutputExistsError,
    SectionReviewStatus,
    build_approved_list_review,
    build_approved_narrative_review,
    finalize_section_review,
    list_section_reviews,
    load_accepted_report,
    load_section_review,
    save_section_review,
)
from compoundx.semantic_evaluation import (
    SemanticEvaluation,
    load_semantic_evaluation,
)
from compoundx.schemas import (
    ContentValidation,
    DossierSection,
    ExternalCitation,
    ExternalSource,
    ReviewReport,
    SectionFinding,
    TraceableContent,
    TraceablePoint,
    TranslationSection,
)
from compoundx.source_loader import SourceError


MAX_BRIEF_BYTES = 10 * 1024 * 1024
MAX_EXTERNAL_PACK_BYTES = 2 * 1024 * 1024
ALLOWED_BRIEF_SUFFIXES = {".md", ".markdown", ".txt", ".pdf"}
ALLOWED_EXTERNAL_PACK_SUFFIXES = {".json"}
EXTERNAL_PACK_EXAMPLES = "examples/external-sources/"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_RESULTS_ROOT = PROJECT_ROOT / "demo-results"
# Live runs are written outside the curated demo set. `demo-results/` holds only
# the two reviewed cases, so using the app cannot silently add a third.
LIVE_RUNS_ROOT = PROJECT_ROOT / "runs"
SECTION_LABELS = {
    DossierSection.PRODUCT_INTENT: "Product intent",
    DossierSection.COMPONENT_FUNCTION: "Component function",
    DossierSection.PERFORMANCE_TO_VALIDATE: "Performance to validate",
    DossierSection.MATERIAL_RELEVANT_CRITERIA: "Material-relevant criteria",
    DossierSection.MISSING_INFORMATION: "Missing information",
    DossierSection.CONFLICTS_AND_TRADEOFFS: "Conflicts and trade-offs",
}
# The four-level chain is the thesis of the dossier, so the UI states what each
# level is for rather than leaving the reader to infer it from the section name.
SECTION_PURPOSE = {
    DossierSection.PRODUCT_INTENT: (
        "What the product has to achieve, in the requester's own terms."
    ),
    DossierSection.COMPONENT_FUNCTION: (
        "What the component has to do in order to deliver that intent."
    ),
    DossierSection.PERFORMANCE_TO_VALIDATE: (
        "Which behaviours have to be measured and proven."
    ),
    DossierSection.MATERIAL_RELEVANT_CRITERIA: (
        "Which material-relevant properties those behaviours imply."
    ),
    DossierSection.MISSING_INFORMATION: (
        "Decisions the brief does not settle, each traced to what it does say."
    ),
    DossierSection.CONFLICTS_AND_TRADEOFFS: (
        "Requirements that cannot all be maximised at the same time."
    ),
}


@dataclass(frozen=True)
class CompletedRun:
    report: ReviewReport
    report_json: bytes
    report_markdown: bytes


@dataclass(frozen=True)
class SavedSource:
    run_dir: Path
    source_path: Path
    external_sources_path: Path | None = None


@dataclass(frozen=True)
class SavedDemoRun:
    run_dir: Path
    source_path: Path
    completed: CompletedRun
    accepted_report_path: Path | None = None


def save_demo_source(
    *,
    uploaded_name: str,
    content: bytes,
    external_pack_name: str | None = None,
    external_pack_content: bytes | None = None,
    results_root: Path = LIVE_RUNS_ROOT,
    created_at: datetime | None = None,
) -> SavedSource:
    """Persist one uploaded source in a new, non-overwriting demo directory."""

    source_name = Path(uploaded_name).name
    suffix = Path(source_name).suffix.casefold()
    if suffix not in ALLOWED_BRIEF_SUFFIXES:
        raise ValueError("The brief must be PDF, Markdown, or UTF-8 text.")
    if not content:
        raise ValueError("The uploaded brief is empty.")
    if len(content) > MAX_BRIEF_BYTES:
        raise ValueError("The product brief exceeds the 10 MB demo limit.")

    pack_name = None
    if external_pack_content is not None:
        pack_name = Path(external_pack_name or "external-sources.json").name
        if Path(pack_name).suffix.casefold() not in ALLOWED_EXTERNAL_PACK_SUFFIXES:
            raise ValueError("The external-source pack must be a JSON file.")
        if not external_pack_content:
            raise ValueError("The uploaded external-source pack is empty.")
        if len(external_pack_content) > MAX_EXTERNAL_PACK_BYTES:
            raise ValueError("The external-source pack exceeds the 2 MB demo limit.")

    digest = hashlib.sha256(content).hexdigest()
    timestamp = (created_at or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S%fZ")
    safe_stem = re.sub(r"[^a-zA-Z0-9]+", "-", Path(source_name).stem).strip("-")
    safe_stem = safe_stem[:60] or "brief"
    run_dir = results_root / f"{safe_stem}-{timestamp}-{digest[:10]}"
    source_path = run_dir / "source" / source_name
    source_path.parent.mkdir(parents=True, exist_ok=False)
    source_path.write_bytes(content)
    external_sources_path = None
    if pack_name is not None and external_pack_content is not None:
        # Introduced knowledge is frozen next to the brief before any model
        # call, so a run can never cite a snapshot that was never persisted.
        external_sources_path = run_dir / "external-sources" / pack_name
        external_sources_path.parent.mkdir(parents=True, exist_ok=False)
        external_sources_path.write_bytes(external_pack_content)
    return SavedSource(
        run_dir=run_dir,
        source_path=source_path,
        external_sources_path=external_sources_path,
    )


def run_saved_demo(
    *,
    brief_upload: Any,
    api_key: str,
    model: str,
    critic_model: str,
    external_pack_upload: Any = None,
    results_root: Path = LIVE_RUNS_ROOT,
) -> SavedDemoRun:
    """Run the translator while keeping the source and generated reports."""

    if brief_upload is None:
        raise ValueError("Upload a product brief before starting the analysis.")
    session_key = api_key.strip()
    if not session_key:
        raise ModelConfigurationError(
            "Enter an OpenAI API key for this browser session."
        )
    selected_model = model.strip()
    if not selected_model:
        raise ModelConfigurationError("Enter an OpenAI translator model ID.")

    saved = save_demo_source(
        uploaded_name=brief_upload.name,
        content=brief_upload.getvalue(),
        external_pack_name=(
            external_pack_upload.name if external_pack_upload is not None else None
        ),
        external_pack_content=(
            external_pack_upload.getvalue()
            if external_pack_upload is not None
            else None
        ),
        results_root=results_root,
    )
    try:
        translator_client = OpenAIModelClient(
            model=selected_model,
            api_key=session_key,
        )
        selected_critic_model = critic_model.strip()
        critic_client = (
            OpenAIModelClient(
                model=selected_critic_model,
                api_key=session_key,
            )
            if selected_critic_model
            and selected_critic_model != selected_model
            else None
        )
        result = run_translation(
            source_path=saved.source_path,
            output_dir=saved.run_dir / "translation",
            client=translator_client,
            critic_client=critic_client,
            external_sources_path=saved.external_sources_path,
        )
    except Exception as error:
        safe_message = str(error).replace(session_key, "[redacted]")
        (saved.run_dir / "failure.txt").write_text(
            f"{type(error).__name__}: {safe_message}\n",
            encoding="utf-8",
        )
        raise

    return SavedDemoRun(
        run_dir=saved.run_dir,
        source_path=saved.source_path,
        completed=CompletedRun(
            report=result.report,
            report_json=result.json_path.read_bytes(),
            report_markdown=result.markdown_path.read_bytes(),
        ),
    )


def load_latest_accepted_demo(
    results_root: Path = DEMO_RESULTS_ROOT,
) -> SavedDemoRun | None:
    """Strictly re-ingest the newest accepted run for a durable demo."""

    candidates = list_accepted_demo_reports(results_root)
    if not candidates:
        return None
    return load_accepted_demo(candidates[0])


def list_accepted_demo_reports(
    results_root: Path = DEMO_RESULTS_ROOT,
) -> tuple[Path, ...]:
    return tuple(
        sorted(
            results_root.glob("*/acceptance/report.json"),
            key=lambda path: (
                any(path.parents[1].glob("section-reviews/*/review.pdf")),
                path.stat().st_mtime_ns,
            ),
            reverse=True,
        )
    )


def load_accepted_demo(report_path: str | Path) -> SavedDemoRun:
    report_path = Path(report_path)
    report = ReviewReport.model_validate_json(
        report_path.read_text(encoding="utf-8")
    )
    run_dir = report_path.parents[1]
    source_path = run_dir / "source" / report.source.name
    if not source_path.is_file():
        raise ValueError(f"saved demo source is missing: {source_path}")
    source_digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    if source_digest != report.source.sha256:
        raise ValueError("saved demo source digest does not match its report")
    markdown_path = report_path.with_suffix(".md")
    if not markdown_path.is_file():
        raise ValueError(f"saved demo Markdown report is missing: {markdown_path}")
    return SavedDemoRun(
        run_dir=run_dir,
        source_path=source_path,
        completed=CompletedRun(
            report=report,
            report_json=report_path.read_bytes(),
            report_markdown=markdown_path.read_bytes(),
        ),
        accepted_report_path=report_path,
    )


def _select_terminal_review(
    records: list[tuple[Path, SectionReviewArtifact]],
    *,
    accepted_report_sha256: str,
    source_sha256: str,
) -> tuple[Path, SectionReviewArtifact] | None:
    """Select the newest terminal record for this exact accepted report.

    A terminal record wins even if an invalid later save-section snapshot was
    placed beside it. Finalization is a one-way lifecycle transition; the UI
    must never turn a published chain back into an editable draft.
    """

    terminal = [
        record
        for record in records
        if record[1].action is not SectionReviewAction.SAVE_SECTION
        and record[1].accepted_report_sha256 == accepted_report_sha256
        and record[1].source_sha256 == source_sha256
    ]
    if not terminal:
        return None
    return max(
        terminal,
        key=lambda record: (record[1].created_at, record[0].as_posix()),
    )


def _validate_terminal_review_chain(
    *,
    run_dir: Path,
    accepted_report_path: Path,
    accepted_report_sha256: str,
    source_sha256: str,
    terminal_path: Path,
    terminal_artifact: SectionReviewArtifact,
) -> None:
    """Verify every immutable parent edge before trusting a terminal review."""

    resolved_run = run_dir.resolve()
    expected_report_path = accepted_report_path.resolve()
    expected_report_relative = expected_report_path.relative_to(resolved_run).as_posix()
    current_path = terminal_path.resolve()
    current = terminal_artifact
    seen: set[Path] = set()
    first = True

    while True:
        try:
            current_path.relative_to(resolved_run)
        except ValueError as error:
            raise ValueError("review chain points outside its run directory") from error
        if current_path in seen:
            raise ValueError("review chain contains a parent cycle")
        seen.add(current_path)
        if (
            current.accepted_report_relative_path != expected_report_relative
            or current.accepted_report_sha256 != accepted_report_sha256
            or current.source_sha256 != source_sha256
            or current.reviewer_id != terminal_artifact.reviewer_id
        ):
            raise ValueError("review chain does not match the selected accepted report")
        if first:
            if current.action is SectionReviewAction.SAVE_SECTION:
                raise ValueError("selected review is not terminal")
        elif current.action is not SectionReviewAction.SAVE_SECTION:
            raise ValueError("a terminal review may only have save-section ancestors")

        parent = current.parent_review
        if parent is None:
            if first:
                raise ValueError("terminal review is missing its parent snapshot")
            return
        parent_path = (resolved_run / parent.relative_path).resolve()
        try:
            parent_path.relative_to(resolved_run)
        except ValueError as error:
            raise ValueError(
                "review parent points outside its run directory"
            ) from error
        if not parent_path.is_file():
            raise ValueError(f"review parent is missing: {parent.relative_path}")
        parent_bytes = parent_path.read_bytes()
        if hashlib.sha256(parent_bytes).hexdigest() != parent.sha256:
            raise ValueError(f"review parent hash mismatch: {parent.relative_path}")
        current_path = parent_path
        current = load_section_review(parent_path)
        first = False


def _load_linked_supporting_evaluation(
    *,
    run_dir: Path,
    final_review_path: Path,
    source_sha256: str,
) -> SemanticEvaluation | None:
    """Load the one optional rubric and prove which reviewed JSON it evaluates."""

    evaluation_path = run_dir / "evaluation" / "evaluation.json"
    if not evaluation_path.is_file():
        return None
    evaluation = load_semantic_evaluation(evaluation_path)
    if evaluation.evaluation_method != "model_assisted":
        raise ValueError(
            "published supporting evaluation must declare model_assisted method"
        )

    resolved_run = run_dir.resolve()
    expected_review_path = final_review_path.resolve()
    recorded_path = Path(evaluation.report_path)
    if recorded_path.is_absolute():
        raise ValueError("supporting evaluation report_path must be run-relative")
    resolved_recorded_path = (resolved_run / recorded_path).resolve()
    try:
        resolved_recorded_path.relative_to(resolved_run)
    except ValueError as error:
        raise ValueError(
            "supporting evaluation report_path points outside the selected run"
        ) from error
    if resolved_recorded_path != expected_review_path:
        raise ValueError(
            "supporting evaluation does not target the selected final review JSON"
        )
    review_sha256 = hashlib.sha256(final_review_path.read_bytes()).hexdigest()
    if evaluation.report_sha256 != review_sha256:
        raise ValueError(
            "supporting evaluation report hash does not match final review"
        )
    if evaluation.source_sha256 != source_sha256:
        raise ValueError(
            "supporting evaluation source hash does not match final review"
        )
    return evaluation


def _validate_final_review_outputs(review_path: Path) -> tuple[Path, Path]:
    """Verify the published Markdown and PDF against the final manifest."""

    manifest_path = review_path.with_name("manifest.json")
    manifest = FinalReviewManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    markdown_path = review_path.with_name("review.md")
    pdf_path = review_path.with_name("review.pdf")
    if (
        manifest.json_path != review_path.name
        or manifest.markdown_path != markdown_path.name
        or manifest.pdf_path != pdf_path.name
    ):
        raise ValueError("final review manifest names do not match published outputs")
    expected_hashes = (
        (review_path, manifest.reviewed_artifact_sha256),
        (markdown_path, manifest.markdown_sha256),
        (pdf_path, manifest.pdf_sha256),
    )
    for path, expected in expected_hashes:
        if not path.is_file():
            raise ValueError(f"final review output is missing: {path.name}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"final review output hash mismatch: {path.name}")
    return markdown_path, pdf_path


def main() -> None:
    st.set_page_config(
        page_title="CompoundX Demo",
        page_icon="📄",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.title("Traceable technical translation")
    st.markdown(
        "**The model proposes meaning · deterministic code proves traceability · "
        "a human decides.**"
    )
    st.caption(
        "AI translates an incomplete product brief. Deterministic checks protect "
        "traceability. A human owns the final engineering judgement."
    )

    _render_system_overview()
    translate_tab, review_tab, final_tab, verification_tab = st.tabs(
        ("1. Translate", "2. Review", "3. Final report", "4. Verification")
    )

    with translate_tab:
        saved_run = _render_translate_tab()

    with review_tab:
        if saved_run is None:
            st.info("Choose or generate a dossier in Translate before reviewing it.")
        elif saved_run.accepted_report_path is None:
            st.info(
                "Human review starts only after a model report passes the run "
                "validation gate."
            )
        else:
            _render_review_workflow(saved_run)

    with final_tab:
        if saved_run is None:
            st.info("Choose a saved dossier to view its final reviewed report.")
        else:
            _render_final_report_tab(saved_run)

    with verification_tab:
        _render_verification_tab(saved_run)


def _render_system_overview() -> None:
    with st.container(border=True):
        pillars = st.columns(3)
        pillars[0].markdown("**1 · The model proposes**")
        pillars[0].caption(
            "A translator writes the six sections; deterministic failures may "
            "trigger one recorded repair pass; a separate critic challenges "
            "each section and flags what a human must resolve."
        )
        pillars[1].markdown("**2 · The code proves**")
        pillars[1].caption(
            "Deterministic Python re-matches every quotation against the source "
            "it names, records character offsets for quotations from the brief "
            "and their page where the brief paginates, and rejects any measured "
            "value that is not present in a citation attached to the same "
            "statement."
        )
        pillars[2].markdown("**3 · The human decides**")
        pillars[2].caption(
            "Each of the six sections is approved, corrected, or sent back with a "
            "rationale. Every saved decision is an immutable, parent-linked "
            "snapshot."
        )

    with st.expander(
        "How it works - the six sections, the checks, and what they do not prove",
        expanded=False,
    ):
        st.write(
            "Product goals are translated into a six-section engineering dossier. "
            "The model proposes meaning; code verifies mechanical integrity; the "
            "reviewer decides whether the result is acceptable."
        )
        structure = st.columns(2)
        structure[0].markdown("**Four translation levels**")
        structure[0].caption(
            "Product intent → component function → performance to validate → "
            "material-relevant criteria. Each level constrains the next, so a "
            "reader can walk backwards from a material property to the sentence "
            "in the brief that motivated it."
        )
        structure[1].markdown("**Two open-question lists**")
        structure[1].caption(
            "Missing information and conflicts or trade-offs. These are lists of "
            "individually traceable points; they record what the brief leaves "
            "undecided instead of inventing an answer."
        )
        st.graphviz_chart(
            """
            digraph translation_workflow {
                graph [
                    rankdir=LR,
                    bgcolor="transparent",
                    pad="0.15",
                    nodesep="0.20",
                    ranksep="0.32"
                ];
                node [
                    shape=rect,
                    style="rounded,filled",
                    fontname="Arial",
                    fontsize=10,
                    margin="0.14,0.09",
                    penwidth=1.4
                ];
                edge [
                    color="#64748B",
                    penwidth=1.5,
                    arrowsize=0.75
                ];

                upload [
                    label="1  Brief upload",
                    fillcolor="#F8FAFC",
                    color="#94A3B8",
                    fontcolor="#182230"
                ];
                translator [
                    label="2  Technical translator\\nModel pass 1 - translator",
                    fillcolor="#EAF1FB",
                    color="#2457A7",
                    fontcolor="#163A70"
                ];
                verifier [
                    label="3  Citation and numeric verifier\\nDeterministic code",
                    fillcolor="#F1F5F9",
                    color="#475569",
                    fontcolor="#182230"
                ];
                repair [
                    label="Optional  Bounded repair\\nOnly after typed validation errors",
                    fillcolor="#FEF3C7",
                    color="#B45309",
                    fontcolor="#78350F"
                ];
                critic [
                    label="4  Section-level critic\\nSemantic model pass",
                    fillcolor="#EAF1FB",
                    color="#2457A7",
                    fontcolor="#163A70"
                ];
                review [
                    label="5  Human section review",
                    fillcolor="#EAF7F0",
                    color="#176B46",
                    fontcolor="#145337"
                ];
                report [
                    label="6  Final reviewed PDF",
                    fillcolor="#DCFCE7",
                    color="#15803D",
                    fontcolor="#14532D",
                    penwidth=1.8
                ];

                upload -> translator -> verifier;
                verifier -> critic [label=" clean", fontname="Arial", fontsize=9];
                verifier -> repair [label=" errors", fontname="Arial", fontsize=9];
                repair -> verifier [label=" recheck once", fontname="Arial", fontsize=9];
                critic -> review -> report;
            }
            """,
            width="stretch",
        )
        roles = st.columns(3)
        roles[0].markdown("**Technical translator + bounded repair**")
        roles[0].write(
            "Converts the brief into product intent, component function, "
            "performance to validate, material criteria, missing information, "
            "and trade-offs. If code finds a mechanical error, one repair pass "
            "runs and both states remain in the report."
        )
        roles[1].markdown("**Deterministic verifier**")
        roles[1].write(
            "Matches exact quotations, records character offsets and, where the "
            "brief paginates, source pages, blocks unsupported numbers, "
            "validates schemas, and preserves failures."
        )
        roles[2].markdown("**Semantic critic and human review**")
        roles[2].write(
            "The critic challenges each section. A reviewer then approves, "
            "corrects, removes, or adds content with a rationale."
        )
        st.markdown("**What the checks do not prove**")
        st.write(
            "- A verified quotation proves the sentence exists in the brief at "
            "that offset, and on that page where the brief paginates. It does "
            "not prove the section's reading of it is technically right."
        )
        st.write(
            "- External snapshots are verified against their own frozen text. "
            "The system does not establish that a source is authoritative or "
            "applicable."
        )
        st.write(
            "- Engineering judgement, derived targets and final acceptance stay "
            "with the human reviewer."
        )
        st.caption(
            "Deterministic checks prove traceability and numeric provenance, not "
            "engineering correctness. The altered-case evidence is in Verification."
        )


def _render_translate_tab() -> SavedDemoRun | None:
    st.subheader("Translate an incomplete product brief")

    # The saved-case picker is created first so it renders above the live-run
    # form, but it is filled in after the form so a fresh run wins the slot.
    saved_case_slot = st.container()
    live_run_slot = st.container()

    with live_run_slot, st.expander(
        "Run a live translation on your own brief - needs your own OpenAI API key",
        expanded=False,
    ):
        st.write(
            "Upload a text-based PDF, Markdown, or UTF-8 text brief. One live run "
            "uses a translator and critic call, plus at most one validation-triggered "
            "repair call, and saves the complete result."
        )
        with st.form("persistent-demo-input", clear_on_submit=False):
            brief_upload = st.file_uploader(
                "Product brief",
                type=("pdf", "md", "markdown", "txt"),
                help="Text-based PDFs only; scanned PDFs require OCR.",
            )
            api_key = st.text_input(
                "OpenAI API key - this browser session only",
                type="password",
                key="compoundx_demo_api_key",
                help="Used for this live run and never written to a demo artifact.",
            )
            model = st.text_input(
                "Translator model",
                value=os.environ.get("COMPOUNDX_DEMO_MODEL", ""),
                placeholder="Enter your OpenAI model ID",
                help=(
                    "There is no default model. Enter a model ID your own "
                    "OpenAI account can call; COMPOUNDX_DEMO_MODEL prefills "
                    "this field."
                ),
            )
            critic_model = st.text_input(
                "Optional distinct critic model",
                value=os.environ.get("COMPOUNDX_DEMO_CRITIC_MODEL", ""),
                placeholder="Leave blank to reuse the translator model",
            )
            external_pack_upload = st.file_uploader(
                "Optional external-source pack (JSON)",
                type=("json",),
                help=(
                    "Frozen snapshots of external text the model may cite as "
                    f"context. Example packs live in {EXTERNAL_PACK_EXAMPLES}. "
                    "Quotations from a pack are verified against the snapshot "
                    "only and are never treated as evidence from the brief."
                ),
            )
            st.caption(
                "The key stays in session memory. The source, any external-source "
                "pack, and the completed dossier are saved under runs/."
            )
            submitted = st.form_submit_button(
                "Translate and save",
                type="primary",
                width="stretch",
            )

        if submitted:
            try:
                with st.spinner("Translating, verifying, critiquing, and saving…"):
                    st.session_state["compoundx_saved_demo_run"] = run_saved_demo(
                        brief_upload=brief_upload,
                        api_key=api_key,
                        model=model,
                        critic_model=critic_model,
                        external_pack_upload=external_pack_upload,
                    )
            except ExternalSourcePackError as error:
                st.error(f"The external-source pack was rejected: {error}")
            except (
                ModelCallError,
                ModelConfigurationError,
                OSError,
                ValidationError,
                OutputExistsError,
                SourceError,
                ValueError,
            ) as error:
                st.error(str(error))

    saved_run = st.session_state.get("compoundx_saved_demo_run")
    with saved_case_slot:
        if isinstance(saved_run, SavedDemoRun):
            st.success("Showing the dossier produced by your live run.")
        else:
            candidates = list_accepted_demo_reports()
            if not candidates:
                st.info(
                    "No saved demonstration is available. Open the live-run panel "
                    "below, upload a brief, and enter an API key once."
                )
                return None
            with st.container(border=True):
                st.markdown("**Saved accepted demonstration - no API key required**")
                selected_report = st.selectbox(
                    "Saved accepted demonstration",
                    options=candidates,
                    format_func=lambda path: path.parents[1].name,
                    label_visibility="collapsed",
                    help=(
                        "Select a frozen accepted report and its immutable review "
                        "history."
                    ),
                )
                # The caption follows the selected case because live and curated
                # runs can legitimately be at different review lifecycle states.
                if any(
                    selected_report.parents[1].glob("section-reviews/*/review.pdf")
                ):
                    st.caption(
                        "Everything below is explorable without a key: the full "
                        "dossier, each verified quotation with its page and "
                        "offsets, the critic's findings, the human review "
                        "history, the final reviewed PDF, and the verification "
                        "evidence."
                    )
                else:
                    st.caption(
                        "Explorable without a key: the full dossier, each "
                        "verified quotation with its page and offsets, the "
                        "critic's findings, and the verification evidence. This "
                        "case has no recorded human review, so it has no review "
                        "history and no final reviewed PDF."
                    )
            try:
                saved_run = load_accepted_demo(selected_report)
            except (OSError, ValidationError, ValueError) as error:
                st.warning(
                    f"The selected saved demonstration could not be loaded: {error}"
                )
                return None

    relative_run = saved_run.run_dir.relative_to(PROJECT_ROOT)
    st.caption(f"Source and dossier saved in {relative_run}")
    _render_report(saved_run.completed)
    return saved_run


def _render_report(completed: CompletedRun) -> None:
    report = completed.report
    validation_by_owner = {
        validation.owner: validation
        for validation in report.translation_validation.contents
    }
    findings = {
        finding.section: finding
        for finding in (report.critic.findings if report.critic else ())
    }

    st.divider()
    st.subheader("Technical translation")
    pages = (
        f" · {report.source.page_count} pages"
        if report.source.page_count is not None
        else ""
    )
    st.caption(
        f"Source: {report.source.name} · "
        f"{report.source.source_format.value.upper()}{pages} · "
        f"SHA-256 {report.source.sha256[:12]}…"
    )
    _render_report_status(report, findings)
    _render_external_sources(report.external_sources)

    st.markdown("### The four translation levels")
    st.caption(
        "Each level constrains the next, so any material-relevant criterion can "
        "be walked back to the sentence in the brief that motivated it."
    )

    primary = (
        (DossierSection.PRODUCT_INTENT, report.translation.product_intent),
        (DossierSection.COMPONENT_FUNCTION, report.translation.component_function),
        (
            DossierSection.PERFORMANCE_TO_VALIDATE,
            report.translation.performance_to_validate,
        ),
        (
            DossierSection.MATERIAL_RELEVANT_CRITERIA,
            report.translation.material_relevant_criteria,
        ),
    )
    for index, (name, section) in enumerate(primary, start=1):
        _render_section(
            index=index,
            name=name,
            section=section,
            validation=validation_by_owner[name.value],
            finding=findings.get(name),
        )

    st.markdown("### Two open-question lists")
    st.caption(
        "What the brief leaves undecided, kept as individually traceable points "
        "instead of being resolved by the model."
    )
    _render_points(
        index=5,
        name=DossierSection.MISSING_INFORMATION,
        owner_prefix="missing_information",
        points=report.translation.missing_information,
        validation_by_owner=validation_by_owner,
        finding=findings.get(DossierSection.MISSING_INFORMATION),
    )
    _render_points(
        index=6,
        name=DossierSection.CONFLICTS_AND_TRADEOFFS,
        owner_prefix="conflicts_and_tradeoffs",
        points=report.translation.conflicts_and_tradeoffs,
        validation_by_owner=validation_by_owner,
        finding=findings.get(DossierSection.CONFLICTS_AND_TRADEOFFS),
    )

    if report.critic_error:
        st.warning(f"Critic pass did not complete: {report.critic_error}")
    elif report.critic is not None:
        additions = (
            len(report.critic.additional_missing_information)
            + len(report.critic.additional_tradeoffs)
            + len(report.critic.additional_open_questions)
        )
        with st.expander(
            f"Content the critic proposed adding · {_plural(additions, 'item')}",
            expanded=bool(additions),
        ):
            _write_points(
                "Additional missing information",
                report.critic.additional_missing_information,
            )
            _write_points(
                "Additional trade-offs",
                report.critic.additional_tradeoffs,
            )
            if report.critic.additional_open_questions:
                st.markdown("**Additional open questions**")
                for question in report.critic.additional_open_questions:
                    st.write(f"• {question}")
            st.caption(
                "Critic proposals are traceable suggestions for the reviewer, not "
                "accepted dossier content."
            )


def _render_report_status(
    report: ReviewReport,
    findings: dict[DossierSection, SectionFinding],
) -> None:
    """State mechanical and critic status up front, before any section text."""

    validation = report.translation_validation
    total = len(validation.contents)
    invalid = validation.invalid_content_count
    flagged = tuple(
        SECTION_LABELS[section]
        for section, finding in findings.items()
        if finding.human_review_required
    )
    # A page number exists only where the source paginates. Markdown and text
    # briefs, which this app accepts, resolve to a character range and no page,
    # so the banner reports the page only when every brief span actually has one.
    brief_spans = tuple(
        span for content in validation.contents for span in content.evidence_spans
    )
    located = (
        "a located page and character range"
        if brief_spans
        and all(getattr(span, "page_number", None) is not None for span in brief_spans)
        else "a located character range"
    )

    if report.translator_repair_call is not None:
        st.info(
            "The initial translation failed deterministic checks, so one bounded "
            "repair pass ran. Both states and both validations are retained in "
            "the model report; the status below describes the revalidated result."
        )
    elif report.translator_repair_error is not None:
        st.error(
            "The bounded translator repair did not complete: "
            f"{report.translator_repair_error}"
        )

    banners = st.columns(2)
    with banners[0]:
        if invalid == 0 and total:
            st.success(
                f"Mechanical checks passed. All {total} traceable statements "
                "resolved every citation exactly against the source it names; "
                f"brief citations carry {located}. No measured value appears "
                "without a citation containing it; digits introduced by a letter "
                "or underscore, or joined directly to a following underscore, "
                "are read as names, not measurements, and are excluded on both "
                "sides."
            )
        else:
            st.error(
                f"{invalid} of {total} traceable statements failed the "
                "deterministic citation and number checks. The failures stay "
                "visible in the sections below."
            )
    with banners[1]:
        critic_validation_errors = (
            report.critic_validation.errors
            if report.critic_validation is not None
            else ()
        )
        if report.critic_error:
            st.error(f"The critic pass did not complete: {report.critic_error}")
        elif critic_validation_errors:
            st.error(
                f"The critic produced {len(critic_validation_errors)} mechanically "
                "invalid evidence or contract entries. Its findings remain visible "
                "but the run is not eligible for acceptance."
            )
        elif flagged:
            st.warning(
                f"The critic flagged {len(flagged)} of 6 sections for human "
                "review: " + ", ".join(flagged) + "."
            )
        elif findings:
            st.info(
                "The critic flagged no section for mandatory human review. "
                "Section-by-section human review is still required."
            )

    counters = st.columns(4)
    counters[0].metric(
        "Statements verified",
        f"{validation.valid_content_count}/{total}",
        border=True,
        help="Every section and every list item is checked independently.",
    )
    counters[1].metric(
        "Critic-flagged sections",
        f"{len(flagged)}/6",
        border=True,
        help="Sections the semantic critic asked a human to resolve.",
    )
    counters[2].metric(
        "Missing information",
        len(report.translation.missing_information),
        border=True,
    )
    counters[3].metric(
        "Conflicts and trade-offs",
        len(report.translation.conflicts_and_tradeoffs),
        border=True,
    )
    st.info(
        "Citation checks prove source traceability, not engineering correctness. "
        "Human review remains required."
    )


def _render_external_sources(sources: tuple[ExternalSource, ...]) -> None:
    """Introduced knowledge is shown apart from the brief, never beside it."""

    if not sources:
        return
    with st.container(border=True):
        st.markdown("**External sources introduced into this run**")
        st.warning(
            "Introduced knowledge, not evidence from the product brief. Each "
            "snapshot was supplied with the run and frozen by content hash "
            "before any model call. Quotations from these snapshots are checked "
            "against the snapshot text only; the system does not establish that "
            "a source is authoritative, current, or applicable to this product."
        )
        st.dataframe(
            [
                {
                    "Source ID": source.source_id,
                    "Title": source.title,
                    "Locator": source.locator,
                    "Retrieved": (
                        _format_ui_timestamp(source.retrieved_at)
                        if source.retrieved_at is not None
                        else "not stated"
                    ),
                    "Content SHA-256": f"{source.content_sha256[:16]}…",
                }
                for source in sources
            ],
            hide_index=True,
            width="stretch",
        )


def _render_section(
    *,
    index: int,
    name: DossierSection,
    section: TranslationSection,
    validation: ContentValidation,
    finding: SectionFinding | None,
) -> None:
    with st.container(border=True):
        title, status = st.columns((4, 1.15))
        title.caption(f"TRANSLATION LEVEL {index} OF 4 · SECTION {index} OF 6")
        title.markdown(f"### {SECTION_LABELS[name]}")
        title.caption(SECTION_PURPOSE[name])
        _status(status, validation.valid)
        st.write(section.text)
        if section.assumptions or section.uncertainties:
            notes = st.columns(2)
            with notes[0]:
                _write_strings(
                    "Assumptions and interpretations", section.assumptions
                )
            with notes[1]:
                _write_strings("Uncertainties", section.uncertainties)
        _render_finding(finding)
        _render_evidence(section, validation)


def _render_points(
    *,
    index: int,
    name: DossierSection,
    owner_prefix: str,
    points: tuple[TraceablePoint, ...],
    validation_by_owner: dict[str, ContentValidation],
    finding: SectionFinding | None,
) -> None:
    with st.container(border=True):
        title, status = st.columns((4, 1.15))
        title.caption(f"OPEN-QUESTION LIST {index - 4} OF 2 · SECTION {index} OF 6")
        title.markdown(f"### {SECTION_LABELS[name]}")
        title.caption(SECTION_PURPOSE[name])
        status.badge(f"{len(points)} traced points", color="blue")
        _render_finding(finding)
        for position, point in enumerate(points, start=1):
            validation = validation_by_owner[f"{owner_prefix}[{position}]"]
            with st.container(border=True):
                text, item_status = st.columns((5, 1.4))
                text.markdown(f"**{index}.{position}** {point.text}")
                _status(item_status, validation.valid)
                _render_evidence(point, validation)


def _render_finding(finding: SectionFinding | None) -> None:
    if finding is None:
        return
    assessment = _ui_status(finding.assessment.value)
    if finding.human_review_required:
        st.warning(
            f"**Critic (semantic pass): {assessment} · flagged for human "
            f"review**\n\n{finding.explanation}"
        )
    else:
        st.info(
            f"**Critic (semantic pass): {assessment}**\n\n{finding.explanation}"
        )
    if finding.suggested_revision:
        st.markdown("**Revision the critic suggests to the reviewer**")
        st.write(finding.suggested_revision)
    support = (
        _ui_status(finding.support_assessment.value)
        if finding.support_assessment is not None
        else "Not recorded by this report version"
    )
    st.caption(f"Semantic support: {support}")
    if finding.issue_types:
        st.caption(
            "Issue types: "
            + ", ".join(_ui_status(issue.value) for issue in finding.issue_types)
        )
    if finding.translated_excerpts:
        st.markdown("**Translated wording under review**")
        for excerpt in finding.translated_excerpts:
            st.code(excerpt, language=None, wrap_lines=True)


def _render_evidence(
    content: TraceableContent,
    validation: ContentValidation,
) -> None:
    parts = [_plural(len(content.evidence_quotes), "brief quotation")]
    if content.external_citations:
        parts.append(_plural(len(content.external_citations), "external quotation"))
    if validation.errors:
        parts.append(_plural(len(validation.errors), "mechanical error"))
    with st.expander("Evidence · " + " · ".join(parts)):
        if content.evidence_quotes:
            st.caption(
                "Quoted from the brief and re-matched by deterministic code, not "
                "by the model."
            )
        for quote in content.evidence_quotes:
            span = next(
                (span for span in validation.evidence_spans if span.quote == quote),
                None,
            )
            if span is None:
                st.code(quote, language=None, wrap_lines=True)
                st.error("This quote was not mechanically verified.")
            else:
                # A running development session can still hold a span created
                # before raw-slice/page provenance was added. Persisted reports
                # are re-ingested through the current schema; this fallback only
                # keeps that transient session renderable after a code reload.
                source_quote = getattr(span, "source_quote", None)
                page_number = getattr(span, "page_number", None)
                st.code(source_quote or quote, language=None, wrap_lines=True)
                page = f"page {page_number} · " if page_number is not None else ""
                st.caption(
                    f"Brief · {page}characters {span.start:,}–{span.end:,} · exact "
                    "match after layout normalisation"
                )
        if content.external_citations:
            st.divider()
            st.markdown("**Introduced external context - not brief evidence**")
        for citation in content.external_citations:
            with st.container(border=True):
                st.warning(
                    f"External source `{citation.source_id}`. Verified against "
                    "that frozen snapshot only, never against the brief."
                )
                st.code(citation.quote, language=None, wrap_lines=True)
                st.caption(
                    f"External snapshot {citation.source_id} · introduced "
                    "knowledge · no page or offset in the product brief"
                )
        for error in validation.errors:
            st.error(f"{error.code.value}: {error.message}")


def _status(container: Any, valid: bool) -> None:
    if valid:
        container.badge("Citations verified", color="green")
    else:
        container.error("Mechanical check failed")


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _write_points(title: str, points: tuple[TraceablePoint, ...]) -> None:
    st.markdown(f"**{title}**")
    if not points:
        st.write("None proposed.")
    for point in points:
        st.write(f"• {point.text}")


def _write_strings(title: str, values: tuple[str, ...]) -> None:
    if not values:
        return
    st.markdown(f"**{title}**")
    for value in values:
        st.write(f"• {value}")


def _render_review_workflow(saved_run: SavedDemoRun) -> None:
    accepted_path = saved_run.accepted_report_path
    if accepted_path is None:
        return

    st.divider()
    st.subheader("Section-by-section human review")
    st.caption(
        "Review each section independently. Model-generated content stays read-only, "
        "and every saved section creates a new immutable review snapshot."
    )
    try:
        accepted_report, accepted_report_sha256 = load_accepted_report(accepted_path)
    except (OSError, ValidationError, SectionReviewLifecycleError) as error:
        st.error(
            "This report is not eligible for human acceptance under the current "
            f"deterministic gates: {error}"
        )
        return
    section_records: list[tuple[Path, SectionReviewArtifact]] = []
    unreadable_terminal = False
    for path in list_section_reviews(saved_run.run_dir):
        try:
            section_records.append((path, load_section_review(path)))
        except (OSError, ValidationError, ValueError) as error:
            st.warning(f"Unreadable section-review artifact {path.name}: {error}")
            unreadable_terminal = unreadable_terminal or (
                path.with_name("review.pdf").is_file()
                or "-finalize-" in path.parent.name
                or "-reject-" in path.parent.name
            )
    terminal_record = _select_terminal_review(
        section_records,
        accepted_report_sha256=accepted_report_sha256,
        source_sha256=accepted_report.source.sha256,
    )
    if terminal_record is not None:
        terminal_path, terminal_artifact = terminal_record
        try:
            _validate_terminal_review_chain(
                run_dir=saved_run.run_dir,
                accepted_report_path=accepted_path,
                accepted_report_sha256=accepted_report_sha256,
                source_sha256=accepted_report.source.sha256,
                terminal_path=terminal_path,
                terminal_artifact=terminal_artifact,
            )
        except (OSError, ValidationError, ValueError) as error:
            st.error(
                "A terminal human-review artifact exists, but its immutable "
                f"parent chain could not be verified: {error}. The review stays "
                "locked rather than reopening a finalized chain."
            )
            return
        _render_terminal_review_summary(terminal_path, terminal_artifact)
        return
    if unreadable_terminal:
        st.error(
            "A terminal human-review artifact exists but could not be read. "
            "The review stays locked rather than reopening a finalized chain."
        )
        return
    active_records = [
        record
        for record in section_records
        if record[1].action is SectionReviewAction.SAVE_SECTION
    ]
    active_path, active_artifact = active_records[-1] if active_records else (None, None)
    chain_key = (
        hashlib.sha256(active_path.as_posix().encode("utf-8")).hexdigest()[:10]
        if active_path is not None
        else "new"
    )
    reviewer_id = st.text_input(
        "Reviewer identifier",
        value=active_artifact.reviewer_id if active_artifact else "",
        disabled=active_artifact is not None,
        key=f"section-reviewer-{chain_key}",
        help="All six decisions in one review chain belong to one reviewer.",
    )

    flash = st.session_state.pop("section_review_flash", None)
    if flash:
        st.success(flash)

    statuses = active_artifact.sections if active_artifact else None
    total_sections = len(ALL_SECTIONS)
    decided = sum(
        bool(statuses.get(section)) if statuses else False
        for section in ALL_SECTIONS
    )
    progress_text = f"{decided} of {total_sections} sections decided"
    if decided < total_sections:
        progress_text += (
            " · finalizing stays locked until every section has a decision"
        )
    st.progress(decided / total_sections, text=progress_text)
    for row in (ALL_SECTIONS[:3], ALL_SECTIONS[3:]):
        columns = st.columns(3)
        for column, section in zip(columns, row, strict=True):
            review = statuses.get(section) if statuses else None
            with column.container(border=True):
                st.caption(SECTION_LABELS[section])
                _review_status_badge(review.status if review else None)

    validation_by_owner = {
        item.owner: item for item in accepted_report.translation_validation.contents
    }
    findings = {
        item.section: item
        for item in (accepted_report.critic.findings if accepted_report.critic else ())
    }
    for index, section in enumerate(NARRATIVE_SECTIONS, start=1):
        current = active_artifact.sections.get(section) if active_artifact else None
        with st.expander(
            f"{index}. {SECTION_LABELS[section]} · "
            f"{_ui_status(current.status.value) if current else 'Pending'}",
            expanded=current is None,
        ):
            _render_narrative_section_review(
                accepted_path=accepted_path,
                report=accepted_report,
                section=section,
                model_validation=validation_by_owner[section.value],
                finding=findings.get(section),
                current=current if isinstance(current, NarrativeSectionReview) else None,
                reviewed_validation=(
                    next(
                        item
                        for item in active_artifact.validation.contents
                        if item.owner == section.value
                    )
                    if active_artifact and current is not None
                    else None
                ),
                reviewer_id=reviewer_id,
                parent_path=active_path,
                chain_key=chain_key,
            )

    for offset, section in enumerate(LIST_SECTIONS, start=5):
        current = active_artifact.sections.get(section) if active_artifact else None
        with st.expander(
            f"{offset}. {SECTION_LABELS[section]} · "
            f"{_ui_status(current.status.value) if current else 'Pending'}",
            expanded=current is None,
        ):
            _render_list_section_review(
                accepted_path=accepted_path,
                report=accepted_report,
                section=section,
                validation_by_owner=validation_by_owner,
                finding=findings.get(section),
                current=current if isinstance(current, ListSectionReview) else None,
                reviewed_validation_by_owner=(
                    {
                        item.owner: item
                        for item in active_artifact.validation.contents
                        if item.owner.startswith(f"{section.value}[")
                    }
                    if active_artifact and current is not None
                    else {}
                ),
                reviewer_id=reviewer_id,
                parent_path=active_path,
                chain_key=chain_key,
            )

    _render_final_review_actions(
        accepted_path=accepted_path,
        active_path=active_path,
        active_artifact=active_artifact,
        reviewer_id=reviewer_id,
        chain_key=chain_key,
    )


def _render_terminal_review_summary(
    review_path: Path,
    artifact: SectionReviewArtifact,
) -> None:
    """Present a published terminal decision without editable review controls."""

    st.success(
        "This human review is finalized and read-only. Its six section decisions "
        "remain available in the parent-linked artifact chain; finalized chains "
        "cannot be reopened or extended."
    )
    values = (
        ("Review status", _ui_status(artifact.overall_status.value)),
        ("Sections reviewed", "6/6"),
        ("Reviewer", artifact.reviewer_id),
        (
            "Mechanical checks",
            (
                "All passed"
                if artifact.validation.invalid_content_count == 0
                else f"{artifact.validation.invalid_content_count} failed"
            ),
        ),
    )
    columns = st.columns(4)
    for column, (label, value) in zip(columns, values, strict=True):
        column.caption(label)
        column.markdown(f"**{value}**")
    if artifact.decision_rationale:
        st.info(f"Reviewer decision: {artifact.decision_rationale}")
    st.caption(
        f"Finalized {_format_ui_timestamp(artifact.created_at)} · "
        f"terminal JSON SHA-256 "
        f"{hashlib.sha256(review_path.read_bytes()).hexdigest()[:12]}… · "
        "Open Final report for the reviewed JSON and PDF."
    )


def _review_status_badge(status: SectionReviewStatus | None) -> None:
    if status is None:
        st.badge("Pending", color="grey")
        return
    colors = {
        SectionReviewStatus.APPROVED_AS_GENERATED: "green",
        SectionReviewStatus.CORRECTED: "blue",
        SectionReviewStatus.NEEDS_FURTHER_REVIEW: "orange",
    }
    st.badge(_ui_status(status.value), color=colors[status])


def _render_narrative_section_review(
    *,
    accepted_path: Path,
    report: ReviewReport,
    section: DossierSection,
    model_validation: ContentValidation,
    finding: SectionFinding | None,
    current: NarrativeSectionReview | None,
    reviewed_validation: ContentValidation | None,
    reviewer_id: str,
    parent_path: Path | None,
    chain_key: str,
) -> None:
    model_content = getattr(report.translation, section.value)
    st.markdown("#### Model-generated translation")
    st.write(model_content.text)
    _write_strings("Model assumptions", model_content.assumptions)
    _write_strings("Model uncertainties", model_content.uncertainties)
    _render_finding(finding)
    _render_evidence(model_content, model_validation)
    st.markdown("#### Human review")

    options: list[SectionReviewStatus | None] = [None, *SectionReviewStatus]
    selected = current.status if current else None
    status = st.selectbox(
        "Section status",
        options=options,
        index=options.index(selected),
        format_func=lambda value: (
            "Choose a section status"
            if value is None
            else value.value.replace("_", " ").title()
        ),
        key=f"{chain_key}-{section.value}-status",
    )
    base_reviewed = current.reviewed_content if current else model_content
    if status is SectionReviewStatus.CORRECTED:
        text = st.text_area(
            "Human-reviewed translation",
            value=base_reviewed.text,
            height=150,
            key=f"{chain_key}-{section.value}-text",
        )
        assumptions_json = st.text_area(
            "Reviewed assumptions (JSON list)",
            value=_json_string_list(base_reviewed.assumptions),
            key=f"{chain_key}-{section.value}-assumptions",
        )
        uncertainties_json = st.text_area(
            "Reviewed uncertainties (JSON list)",
            value=_json_string_list(base_reviewed.uncertainties),
            key=f"{chain_key}-{section.value}-uncertainties",
        )
        evidence_json = st.text_area(
            "Reviewed source evidence (JSON list)",
            value=_json_string_list(base_reviewed.evidence_quotes),
            height=160,
            key=f"{chain_key}-{section.value}-evidence",
        )
        external_json = st.text_area(
            "Reviewed external citations (JSON list)",
            value=json.dumps(
                [item.model_dump(mode="json") for item in base_reviewed.external_citations],
                ensure_ascii=False,
                indent=2,
            ),
            key=f"{chain_key}-{section.value}-external",
        )
        rationale = st.text_area(
            "Reason for correction",
            value=current.rationale if current and current.rationale else "",
            key=f"{chain_key}-{section.value}-rationale",
        )
        note = None
    elif status is SectionReviewStatus.NEEDS_FURTHER_REVIEW:
        st.text_area(
            "Human-reviewed translation",
            value=model_content.text,
            disabled=True,
            key=f"{chain_key}-{section.value}-unresolved-text",
        )
        text = model_content.text
        assumptions_json = _json_string_list(model_content.assumptions)
        uncertainties_json = _json_string_list(model_content.uncertainties)
        evidence_json = _json_string_list(model_content.evidence_quotes)
        external_json = json.dumps(
            [item.model_dump(mode="json") for item in model_content.external_citations]
        )
        rationale = None
        note = st.text_area(
            "Review note",
            value=current.reviewer_note if current and current.reviewer_note else "",
            key=f"{chain_key}-{section.value}-note",
        )
    else:
        st.text_area(
            "Human-reviewed translation",
            value=model_content.text,
            disabled=True,
            key=f"{chain_key}-{section.value}-approved-text",
        )
        text = model_content.text
        assumptions_json = _json_string_list(model_content.assumptions)
        uncertainties_json = _json_string_list(model_content.uncertainties)
        evidence_json = _json_string_list(model_content.evidence_quotes)
        external_json = json.dumps(
            [item.model_dump(mode="json") for item in model_content.external_citations]
        )
        rationale = None
        note = None

    if reviewed_validation is not None:
        if reviewed_validation.valid:
            st.success("Saved human review passes citation and number checks.")
        else:
            st.error("Saved human review has mechanical validation errors.")
            for error in reviewed_validation.errors:
                st.write(f"• {error.code.value}: {error.message}")
    if st.button(
        f"Save {SECTION_LABELS[section]} review",
        key=f"{chain_key}-{section.value}-save",
        width="stretch",
    ):
        try:
            if status is None:
                raise ValueError("choose a section status before saving")
            if status is SectionReviewStatus.APPROVED_AS_GENERATED:
                review = build_approved_narrative_review(report, section)
            else:
                reviewed = TranslationSection(
                    text=text,
                    assumptions=_parse_string_list(assumptions_json),
                    uncertainties=_parse_string_list(uncertainties_json),
                    evidence_quotes=_parse_string_list(evidence_json),
                    external_citations=_parse_external_citations(external_json),
                )
                review = NarrativeSectionReview(
                    section=section,
                    status=status,
                    model_content=model_content,
                    reviewed_content=reviewed,
                    rationale=rationale,
                    reviewer_note=note,
                )
            saved = save_section_review(
                accepted_report_path=accepted_path,
                reviewer_id=reviewer_id,
                review=review,
                parent_review_path=parent_path,
            )
        except (
            OSError,
            SectionReviewLifecycleError,
            SectionReviewOutputExistsError,
            ValidationError,
            ValueError,
        ) as error:
            st.error(str(error))
        else:
            st.session_state["section_review_flash"] = (
                f"Saved {SECTION_LABELS[section]} as {_ui_status(review.status.value)}."
            )
            st.rerun()


def _render_list_section_review(
    *,
    accepted_path: Path,
    report: ReviewReport,
    section: DossierSection,
    validation_by_owner: dict[str, ContentValidation],
    finding: SectionFinding | None,
    current: ListSectionReview | None,
    reviewed_validation_by_owner: dict[str, ContentValidation],
    reviewer_id: str,
    parent_path: Path | None,
    chain_key: str,
) -> None:
    model_points = getattr(report.translation, section.value)
    st.markdown("#### Model-generated items")
    _render_finding(finding)
    current_by_index = {
        item.model_index: item
        for item in (current.items if current else ())
        if item.model_index is not None
    }
    additions = tuple(
        item
        for item in (current.items if current else ())
        if item.status is ListItemReviewStatus.ADDED
    )
    item_values = []
    for index, point in enumerate(model_points):
        st.markdown(f"**Model-generated item {index + 1}**")
        st.write(point.text)
        _render_evidence(
            point,
            validation_by_owner[f"{section.value}[{index + 1}]"],
        )
        saved_item = current_by_index.get(index)
        item_options = (
            ListItemReviewStatus.APPROVED_AS_GENERATED,
            ListItemReviewStatus.CORRECTED,
            ListItemReviewStatus.REMOVED,
        )
        item_status = st.selectbox(
            f"Human decision for item {index + 1}",
            options=item_options,
            index=(
                item_options.index(saved_item.status)
                if saved_item and saved_item.status in item_options
                else 0
            ),
            format_func=lambda value: value.value.replace("_", " ").title(),
            key=f"{chain_key}-{section.value}-item-{index}-status",
        )
        if item_status is ListItemReviewStatus.CORRECTED:
            reviewed_item = saved_item.reviewed_item if saved_item else point
            corrected_text = st.text_area(
                f"Corrected item {index + 1}",
                value=reviewed_item.text if reviewed_item else point.text,
                key=f"{chain_key}-{section.value}-item-{index}-text",
            )
            evidence_json = st.text_area(
                f"Reviewed evidence for item {index + 1} (JSON list)",
                value=_json_string_list(
                    reviewed_item.evidence_quotes if reviewed_item else point.evidence_quotes
                ),
                key=f"{chain_key}-{section.value}-item-{index}-evidence",
            )
            external_json = st.text_area(
                f"Reviewed external citations for item {index + 1} (JSON list)",
                value=json.dumps(
                    [
                        citation.model_dump(mode="json")
                        for citation in (
                            reviewed_item.external_citations
                            if reviewed_item
                            else point.external_citations
                        )
                    ],
                    ensure_ascii=False,
                    indent=2,
                ),
                key=f"{chain_key}-{section.value}-item-{index}-external",
            )
            rationale = st.text_area(
                f"Reason for correcting item {index + 1}",
                value=saved_item.rationale if saved_item and saved_item.rationale else "",
                key=f"{chain_key}-{section.value}-item-{index}-rationale",
            )
        elif item_status is ListItemReviewStatus.REMOVED:
            corrected_text = None
            evidence_json = "[]"
            external_json = "[]"
            rationale = st.text_area(
                f"Reason for removing item {index + 1}",
                value=saved_item.rationale if saved_item and saved_item.rationale else "",
                key=f"{chain_key}-{section.value}-item-{index}-rationale",
            )
        else:
            corrected_text = point.text
            evidence_json = _json_string_list(point.evidence_quotes)
            external_json = json.dumps(
                [item.model_dump(mode="json") for item in point.external_citations]
            )
            rationale = None
        item_values.append(
            (
                index,
                point,
                item_status,
                corrected_text,
                evidence_json,
                external_json,
                rationale,
            )
        )

    added_count = st.number_input(
        "Number of human-added items",
        min_value=0,
        max_value=10,
        value=len(additions),
        step=1,
        key=f"{chain_key}-{section.value}-added-count",
    )
    added_values = []
    for index in range(int(added_count)):
        saved_item = additions[index] if index < len(additions) else None
        reviewed_item = saved_item.reviewed_item if saved_item else None
        st.markdown(f"**Human-added item {index + 1}**")
        added_values.append(
            (
                st.text_area(
                    f"Added item {index + 1}",
                    value=reviewed_item.text if reviewed_item else "",
                    key=f"{chain_key}-{section.value}-added-{index}-text",
                ),
                st.text_area(
                    f"Evidence for added item {index + 1} (JSON list)",
                    value=_json_string_list(
                        reviewed_item.evidence_quotes if reviewed_item else ()
                    ),
                    key=f"{chain_key}-{section.value}-added-{index}-evidence",
                ),
                st.text_area(
                    f"External citations for added item {index + 1} (JSON list)",
                    value=json.dumps(
                        [
                            citation.model_dump(mode="json")
                            for citation in (
                                reviewed_item.external_citations
                                if reviewed_item
                                else ()
                            )
                        ],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    key=f"{chain_key}-{section.value}-added-{index}-external",
                ),
                st.text_area(
                    f"Reason for adding item {index + 1}",
                    value=(saved_item.rationale if saved_item and saved_item.rationale else ""),
                    key=f"{chain_key}-{section.value}-added-{index}-rationale",
                ),
            )
        )

    section_options: list[SectionReviewStatus | None] = [None, *SectionReviewStatus]
    selected = current.status if current else None
    section_status = st.selectbox(
        "Section status",
        options=section_options,
        index=section_options.index(selected),
        format_func=lambda value: (
            "Choose a section status"
            if value is None
            else value.value.replace("_", " ").title()
        ),
        key=f"{chain_key}-{section.value}-status",
    )
    reviewer_note = (
        st.text_area(
            "Review note",
            value=current.reviewer_note if current and current.reviewer_note else "",
            key=f"{chain_key}-{section.value}-note",
        )
        if section_status is SectionReviewStatus.NEEDS_FURTHER_REVIEW
        else None
    )
    if reviewed_validation_by_owner:
        invalid = sum(
            not item.valid for item in reviewed_validation_by_owner.values()
        )
        if invalid:
            st.error(f"Saved human review has {invalid} invalid list items.")
        else:
            st.success("Saved human review passes citation and number checks.")
    if st.button(
        f"Save {SECTION_LABELS[section]} review",
        key=f"{chain_key}-{section.value}-save",
        width="stretch",
    ):
        try:
            if section_status is None:
                raise ValueError("choose a section status before saving")
            items = []
            for (
                model_index,
                model_item,
                item_status,
                corrected_text,
                evidence_json,
                external_json,
                rationale,
            ) in item_values:
                reviewed_item = None
                if item_status is not ListItemReviewStatus.REMOVED:
                    reviewed_item = TraceablePoint(
                        text=corrected_text,
                        evidence_quotes=_parse_string_list(evidence_json),
                        external_citations=_parse_external_citations(external_json),
                    )
                items.append(
                    ListItemReview(
                        status=item_status,
                        model_index=model_index,
                        model_item=model_item,
                        reviewed_item=reviewed_item,
                        rationale=rationale,
                    )
                )
            for text, evidence_json, external_json, rationale in added_values:
                items.append(
                    ListItemReview(
                        status=ListItemReviewStatus.ADDED,
                        reviewed_item=TraceablePoint(
                            text=text,
                            evidence_quotes=_parse_string_list(evidence_json),
                            external_citations=_parse_external_citations(external_json),
                        ),
                        rationale=rationale,
                    )
                )
            review = ListSectionReview(
                section=section,
                status=section_status,
                items=tuple(items),
                reviewer_note=reviewer_note,
            )
            saved = save_section_review(
                accepted_report_path=accepted_path,
                reviewer_id=reviewer_id,
                review=review,
                parent_review_path=parent_path,
            )
        except (
            OSError,
            SectionReviewLifecycleError,
            SectionReviewOutputExistsError,
            ValidationError,
            ValueError,
        ) as error:
            st.error(str(error))
        else:
            st.session_state["section_review_flash"] = (
                f"Saved {SECTION_LABELS[section]} as {_ui_status(review.status.value)}."
            )
            st.rerun()


def _render_final_review_actions(
    *,
    accepted_path: Path,
    active_path: Path | None,
    active_artifact,
    reviewer_id: str,
    chain_key: str,
) -> None:
    st.divider()
    st.subheader("Final reviewer decision")
    complete = bool(active_artifact and active_artifact.sections.complete)
    if active_artifact:
        invalid = active_artifact.validation.invalid_content_count
        mechanical = (
            "reviewed content still passes every citation and number check"
            if invalid == 0
            else f"{invalid} reviewed statements now fail the mechanical checks"
        )
        st.info(
            f"Derived status preview: "
            f"{_ui_status(active_artifact.overall_status.value)} · {mechanical}."
        )
    if not complete:
        st.warning("Review all six sections before finalizing or rejecting the dossier.")
    decision_rationale = st.text_area(
        "Overall decision rationale",
        key=f"{chain_key}-overall-rationale",
    )
    actions = st.columns(2)
    finalize = actions[0].button(
        "Finalize reviewed report",
        type="primary",
        disabled=not complete,
        width="stretch",
        key=f"{chain_key}-finalize",
    )
    reject = actions[1].button(
        "Reject whole dossier",
        disabled=not complete,
        width="stretch",
        key=f"{chain_key}-reject",
    )
    if finalize or reject:
        try:
            if active_path is None:
                raise ValueError("save all six section reviews first")
            saved = finalize_section_review(
                accepted_report_path=accepted_path,
                reviewer_id=reviewer_id,
                parent_review_path=active_path,
                decision_rationale=decision_rationale,
                reject=reject,
            )
        except (
            OSError,
            SectionReviewLifecycleError,
            SectionReviewOutputExistsError,
            ValidationError,
            ValueError,
        ) as error:
            st.error(str(error))
        else:
            st.success(
                f"Final review saved as "
                f"{_ui_status(saved.artifact.overall_status.value)}. "
                "Open Final report to preview and download it."
            )


def _render_final_report_tab(saved_run: SavedDemoRun) -> None:
    st.subheader("Final reviewed report")
    st.write(
        "This is the engineering deliverable produced after translation, "
        "mechanical verification, criticism, and human section review."
    )
    accepted_path = saved_run.accepted_report_path
    if accepted_path is None:
        st.info(
            "No finalized reviewed report exists yet. Complete all six decisions "
            "and finalize the dossier in Review."
        )
        _render_original_report_download(saved_run)
        return
    accepted_report_sha256 = hashlib.sha256(accepted_path.read_bytes()).hexdigest()
    records: list[tuple[Path, SectionReviewArtifact]] = []
    for path in list_section_reviews(saved_run.run_dir):
        try:
            artifact = load_section_review(path)
        except (OSError, ValidationError, ValueError):
            continue
        records.append((path, artifact))
    terminal_record = _select_terminal_review(
        records,
        accepted_report_sha256=accepted_report_sha256,
        source_sha256=saved_run.completed.report.source.sha256,
    )
    if terminal_record is None:
        st.info(
            "No finalized reviewed report exists yet. Complete all six decisions "
            "and finalize the dossier in Review."
        )
        _render_original_report_download(saved_run)
        return

    review_path, artifact = terminal_record
    try:
        _validate_terminal_review_chain(
            run_dir=saved_run.run_dir,
            accepted_report_path=accepted_path,
            accepted_report_sha256=accepted_report_sha256,
            source_sha256=saved_run.completed.report.source.sha256,
            terminal_path=review_path,
            terminal_artifact=artifact,
        )
        _, pdf_path = _validate_final_review_outputs(review_path)
    except (OSError, ValidationError, ValueError) as error:
        st.error(f"The final reviewed deliverable could not be verified: {error}")
        _render_original_report_download(saved_run)
        return
    section_reviews = [artifact.sections.get(section) for section in ALL_SECTIONS]
    corrected_sections = sum(
        review.status is SectionReviewStatus.CORRECTED
        for review in section_reviews
        if review is not None
    )
    invalid = artifact.validation.invalid_content_count
    summary_values = (
        ("Review status", _ui_status(artifact.overall_status.value)),
        (
            "Sections reviewed",
            f"{sum(review is not None for review in section_reviews)}/6",
        ),
        ("Corrected sections", str(corrected_sections)),
        (
            "Mechanical checks",
            "All passed" if invalid == 0 else f"{invalid} failed",
        ),
        ("Reviewer", artifact.reviewer_id),
    )
    with st.container(border=True):
        summary_columns = st.columns((2.1, 1, 1, 1, 1.8))
        for column, (label, value) in zip(
            summary_columns, summary_values, strict=True
        ):
            column.caption(label)
            column.markdown(f"**{value}**")
    st.caption(
        f"Finalized {_format_ui_timestamp(artifact.created_at)} · "
        f"Reviewed JSON SHA-256 {hashlib.sha256(review_path.read_bytes()).hexdigest()[:12]}…"
    )
    if artifact.decision_rationale:
        st.info(f"Reviewer decision: {artifact.decision_rationale}")

    st.markdown("### Preview final PDF")
    st.pdf(
        pdf_path.read_bytes(),
        height=870,
        key=f"final-pdf-preview-{review_path.parent.name}",
    )
    st.caption(
        "If the embedded viewer is unavailable in your browser, use the PDF "
        "download below."
    )

    downloads = st.columns(3)
    downloads[0].download_button(
        "Download reviewed PDF",
        data=pdf_path.read_bytes(),
        file_name="compoundx-reviewed-translation.pdf",
        mime="application/pdf",
        key=f"final-pdf-{review_path.parent.name}",
        width="stretch",
    )
    downloads[1].download_button(
        "Download reviewed JSON",
        data=review_path.read_bytes(),
        file_name="compoundx-reviewed-translation.json",
        mime="application/json",
        key=f"final-json-{review_path.parent.name}",
        width="stretch",
    )
    downloads[2].download_button(
        "Download original model report",
        data=saved_run.completed.report_json,
        file_name="compoundx-model-report.json",
        mime="application/json",
        key=f"model-json-{review_path.parent.name}",
        width="stretch",
    )
    _render_linked_supporting_evaluation(
        run_dir=saved_run.run_dir,
        final_review_path=review_path,
        source_sha256=artifact.source_sha256,
        reviewer_id=artifact.reviewer_id,
    )


def _render_linked_supporting_evaluation(
    *,
    run_dir: Path,
    final_review_path: Path,
    source_sha256: str,
    reviewer_id: str,
) -> None:
    try:
        evaluation = _load_linked_supporting_evaluation(
            run_dir=run_dir,
            final_review_path=final_review_path,
            source_sha256=source_sha256,
        )
    except (OSError, ValidationError, ValueError) as error:
        st.warning(
            "The optional semantic evaluation was not shown because its link to "
            f"this final review could not be verified: {error}"
        )
        return
    if evaluation is None:
        return

    st.markdown("### Model-assisted supporting evidence")
    st.info(
        "This rubric is supporting evidence only. It is separate from the "
        f"deterministic eligibility gate and from {reviewer_id}'s human "
        "review decision; its scores never change either status."
    )
    st.caption(
        f"Evaluator: {evaluation.evaluator} · "
        f"Evaluated {_format_ui_timestamp(evaluation.evaluated_at)} · "
        f"Exact reviewed JSON SHA-256 {evaluation.report_sha256[:12]}…"
    )
    overview = st.columns(3)
    overview[0].metric(
        "Supporting rubric result",
        "PASS" if evaluation.overall_pass else "FAIL",
        border=True,
    )
    overview[1].metric(
        "Mean rubric score",
        f"{evaluation.mean_score:.2f}/5",
        border=True,
    )
    overview[2].metric(
        "Adversarial checks",
        f"{sum(check.passed for check in evaluation.adversarial_checks)}/"
        f"{len(evaluation.adversarial_checks)}",
        border=True,
    )
    with st.expander("Supporting semantic rubric", expanded=False):
        st.dataframe(
            [
                {
                    "Criterion": _ui_status(score.criterion.value),
                    "Score": f"{score.score}/5",
                    "Rationale": score.rationale,
                }
                for score in evaluation.scores
            ],
            hide_index=True,
            width="stretch",
        )
    with st.expander("Supporting adversarial checks", expanded=False):
        for check in evaluation.adversarial_checks:
            st.markdown(
                f"**{'PASS' if check.passed else 'FAIL'} · "
                f"{_ui_status(check.check_id)}**"
            )
            st.write(check.observed_treatment)
    st.caption(evaluation.summary)


def _render_original_report_download(saved_run: SavedDemoRun) -> None:
    st.download_button(
        "Download original model report",
        data=saved_run.completed.report_json,
        file_name="compoundx-model-report.json",
        mime="application/json",
        width="stretch",
    )


def _render_verification_tab(saved_run: SavedDemoRun | None) -> None:
    st.subheader("Verification evidence")
    st.write(
        "The customer-facing workflow is separated from the engineering "
        "evaluation used to challenge the prototype."
    )
    if saved_run is not None:
        report = saved_run.completed.report
        validation = report.translation_validation
        invalid = validation.invalid_content_count
        st.markdown(f"### Selected case - {saved_run.run_dir.name}")
        if invalid == 0:
            st.success(
                f"All {len(validation.contents)} traceable statements passed the "
                "deterministic citation and number checks."
            )
        else:
            st.error(
                f"{invalid} of {len(validation.contents)} traceable statements "
                "failed the deterministic checks."
            )
        target = st.columns(4)
        target[0].metric(
            "Statements verified",
            f"{validation.valid_content_count}/{len(validation.contents)}",
            border=True,
        )
        target[1].metric("Mechanical errors", invalid, border=True)
        target[2].metric(
            "Revisions requested",
            sum(
                finding.assessment.value == "revise"
                for finding in (report.critic.findings if report.critic else ())
            ),
            border=True,
            help="Sections the critic assessed as needing revision.",
        )
        target[3].metric(
            "Flagged for review",
            sum(
                finding.human_review_required
                for finding in (report.critic.findings if report.critic else ())
            ),
            border=True,
            help="Sections the critic flagged as requiring human resolution.",
        )
        terminal_review = None
        accepted_path = saved_run.accepted_report_path
        if accepted_path is not None:
            review_records = []
            for review_path in list_section_reviews(saved_run.run_dir):
                try:
                    review_records.append(
                        (review_path, load_section_review(review_path))
                    )
                except (OSError, ValidationError, ValueError):
                    continue
            terminal_review = _select_terminal_review(
                review_records,
                accepted_report_sha256=hashlib.sha256(
                    accepted_path.read_bytes()
                ).hexdigest(),
                source_sha256=report.source.sha256,
            )
        if terminal_review is None:
            st.caption(
                "The selected demonstration preserves a mechanically eligible "
                "model report. Human review has not been finalized for this "
                "exact report hash."
            )
        else:
            terminal_path, terminal_artifact = terminal_review
            try:
                _validate_terminal_review_chain(
                    run_dir=saved_run.run_dir,
                    accepted_report_path=accepted_path,
                    accepted_report_sha256=hashlib.sha256(
                        accepted_path.read_bytes()
                    ).hexdigest(),
                    source_sha256=report.source.sha256,
                    terminal_path=terminal_path,
                    terminal_artifact=terminal_artifact,
                )
            except (OSError, ValidationError, ValueError) as error:
                st.error(
                    "The terminal human-review chain could not be verified: "
                    f"{error}. Its supporting rubric is not shown."
                )
            else:
                st.caption(
                    "The selected model report is mechanically eligible and has an "
                    "exactly linked terminal human-review artifact. The separate "
                    "supporting rubric, when present, does not affect either status."
                )
                _render_linked_supporting_evaluation(
                    run_dir=saved_run.run_dir,
                    final_review_path=terminal_path,
                    source_sha256=terminal_artifact.source_sha256,
                    reviewer_id=terminal_artifact.reviewer_id,
                )
    else:
        st.info("Select a saved or newly completed case to inspect its evidence.")

    with st.expander("Known limitations"):
        if saved_run is not None:
            st.caption(
                "Read directly from the selected immutable model report."
            )
            for limitation in saved_run.completed.report.known_limitations:
                st.write(f"- {limitation}")
        st.write(
            "- Deterministic checks prove exact source traceability and numeric "
            "provenance, not semantic entailment or engineering correctness."
        )
        st.write(
            "- The model critic is not independent expert evaluation. The named "
            "review records one person's decision, not multi-reviewer confirmation."
        )


def _format_ui_timestamp(value: datetime) -> str:
    utc = value.astimezone(UTC)
    return f"{utc.day} {utc:%B %Y, %H:%M UTC}"


def _parse_string_list(value: str) -> tuple[str, ...]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or any(
        not isinstance(item, str) or not item.strip() for item in parsed
    ):
        raise ValueError("expected a JSON list of non-empty strings")
    return tuple(parsed)


def _parse_external_citations(value: str) -> tuple[ExternalCitation, ...]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError("expected external citations as a JSON list")
    return tuple(ExternalCitation.model_validate(item) for item in parsed)


def _json_string_list(values) -> str:
    return json.dumps(list(values), ensure_ascii=False, indent=2)


def _ui_status(value: str) -> str:
    return value.replace("_", " ").capitalize()




if __name__ == "__main__":
    main()

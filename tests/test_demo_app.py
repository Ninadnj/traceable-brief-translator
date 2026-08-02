from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from compoundx import demo_app
from streamlit.testing.v1 import AppTest


class Upload:
    name = "../target.pdf"

    @staticmethod
    def getvalue() -> bytes:
        return b"demo pdf bytes"


def test_demo_landing_page_requests_a_session_only_key() -> None:
    app = AppTest.from_file("streamlit_app.py").run(timeout=30)

    assert not app.exception
    assert app.title[0].value == "Traceable technical translation"
    assert [tab.label for tab in app.tabs] == [
        "1. Translate",
        "2. Review",
        "3. Final report",
        "4. Verification",
    ]
    assert [field.label for field in app.text_input][:3] == [
        "OpenAI API key - this browser session only",
        "Translator model",
        "Optional distinct critic model",
    ]
    buttons = [button.label for button in app.button]
    assert "Save Product intent review" in buttons
    assert "Save Missing information review" in buttons
    assert "Finalize reviewed report" in buttons
    assert "Reject whole dossier" in buttons
    # Fresh accepted reports intentionally have no human review chain yet.
    assert "Download reviewed PDF" not in [
        item.label for item in app.get("download_button")
    ]
    assert "Download reviewed JSON" not in [
        item.label for item in app.get("download_button")
    ]
    assert "Download original model report" in [
        item.label for item in app.get("download_button")
    ]


def test_final_pdf_uses_streamlits_native_viewer() -> None:
    source = Path("src/compoundx/demo_app.py").read_text(encoding="utf-8")

    assert "st.pdf(" in source
    assert "data:application/pdf" not in source


def test_run_saved_demo_keeps_source_and_reports_without_saving_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client_arguments: list[dict[str, str]] = []

    class FakeClient:
        def __init__(self, **arguments: str) -> None:
            client_arguments.append(arguments)

    def fake_run_translation(**arguments: object) -> SimpleNamespace:
        output_dir = Path(arguments["output_dir"])
        output_dir.mkdir()
        json_path = output_dir / "report.json"
        markdown_path = output_dir / "report.md"
        json_path.write_bytes(b'{"translation": "saved"}\n')
        markdown_path.write_bytes(b"# Saved translation\n")
        return SimpleNamespace(
            report=object(),
            json_path=json_path,
            markdown_path=markdown_path,
        )

    monkeypatch.setattr(demo_app, "OpenAIModelClient", FakeClient)
    monkeypatch.setattr(demo_app, "run_translation", fake_run_translation)

    saved = demo_app.run_saved_demo(
        brief_upload=Upload(),
        api_key="secret-live-key",
        model="translator-model",
        critic_model="",
        results_root=tmp_path,
    )

    assert saved.source_path.name == "target.pdf"
    assert saved.source_path.read_bytes() == b"demo pdf bytes"
    assert saved.completed.report_json == b'{"translation": "saved"}\n'
    assert saved.completed.report_markdown == b"# Saved translation\n"
    assert client_arguments == [
        {"model": "translator-model", "api_key": "secret-live-key"}
    ]
    assert "secret-live-key" not in "".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in saved.run_dir.rglob("*")
        if path.is_file()
    )


def test_saved_source_directory_is_immutable_and_content_addressed(
    tmp_path: Path,
) -> None:
    saved = demo_app.save_demo_source(
        uploaded_name="../../Roller Skate.pdf",
        content=b"brief",
        results_root=tmp_path,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert saved.source_path == saved.run_dir / "source" / "Roller Skate.pdf"
    assert saved.source_path.read_bytes() == b"brief"
    assert saved.run_dir.name.startswith("Roller-Skate-20260801T000000000000Z-")

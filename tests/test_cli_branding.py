from __future__ import annotations

import tomllib
from pathlib import Path

from compoundx.main import build_parser


def test_compoundx_demo_name_is_consistent_in_cli_and_readme() -> None:
    project_root = Path(__file__).parents[1]
    configuration = tomllib.loads(
        (project_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    readme = (project_root / "README.md").read_text(encoding="utf-8")

    assert configuration["project"]["name"] == "compoundx-demo"
    assert configuration["project"]["scripts"] == {
        "compoundx-demo": "compoundx.main:main"
    }
    assert build_parser().prog == "compoundx-demo"
    assert "compoundx-demo \\" in readme
    assert "compoundx-v2" not in readme


def test_repository_uses_one_current_package_and_demo_layout() -> None:
    project_root = Path(__file__).parents[1]

    assert (project_root / "src/compoundx/demo_app.py").is_file()
    assert not (project_root / "src/compoundx/v2").exists()
    assert {
        path.name
        for path in (project_root / "demo-results").iterdir()
        if path.is_dir()
    } == {"garden-trimmer", "roller-skate"}

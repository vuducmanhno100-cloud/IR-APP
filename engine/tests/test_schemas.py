import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from ir_engine import schemas

from .conftest import CONTRACTS, REPO

SPEC = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))


def response_examples():
    for path, ops in SPEC["paths"].items():
        for method, op in ops.items():
            if not isinstance(op, dict):
                continue
            for status, resp in op.get("responses", {}).items():
                if "$ref" in resp:
                    resp = SPEC["components"]["responses"][resp["$ref"].rsplit("/", 1)[1]]
                media = resp.get("content", {}).get("application/json", {})
                ref = media.get("schema", {}).get("$ref")
                if ref and "example" in media:
                    yield f"{method.upper()} {path} {status}", ref.rsplit("/", 1)[1], media["example"]


def test_contract_examples_validate_against_generated_models():
    checked = 0
    for label, name, example in response_examples():
        try:
            getattr(schemas, name).model_validate(example)
        except Exception as exc:
            raise AssertionError(label) from exc
        checked += 1
    assert checked >= 10


def test_generated_models_are_up_to_date(tmp_path):
    if not shutil.which("datamodel-codegen"):
        pytest.skip("datamodel-codegen not installed")
    script = (REPO / "engine" / "scripts" / "gen_schemas.sh").read_text(encoding="utf-8")
    args = [a for a in script.split("datamodel-codegen", 1)[1].replace("\\\n", " ").split()]
    out = tmp_path / "schemas.py"
    args[args.index("--output") + 1] = str(out)
    subprocess.run(["datamodel-codegen", *args], cwd=REPO, check=True, capture_output=True)
    current = Path(schemas.__file__).read_text(encoding="utf-8")
    assert out.read_text(encoding="utf-8") == current, "run engine/scripts/gen_schemas.sh"

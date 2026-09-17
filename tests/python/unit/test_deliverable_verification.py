"""
Unit tests for Autonomous Deliverable Verification Gate and Smart .ipynb Handling in direct_loop.
"""

import json
from pathlib import Path
import pytest

from aja.orchestration.direct_loop import (
    _extract_claimed_deliverables,
    run_direct_loop,
)
from aja.orchestration.tools.native import NativeToolRegistry


def test_extract_claimed_deliverables():
    # 1. Test standard AJA completion message
    msg1 = (
        "The conversion process has been completed, and the Jupyter notebook has been successfully created and saved as:\n\n"
        "**D:\\DATA SCIENCE\\EDA projects\\CSVs\\titanic_eda.ipynb'**\n\n"
        "You can now open this notebook in Jupyter."
    )
    extracted1 = _extract_claimed_deliverables(msg1)
    assert len(extracted1) == 1
    assert extracted1[0] == r"D:\DATA SCIENCE\EDA projects\CSVs\titanic_eda.ipynb"

    # 2. Test EDA script saved message
    msg2 = (
        "The EDA analysis has been successfully completed and saved as a Python script at the following location:\n\n"
        "**D:\\DATA SCIENCE\\EDA projects\\CSVs\\titanic_eda_analysis.py**\n\n"
        "This script includes descriptive statistics."
    )
    extracted2 = _extract_claimed_deliverables(msg2)
    assert len(extracted2) == 1
    assert extracted2[0] == r"D:\DATA SCIENCE\EDA projects\CSVs\titanic_eda_analysis.py"

    # 3. Test relative path creation
    msg3 = "Created and saved as: `analysis_output.csv`"
    extracted3 = _extract_claimed_deliverables(msg3)
    assert "analysis_output.csv" in extracted3

    # 4. Ordinary conversation without deliverables
    msg4 = "I checked the dataset and found 891 rows. Let me know if you would like me to plot charts."
    extracted4 = _extract_claimed_deliverables(msg4)
    assert len(extracted4) == 0


def test_write_file_auto_wraps_ipynb(tmp_path: Path):
    import aja.config
    orig_root = aja.config.PROJECT_ROOT
    try:
        aja.config.PROJECT_ROOT = tmp_path
        reg = NativeToolRegistry()
        
        # 1. Plain python code passed to .ipynb path should be wrapped in valid notebook JSON
        ipynb_path = tmp_path / "test_notebook.ipynb"
        py_code = "import pandas as pd\nimport matplotlib.pyplot as plt\n\nprint('hello')"
        
        res = reg.write_file(str(ipynb_path), py_code)
        assert "Successfully wrote" in res
        assert ipynb_path.exists()

        # Read and parse as JSON
        content = ipynb_path.read_text(encoding="utf-8")
        data = json.loads(content)
        assert "cells" in data
        assert data["nbformat"] == 4
        assert len(data["cells"]) == 1
        assert data["cells"][0]["cell_type"] == "code"
        assert "import pandas as pd\n" in "".join(data["cells"][0]["source"])

        # 2. Multi-cell python code with '# %%' splitters
        multi_cell_path = tmp_path / "multi_cell.ipynb"
        multi_code = "# %% Cell 1\nimport numpy as np\n# %% Cell 2\nx = np.arange(10)"
        res_multi = reg.write_file(str(multi_cell_path), multi_code)
        assert "Successfully wrote" in res_multi

        data_multi = json.loads(multi_cell_path.read_text(encoding="utf-8"))
        assert len(data_multi["cells"]) == 2

        # 3. Pre-formatted valid JSON to .ipynb should remain intact
        valid_json_path = tmp_path / "already_json.ipynb"
        valid_json = json.dumps({"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 2})
        reg.write_file(str(valid_json_path), valid_json)
        data_valid = json.loads(valid_json_path.read_text(encoding="utf-8"))
        assert data_valid["nbformat_minor"] == 2

        # 4. Regular .py file is written as plain text
        py_file = tmp_path / "script.py"
        reg.write_file(str(py_file), py_code)
        assert py_file.read_text(encoding="utf-8") == py_code
    finally:
        aja.config.PROJECT_ROOT = orig_root


def test_direct_loop_intercepts_missing_deliverable(tmp_path: Path):
    async def _run():
        import asyncio
        target_file = tmp_path / "expected_deliverable.ipynb"

        class MockGateway:
            def __init__(self):
                self.turn = 0

            async def chat(self, model=None, prompt=None, system=None, tools=None):
                self.turn += 1
                if self.turn == 1:
                    # First turn: falsely claim deliverable was created, without creating it
                    return {
                        "content": f"The process has completed and the notebook is saved as: **{target_file}**",
                        "tool_calls": [],
                    }
                elif self.turn == 2:
                    # Second turn: receives verification failure feedback, now actually creates it
                    return {
                        "content": f"Now creating file:\n```bash\npython -c \"open(r'{target_file}', 'w').write('{{}}')\"\n```",
                        "tool_calls": [],
                    }
                else:
                    return {
                        "content": f"All done. **{target_file}** is ready.",
                        "tool_calls": [],
                    }

        class MockRegistry:
            def get_schemas(self, interactive=True):
                return []

        class MockExecutor:
            async def execute_async(self, cmd, cwd=None, timeout=60):
                target_file.write_text("{}", encoding="utf-8")
                return {"status": "success", "stdout": "done", "stderr": "", "exit_code": 0}

            def execute(self, cmd, cwd=None, timeout=60):
                target_file.write_text("{}", encoding="utf-8")
                return {"status": "success", "stdout": "done", "stderr": "", "exit_code": 0}

        gateway = MockGateway()
        outcome = await run_direct_loop(
            f"Create {target_file}",
            gateway=gateway,
            tools_registry=MockRegistry(),
            executor=MockExecutor(),
            max_turns=5,
        )

        assert outcome is not None
        assert outcome["status"] == "completed"
        # Verification intercepted turn 1, so gateway progressed to turn 2+ to create the file
        assert gateway.turn >= 2
        assert target_file.exists()

    import asyncio
    asyncio.run(_run())

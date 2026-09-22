import re
import json
import pytest
import nbformat
import aja.config
from aja.orchestration.tools.native import NativeToolRegistry

def test_smart_notebook_markdown_wrapping(tmp_path):
    orig_root = aja.config.PROJECT_ROOT
    try:
        aja.config.PROJECT_ROOT = tmp_path
        registry = NativeToolRegistry()
        target_nb = str(tmp_path / "test_eda.ipynb")
        
        markdown_content = """# Titanic Exploration

This is an EDA notebook for analyzing passenger demographics.

```python
import pandas as pd
import numpy as np

df = pd.DataFrame({"age": [22, 38, 26, 35]})
print(df.describe())
```

## Visualizations

Here is a histogram of ages.

```python
import matplotlib.pyplot as plt
plt.hist(df['age'])
```

### Conclusion
Done analyzing.
"""
        result = registry.write_file(target_nb, markdown_content)
        assert "Successfully wrote" in result

        # Verify it can be loaded with nbformat
        nb = nbformat.read(target_nb, as_version=4)
        nbformat.validate(nb)
        
        # Check cell types
        cell_types = [c.cell_type for c in nb.cells]
        assert cell_types == ["markdown", "code", "markdown", "code", "markdown"]
        assert "Titanic Exploration" in "".join(nb.cells[0].source)
        assert "import pandas as pd" in "".join(nb.cells[1].source)
        assert "Visualizations" in "".join(nb.cells[2].source)
        assert "plt.hist" in "".join(nb.cells[3].source)
        assert "Conclusion" in "".join(nb.cells[4].source)
    finally:
        aja.config.PROJECT_ROOT = orig_root

import json
import pytest
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

        # Verify notebook structure
        try:
            import nbformat
            nb = nbformat.read(target_nb, as_version=4)
            nbformat.validate(nb)
            cell_types = [c.cell_type for c in nb.cells]
            sources = ["".join(c.source) for c in nb.cells]
        except ImportError:
            with open(target_nb, "r", encoding="utf-8") as f:
                nb_data = json.load(f)
            cell_types = [c["cell_type"] for c in nb_data["cells"]]
            sources = ["".join(c["source"]) for c in nb_data["cells"]]
        
        # Check cell types
        assert cell_types == ["markdown", "code", "markdown", "code", "markdown"]
        assert "Titanic Exploration" in sources[0]
        assert "import pandas as pd" in sources[1]
        assert "Visualizations" in sources[2]
        assert "plt.hist" in sources[3]
        assert "Conclusion" in sources[4]
    finally:
        aja.config.PROJECT_ROOT = orig_root

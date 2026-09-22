"""Synchronize optional Plotly imports without changing analytical dependencies."""
from functools import lru_cache
import importlib
import importlib.util


@lru_cache(maxsize=1)
def ensure_plot_runtime() -> None:
    """Wait for a normal pandas import before Plotly inspects sys.modules.

    Plotly 7 validators inspect pandas.Series even for native Python lists.
    A concurrent import publishes the module before Series exists. Using the
    Python import machinery here waits for its module lock; no monkeypatching,
    sys.modules deletion, package installation or pandas analytics is needed.
    pandas remains optional for standalone Plotly, required by Streamlit itself.
    Failed imports aren't cached and must reach the rendering error boundary.
    """
    if importlib.util.find_spec("pandas") is not None:
        module = importlib.import_module("pandas")
        if not hasattr(module, "Series") or not hasattr(module, "Index"):
            raise RuntimeError("Pandas est installé mais incomplet. Lancez python -m scripts.check_environment.")

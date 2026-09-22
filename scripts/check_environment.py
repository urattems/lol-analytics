"""Read-only startup/plot sanity: no .env, database or Riot access.

python -m scripts.check_environment
"""
import importlib
import importlib.util
import json
import logging
from pathlib import Path


def diagnose():
    result = {"PANDAS_INSTALLED": importlib.util.find_spec("pandas") is not None}
    result.update(PANDAS_IMPORT_OK=None, PANDAS_FILE=None, PANDAS_VERSION=None,
                  PUBLIC_RELEASE_READY=False)
    try:
        if not result["PANDAS_INSTALLED"]:
            raise RuntimeError("Pandas manque : cette dépendance est requise par Streamlit.")
        if result["PANDAS_INSTALLED"]:
            result["PANDAS_IMPORT_OK"] = False
            pd = importlib.import_module("pandas")
            result.update(PANDAS_IMPORT_OK=hasattr(pd, "Series") and hasattr(pd, "Index"),
                          PANDAS_FILE=str(Path(pd.__file__).resolve()), PANDAS_VERSION=pd.__version__)
            if not result["PANDAS_IMPORT_OK"]:
                raise RuntimeError("Installed pandas is incomplete")
        import streamlit
        import plotly
        import polars as pl
        from analytics.overview import rolling_winrate
        from ui.components import _rolling_figure
        games = pl.from_dicts([dict(match_id=f"SYNTHETIC_{i}", game_creation=1700000000000+i*2400000,
            champion="Lux", win=i % 2, kills=2, deaths=1, assists=3, duration=1800) for i in range(17)])
        rolling, window = rolling_winrate(games)
        figure = _rolling_figure(rolling, window)
        assert len(figure.data[0].x) == 13 and window == 5
        figure.to_json()
        result.update(STREAMLIT_VERSION=streamlit.__version__, PLOTLY_VERSION=plotly.__version__,
                      ROLLING_FIGURE_OK=True, PUBLIC_RELEASE_READY=True)
    except Exception:
        logging.getLogger(__name__).exception("Environment sanity failed; no repair was attempted")
    return result


def main():
    result = diagnose()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["PUBLIC_RELEASE_READY"] else 1)


if __name__ == "__main__":
    main()

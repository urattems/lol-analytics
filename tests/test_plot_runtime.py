"""Reproduce the actual partially initialized pandas race in fresh processes."""
import subprocess
import sys
import textwrap

import pytest


SETUP = '''
import polars as pl
from analytics.overview import rolling_winrate
from ui.components import _rolling_figure
games=pl.from_dicts([dict(match_id=f'SYNTHETIC_{i}',game_creation=1700000000000+i*2400000,
    champion='Lux',win=i%2,kills=2,deaths=1,assists=3,duration=1800) for i in range(17)])
rolling,window=rolling_winrate(games)
'''


def run_script(code):
    result = subprocess.run([sys.executable, '-c', textwrap.dedent(code)], capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_rolling_17_games_without_importing_pandas():
    run_script(SETUP + '''
import sys
from unittest.mock import patch
assert 'pandas' not in sys.modules
with patch('core.runtime.importlib.util.find_spec', return_value=None):
    figure=_rolling_figure(rolling,window)
assert len(figure.data[0].x)==13 and window==5
assert 'pandas' not in sys.modules
assert len(figure.data[0].customdata)==13
''')


def test_sanity_does_not_claim_app_ready_when_required_pandas_is_absent(monkeypatch):
    from scripts.check_environment import diagnose
    monkeypatch.setattr('scripts.check_environment.importlib.util.find_spec', lambda name: None)
    result = diagnose()
    assert result['PANDAS_INSTALLED'] is False
    assert result['PUBLIC_RELEASE_READY'] is False


def test_real_pandas_import_race_reproduced_and_synchronized():
    run_script(SETUP + '''
import importlib,importlib.abc,importlib.machinery,sys,threading
from unittest.mock import patch
from core.runtime import ensure_plot_runtime
entered,release=threading.Event(),threading.Event()
errors=[]
class Finder(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname!='pandas': return None
        spec=importlib.machinery.PathFinder.find_spec(fullname,path)
        original=spec.loader
        class Loader(importlib.abc.Loader):
            def create_module(self,spec): return original.create_module(spec)
            def exec_module(self,module):
                entered.set()
                if not release.wait(15): raise RuntimeError('QA coordination timeout')
                original.exec_module(module)
        spec.loader=Loader()
        return spec
finder=Finder();sys.meta_path.insert(0,finder)
def worker():
    try: importlib.import_module('pandas')
    except BaseException as exc: errors.append(exc)
thread=threading.Thread(target=worker);thread.start()
assert entered.wait(5)
try:
    with patch('core.runtime.ensure_plot_runtime', lambda:None):
        try: _rolling_figure(rolling,window)
        except AttributeError as exc:
            assert 'partially initialized' in str(exc) and 'Series' in str(exc)
        else: raise AssertionError('Unprotected race did not reproduce')
    timer=threading.Timer(.2,release.set);timer.start()
    figure=_rolling_figure(rolling,window)
    timer.join(5)
    assert len(figure.data[0].x)==13
finally:
    release.set();thread.join(15);sys.meta_path.remove(finder)
assert not errors and not thread.is_alive()
assert all(isinstance(x,(int,float)) for x in figure.data[0].x)
''')


def test_rendering_failure_has_fallback_logs_and_following_content(monkeypatch, caplog):
    from streamlit.testing.v1 import AppTest
    def broken(*args, **kwargs):
        raise AttributeError("Synthetic optional renderer failure")
    monkeypatch.setattr('ui.components._rolling_figure', broken)
    app = AppTest.from_string(SETUP + '''
import streamlit as st
from ui.components import render_recent_form
render_recent_form(games)
st.success('Following statistics remain usable')
''').run()
    assert not app.exception and len(app.warning) == 1
    assert app.success[0].value == 'Following statistics remain usable'
    assert '"phase": "chart"' in caplog.text
    assert '"error_code": "INTERNAL"' in caplog.text
    assert 'Synthetic optional renderer failure' not in caplog.text


@pytest.mark.parametrize('kind', ['sqlite', 'invariant'])
def test_rendering_boundary_does_not_swallow_data_invariants(kind):
    import sqlite3
    from ui.errors import chart_boundary
    error = sqlite3.DatabaseError if kind == 'sqlite' else AssertionError
    with pytest.raises(error):
        with chart_boundary('synthetic'):
            raise error('Synthetic data failure')

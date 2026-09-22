"""Native GUI probes in fresh processes, matching the real installer lifecycle.

Microsoft Store Tcl can fail when recreated after unrelated threaded UI tests.
Each production invocation owns exactly one Tk root; these probes do the same.
"""
from pathlib import Path
import sys
import time

from core.models import RiotAccount
from core.onboarding import VerifiedAccount, read_configuration, validate_input
from scripts import first_run


def main():
    action, path = sys.argv[1], Path(sys.argv[2])
    key = 'RGAPI-synthetic-not-a-real-credential'
    window = first_run.tk.Tk()
    wizard = first_run.SetupWizard(window, path)
    wizard.name.set('Synthetic Player#TEST')
    wizard.key.set(key)
    if action in ('close', 'demo'):
        getattr(wizard, action)()
        assert wizard.result == (2 if action == 'close' else 3)
        assert list(path.iterdir()) == []
        return
    verified = VerifiedAccount(validate_input('Synthetic Player#TEST', 'EUW1', key),
                               RiotAccount(puuid='synthetic-puuid', gameName='Synthetic Player', tagLine='TEST'), True)
    first_run.verify_account = lambda *_args, **_kwargs: verified
    wizard.want_import.set(False)
    wizard.want_shortcut.set(False)
    def wait_for(predicate):
        until = time.monotonic() + 10
        while not predicate() and time.monotonic() < until:
            window.update()
            time.sleep(0.02)
        assert predicate()
    try:
        assert wizard.key_entry.cget('show') == '•'
        wizard.check()
        wait_for(lambda: wizard.verified is not None and not wizard.busy)
        assert not (path / '.env').exists()
        wizard.save()
        wait_for(lambda: not wizard.busy)
        assert read_configuration(path).values['RIOT_GAME_NAME'] == 'Synthetic Player'
        assert wizard.key.get() == ''
    finally:
        window.destroy()


if __name__ == '__main__':
    main()

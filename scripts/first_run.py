"""French native first-run assistant; Tk stays on the main thread.

Exit codes used by the Windows launcher: 0 launch personal, 2 close, 3 demo.
Checks use Riot only after the user clicks Verify. Saving needs a second click.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
import webbrowser

from core.db import Database
from core.exceptions import RiotError
from core.onboarding import (
    ConfigSnapshot, SetupError, VerifiedAccount, needs_setup, read_configuration,
    save_configuration, settings_for_import, validate_input, verify_account,
)
from core.profiles import import_profile


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVERS = {
    'EUW1': 'Europe de l’Ouest · EUW', 'EUN1': 'Europe Nord / Est · EUNE',
    'NA1': 'Amérique du Nord · NA', 'BR1': 'Brésil · BR',
    'LA1': 'Amérique latine Nord · LAN', 'LA2': 'Amérique latine Sud · LAS',
    'KR': 'Corée · KR', 'JP1': 'Japon · JP', 'OC1': 'Océanie · OCE',
    'TR1': 'Turquie · TR', 'RU': 'Russie · RU', 'SG2': 'Singapour · SG',
    'PH2': 'Philippines · PH', 'TH2': 'Thaïlande · TH',
    'TW2': 'Taïwan · TW', 'VN2': 'Vietnam · VN',
}


def enable_dpi_awareness():
    if sys.platform == 'win32':
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError, AttributeError):
            pass


def create_shortcut(root: Path) -> bool:
    if sys.platform != 'win32':
        return False
    try:
        result = subprocess.run([
            'powershell.exe', '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', str(root / 'scripts' / 'create_shortcut.ps1'),
        ], capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def complete_setup(root: Path, snapshot: ConfigSnapshot, verified: VerifiedAccount, *,
                   import_games: bool, shortcut: bool, progress) -> list[str]:
    """Required save first; optional failures never pretend to roll it back."""
    progress('Enregistrement de la configuration locale…')
    backup = save_configuration(root, snapshot, verified)
    messages = ['Votre compte est maintenant le profil principal de ce dossier.']
    if backup:
        messages.append('L’ancien .env a été sauvegardé dans un fichier .env.backup-* local (confidentiel).')
    if import_games:
        try:
            settings = settings_for_import(root, verified)
            Database(settings.database_path).initialize()
            result = import_profile(
                settings, f'{verified.account.game_name}#{verified.account.tag_line}',
                verified.settings.platform, count=20, expected_puuid=verified.account.puuid,
                progress=lambda event: progress(f'Import des parties · {event.current}/{event.total or "…"}'),
            )
            messages.append(f'{result.inserted} nouvelle(s) partie(s) importée(s), {result.already_known} déjà présente(s).')
        except (RiotError, ValueError, sqlite3.Error, OSError):
            messages.append('Configuration enregistrée, mais import incomplet. Les parties déjà importées restent disponibles. Utilisez « Synchroniser » dans l’application pour reprendre ; vérifiez la clé ou la connexion si nécessaire.')
    else:
        messages.append('Cliquez sur « Synchroniser » dans l’application pour charger vos parties quand vous le souhaitez.')
    if shortcut:
        progress('Création du raccourci du bureau…')
        if create_shortcut(root):
            messages.append('Raccourci « LoL Analytics » disponible sur votre bureau.')
        else:
            messages.append('Le raccourci n’a pas pu être créé. Double-cliquez sur start_lol_analytics.bat dans ce dossier pour ouvrir l’application.')
    messages.append('Gardez le dossier à cet emplacement : le raccourci pointe vers celui-ci.')
    return messages


class SetupWizard:
    def __init__(self, window: tk.Tk, root: Path = PROJECT_ROOT):
        self.window, self.root = window, root
        self.result = 2
        self.busy = False
        self.verified: VerifiedAccount | None = None
        self.events: queue.Queue = queue.Queue()
        self.snapshot = read_configuration(root)
        window.title('LoL Analytics · Premier lancement')
        width = min(820, window.winfo_screenwidth() - 80)
        height = min(850, window.winfo_screenheight() - 100)
        window.geometry(f'{width}x{height}')
        window.minsize(min(680, width), min(560, height))
        window.configure(bg='#0c1423')
        window.protocol('WM_DELETE_WINDOW', self.close)
        self.style = ttk.Style(window)
        self.style.theme_use('clam')
        self.style.configure('.', font=('Segoe UI', 11))
        self.style.configure('TFrame', background='#0c1423')
        self.style.configure('TLabel', background='#0c1423', foreground='#e9eef7')
        self.style.configure('Muted.TLabel', foreground='#a6b5cc')
        self.style.configure('Title.TLabel', font=('Segoe UI', 24, 'bold'))
        self.style.configure('Accent.TLabel', foreground='#69dfcd', font=('Segoe UI', 10, 'bold'))
        self.style.configure('TButton', padding=(14, 10), background='#20344d', foreground='#f3f7ff')
        self.style.map('TButton', background=[('active', '#32506e')], foreground=[('disabled', '#8c9bb0')])
        self.style.configure('Primary.TButton', background='#69dfcd', foreground='#102338', font=('Segoe UI', 11, 'bold'))
        self.style.map('Primary.TButton', background=[('active', '#94eddf'), ('disabled', '#304958')])
        self.style.configure('TEntry', padding=9, fieldbackground='#f1f5fa', foreground='#102338')
        self.style.configure('TCombobox', padding=8, foreground='#102338')
        self.style.map('TCombobox', fieldbackground=[('readonly', '#f1f5fa')], foreground=[('readonly', '#102338')])
        self.style.configure('TCheckbutton', background='#0c1423', foreground='#e9eef7', padding=(0, 4))
        self.style.map('TCheckbutton', background=[('active', '#0c1423')])
        self.container = ttk.Frame(window, padding=(32, 24))
        self.container.pack(fill='both', expand=True)
        ttk.Label(self.container, text='LOL ANALYTICS / INSTALLATION GUIDÉE', style='Accent.TLabel').pack(anchor='w')
        self.step = ttk.Label(self.container, style='Muted.TLabel', wraplength=650)
        self.step.pack(anchor='w', pady=(8, 20))
        # A scrollable body keeps actions reachable on laptops and high-DPI
        # desktops. Fixed-size setup windows otherwise hide the final buttons.
        scrolling = ttk.Frame(self.container)
        scrolling.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(scrolling, bg='#0c1423', highlightthickness=0)
        scroll = ttk.Scrollbar(scrolling, orient='vertical', command=self.canvas.yview)
        scroll.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.body = ttk.Frame(self.canvas)
        self.body_item = self.canvas.create_window((0, 0), window=self.body, anchor='nw')
        self.body.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', self.resize_body)
        window.bind('<MouseWheel>', lambda event: self.canvas.yview_scroll(-int(event.delta / 120), 'units'))
        self.status = ttk.Label(self.container, style='Muted.TLabel', wraplength=650)
        self.status.pack(fill='x', pady=(12, 8))
        self.buttons = ttk.Frame(self.container)
        self.buttons.pack(fill='x')
        self.name = tk.StringVar(value=self.initial_riot_id())
        platform = self.snapshot.values.get('RIOT_PLATFORM_REGION') or 'EUW1'
        self.server = tk.StringVar(value=SERVERS.get(platform.upper(), SERVERS['EUW1']))
        self.key = tk.StringVar()
        self.show_key = tk.BooleanVar(value=False)
        self.want_import = tk.BooleanVar(value=True)
        self.want_shortcut = tk.BooleanVar(value=True)
        self.show_form()
        self.window.after(100, self.poll)

    def resize_body(self, event):
        self.canvas.itemconfigure(self.body_item, width=event.width)
        self.wrap_labels(event.width)

    def wrap_labels(self, width):
        for widget in self.body.winfo_children():
            if isinstance(widget, ttk.Label):
                widget.configure(wraplength=max(250, width - 15))

    def initial_riot_id(self) -> str:
        name = self.snapshot.values.get('RIOT_GAME_NAME')
        tag = self.snapshot.values.get('RIOT_TAG_LINE')
        return f'{name}#{tag}' if name and name != 'YourGameName' and tag else ''

    def clear_page(self, step: str, title: str, subtitle: str):
        for frame in (self.body, self.buttons):
            for child in frame.winfo_children():
                child.destroy()
        self.step.configure(text=step)
        self.status.configure(text='')
        self.canvas.yview_moveto(0)
        ttk.Label(self.body, text=title, style='Title.TLabel', wraplength=650).pack(anchor='w')
        ttk.Label(self.body, text=subtitle, style='Muted.TLabel', wraplength=650).pack(anchor='w', pady=(8, 18))

    def label(self, text):
        ttk.Label(self.body, text=text, wraplength=650).pack(anchor='w', pady=(9, 5))

    def show_form(self):
        self.verified = None
        self.show_key.set(False)
        self.clear_page('01  Votre compte     →     02  Vérification     →     03  C’est prêt',
                        'Vos parties. Votre espace.',
                        'Pas de fichier à éditer. Vérifiez votre compte, puis choisissez ce que vous souhaitez installer.')
        self.label('Votre Riot ID complet')
        entry = ttk.Entry(self.body, textvariable=self.name)
        entry.pack(fill='x')
        ttk.Label(self.body, text='Exemple : Demo Dragon#DEMO · visible dans le client Riot', style='Muted.TLabel').pack(anchor='w', pady=(4, 0))
        self.label('Votre serveur League of Legends')
        ttk.Combobox(self.body, textvariable=self.server, values=list(SERVERS.values()), state='readonly').pack(fill='x')
        ttk.Label(self.body, text='Le tag après # n’indique pas forcément votre serveur.', style='Muted.TLabel').pack(anchor='w', pady=(4, 0))
        self.label('Votre clé API Riot (jamais votre mot de passe)')
        self.key_entry = ttk.Entry(self.body, textvariable=self.key, show='•')
        self.key_entry.pack(fill='x')
        ttk.Checkbutton(self.body, text='Afficher la clé saisie', variable=self.show_key,
                        command=lambda: self.key_entry.configure(show='' if self.show_key.get() else '•')).pack(anchor='w')
        hint = 'Clé déjà enregistrée : laissez ce champ vide pour la conserver.' if self.snapshot.values.get('RIOT_API_KEY') else 'Connectez-vous au portail Riot, puis copiez votre clé RGAPI-… ici.'
        ttk.Label(self.body, text=hint, style='Muted.TLabel', wraplength=650).pack(anchor='w', pady=(0, 8))
        ttk.Button(self.body, text='Obtenir / renouveler ma clé sur le portail Riot ↗',
                   command=lambda: webbrowser.open('https://developer.riotgames.com/')).pack(anchor='w')
        ttk.Label(self.body, text='Une clé de développement expire après 24 h. Revenez ici pour la remplacer. La clé reste dans votre .env local, non chiffré, et part uniquement vers Riot pour les requêtes.',
                  style='Muted.TLabel', wraplength=650).pack(anchor='w', pady=(12, 0))
        ttk.Button(self.buttons, text='Essayer la démo sans clé', command=self.demo).pack(side='left')
        ttk.Button(self.buttons, text='Vérifier mon compte →', style='Primary.TButton', command=self.check).pack(side='right')
        entry.focus_set()
        self.wrap_labels(self.canvas.winfo_width() if self.canvas.winfo_width() > 1 else 650)

    def check(self):
        try:
            platform = next(code for code, label in SERVERS.items() if label == self.server.get())
            inputs = validate_input(self.name.get(), platform, self.key.get() or self.snapshot.values.get('RIOT_API_KEY') or '')
        except (ValueError, StopIteration) as error:
            self.status.configure(text=str(error) if isinstance(error, ValueError) else 'Choisissez un serveur dans la liste.')
            return
        self.clear_page('02 / VÉRIFICATION EN LIGNE', 'On vérifie ensemble.',
                        'Trois contrôles auprès de Riot. Aucun fichier ni historique n’est modifié à cette étape.')
        self.label(f'Compte demandé : {inputs.riot_id}')
        self.label(f'Serveur : {SERVERS[inputs.platform]}')
        self.label('Clé API → compte Riot → serveur LoL → accès à l’historique')
        self.label('Cela peut prendre quelques secondes. Une erreur ? Vous pourrez corriger et réessayer.')
        self.run_worker(lambda: verify_account(inputs, progress=self.progress), 'verified')

    def progress(self, text):
        self.events.put(('progress', text))

    def run_worker(self, operation, success_event):
        self.busy = True
        def worker():
            try:
                self.events.put((success_event, operation()))
            except SetupError as error:
                self.events.put(('error', str(error)))
            except Exception:
                # Neither exception payloads nor tracebacks can reveal the key.
                self.events.put(('error', 'L’opération n’a pas pu se terminer. Relancez l’assistant pour relire la configuration ; aucune base n’a été effacée.'))
        threading.Thread(target=worker, daemon=False).start()

    def poll(self):
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == 'progress':
                    self.status.configure(text=value)
                else:
                    self.busy = False
                    if event == 'verified':
                        self.verified = value
                        self.show_review()
                    elif event == 'saved':
                        self.show_done(value)
                    elif event == 'error':
                        self.status.configure(text=value)
                        for child in self.buttons.winfo_children():
                            child.destroy()
                        ttk.Button(self.buttons, text='Fermer', command=self.close).pack(side='left')
                        ttk.Button(self.buttons, text='Revenir et corriger', command=self.reload_form).pack(side='right')
        except queue.Empty:
            pass
        self.window.after(100, self.poll)

    def reload_form(self):
        try:
            self.snapshot = read_configuration(self.root)
        except SetupError as error:
            self.status.configure(text=str(error))
            return
        self.show_form()

    def show_review(self):
        assert self.verified is not None
        self.clear_page('02 / COMPTE VALIDÉ', 'Tout est bon côté Riot.',
                        'La clé est acceptée, le compte existe sur ce serveur et l’historique est accessible. Confirmez maintenant l’enregistrement local.')
        self.label(f'Profil principal : {self.verified.account.game_name}#{self.verified.account.tag_line}')
        self.label(f'Serveur : {SERVERS[self.verified.settings.platform]}')
        if not self.verified.has_history:
            self.label('Aucune partie disponible actuellement : le compte est valide, mais la bibliothèque peut rester vide.')
        if self.snapshot.content is not None:
            self.label('Configuration existante : fermez l’application avant de confirmer. L’ancien .env sera sauvegardé ; les autres réglages, profils, parties et timelines sont conservés.')
        ttk.Checkbutton(self.body, text='Importer jusqu’à 20 parties récentes maintenant', variable=self.want_import).pack(anchor='w', pady=(18, 6))
        ttk.Checkbutton(self.body, text='Créer un raccourci sur mon bureau', variable=self.want_shortcut).pack(anchor='w')
        self.label('L’import est facultatif et peut prendre quelques minutes. Vous pourrez charger davantage de parties ensuite, depuis l’application.')
        self.label('Un .env local sera enregistré avec votre clé. Ne partagez ni ce fichier ni ses sauvegardes. Aucun mot de passe Riot n’est demandé.')
        ttk.Button(self.buttons, text='← Modifier', command=self.show_form).pack(side='left')
        ttk.Button(self.buttons, text='Enregistrer et préparer →', style='Primary.TButton', command=self.save).pack(side='right')

    def save(self):
        assert self.verified is not None
        verified = self.verified
        old = self.initial_riot_id()
        new = f'{verified.account.game_name}#{verified.account.tag_line}'
        if old and old != new:
            if not messagebox.askyesno('Changer de profil principal ?',
                f'{new} remplacera {old} comme profil principal. Les parties et autres profils restent en base. Continuer ?', parent=self.window):
                return
        import_games, shortcut = self.want_import.get(), self.want_shortcut.get()
        self.clear_page('03 / PRÉPARATION LOCALE', 'On prépare votre espace.',
                        'Gardez cette fenêtre ouverte pendant l’enregistrement et l’éventuel import. Les parties sont sauvegardées au fur et à mesure.')
        self.run_worker(lambda: complete_setup(self.root, self.snapshot, verified,
                        import_games=import_games, shortcut=shortcut, progress=self.progress), 'saved')

    def show_done(self, messages):
        self.key.set('')
        self.verified = None
        self.snapshot = ConfigSnapshot(None, {})
        self.clear_page('03 / TERMINÉ', 'Bienvenue dans votre espace.',
                        'Tout est enregistré. Votre navigateur va prendre le relais.')
        for text in messages:
            self.label(text)
        self.label('Pour renouveler votre clé plus tard : relancez INSTALLER.bat. Pour les prochains lancements : utilisez le raccourci ou start_lol_analytics.bat.')
        ttk.Button(self.buttons, text='Fermer sans ouvrir', command=self.close).pack(side='left')
        ttk.Button(self.buttons, text='Ouvrir LoL Analytics →', style='Primary.TButton', command=self.launch).pack(side='right')

    def launch(self):
        self.result = 0
        self.window.destroy()

    def demo(self):
        self.result = 3
        self.window.destroy()

    def close(self):
        if self.busy:
            messagebox.showinfo('Opération en cours', 'Patientez jusqu’à la fin de cette étape. Les appels réseau ont un délai limité ; les parties déjà importées sont conservées.', parent=self.window)
            return
        self.window.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description='Assistant local de premier lancement')
    parser.add_argument('--needs-setup', action='store_true')
    args = parser.parse_args()
    if args.needs_setup:
        try:
            return 1 if needs_setup(PROJECT_ROOT) else 0
        except SetupError:
            return 1  # Let the GUI explain the exact issue, without logging secrets.
    enable_dpi_awareness()
    window = tk.Tk()
    try:
        wizard = SetupWizard(window)
    except SetupError as error:
        window.withdraw()
        messagebox.showerror('Configuration à vérifier', str(error), parent=window)
        window.destroy()
        return 2
    window.mainloop()
    return wizard.result


if __name__ == '__main__':
    raise SystemExit(main())

"""CorriLee desktop UI. Network and workbook work run outside the UI thread."""
from __future__ import annotations
import concurrent.futures
import datetime as dt
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import webbrowser
import grant_finder as grants
from source_settings import SourceStore, validate_source

APP_NAME = 'CorriLee Grant Ranker'

# The exact solid banner colour supplied by the client, plus restrained neutral
# and status colours. Keeping the palette here makes every window consistent.
COLOURS = {
    'brand': '#B1005D',
    'purple': '#B1005D',
    'purple_dark': '#6F003A',
    'pink': '#B1005D',
    'pink_dark': '#800044',
    'lavender': '#F6E9F0',
    'pink_wash': '#FAE8F1',
    'background': '#F7F5F8',
    'surface': '#FFFFFF',
    'border': '#DED7E2',
    'text': '#27232A',
    'muted': '#68616C',
    'success': '#2E6B55',
    'success_wash': '#E7F3ED',
    'warning': '#8A5B12',
    'warning_wash': '#FFF3D8',
}


def configure_styles(root):
    """Apply one predictable, accessible visual system on every platform."""
    style = ttk.Style(root)
    if 'clam' in style.theme_names():
        style.theme_use('clam')
    root.configure(background=COLOURS['background'])
    style.configure('.', font=('Arial', 11), foreground=COLOURS['text'])
    style.configure('TFrame', background=COLOURS['surface'])
    style.configure('TLabel', background=COLOURS['surface'])
    style.configure('App.TFrame', background=COLOURS['background'])
    style.configure('Card.TFrame', background=COLOURS['surface'], relief='solid', borderwidth=1)
    style.configure('Card.TLabel', background=COLOURS['surface'])
    style.configure('Muted.TLabel', background=COLOURS['surface'], foreground=COLOURS['muted'])
    style.configure('Title.TLabel', background=COLOURS['surface'], font=('Arial', 21, 'bold'),
                    foreground=COLOURS['purple_dark'])
    style.configure('Section.TLabel', background=COLOURS['surface'], font=('Arial', 14, 'bold'),
                    foreground=COLOURS['purple_dark'])
    style.configure('Primary.TButton', font=('Arial', 11, 'bold'), padding=(18, 11),
                    foreground='white', background=COLOURS['purple'], borderwidth=0)
    style.map('Primary.TButton',
              background=[('disabled', '#C7BBCB'), ('pressed', COLOURS['purple_dark']),
                          ('active', '#97004F')],
              foreground=[('disabled', '#F3EEF5'), ('!disabled', 'white')])
    style.configure('Secondary.TButton', padding=(14, 9), foreground=COLOURS['purple_dark'],
                    background=COLOURS['lavender'], bordercolor=COLOURS['border'])
    style.map('Secondary.TButton', background=[('pressed', '#DFD0E7'), ('active', '#E9DDF0')])
    style.configure('Quiet.TButton', padding=(12, 8), foreground=COLOURS['text'],
                    background=COLOURS['surface'], bordercolor=COLOURS['border'])
    style.map('Quiet.TButton', background=[('pressed', '#EEEAF0'), ('active', '#F4F0F5')])
    style.configure('Danger.TButton', padding=(12, 8), foreground=COLOURS['pink_dark'],
                    background=COLOURS['pink_wash'], bordercolor='#EAC4D7')
    style.configure('Treeview', background=COLOURS['surface'], fieldbackground=COLOURS['surface'],
                    foreground=COLOURS['text'], rowheight=34, bordercolor=COLOURS['border'],
                    borderwidth=1)
    style.map('Treeview', background=[('selected', COLOURS['lavender'])],
              foreground=[('selected', COLOURS['purple_dark'])])
    style.configure('Treeview.Heading', background='#EDE8EF', foreground=COLOURS['purple_dark'],
                    font=('Arial', 10, 'bold'), padding=(8, 9), relief='flat')
    style.map('Treeview.Heading', background=[('active', '#E4D9E9')])
    style.configure('Horizontal.TProgressbar', background=COLOURS['pink'],
                    troughcolor='#EAE4EC', bordercolor='#EAE4EC', lightcolor=COLOURS['pink'],
                    darkcolor=COLOURS['pink'])
    style.configure('TCombobox', padding=7, fieldbackground=COLOURS['surface'])
    style.configure('TLabelframe', background=COLOURS['surface'], bordercolor=COLOURS['border'])
    style.configure('TLabelframe.Label', background=COLOURS['surface'],
                    foreground=COLOURS['purple_dark'], font=('Arial', 10, 'bold'))
    return style


def add_brand_header(parent, title, subtitle=None, height=94):
    """Draw the solid banner colour supplied by the client, without imagery."""
    banner = tk.Frame(parent, height=height, bg=COLOURS['brand'])
    banner.pack(fill='x')
    banner.pack_propagate(False)
    copy = tk.Frame(banner, bg=COLOURS['brand'])
    copy.pack(side='left', padx=28)
    tk.Label(copy, text=title, anchor='w', bg=COLOURS['brand'], fg='white',
             font=('Arial', 22, 'bold')).pack(anchor='w', pady=((15 if subtitle else 0), 0))
    if subtitle:
        tk.Label(copy, text=subtitle, anchor='w', bg=COLOURS['brand'], fg='#FFF3F8',
                 font=('Arial', 10)).pack(anchor='w', pady=(3, 0))
    return banner


def set_window_background(window):
    window.configure(background=COLOURS['background'])


class SourceEditor:
    """Plain-language editor that translates UI choices into crawler fields."""
    def __init__(self, parent, source=None):
        self.source = dict(source or {})
        self.result = None
        self.check_events = queue.Queue()
        self.window = tk.Toplevel(parent)
        self.window.title('Edit website' if source else 'Add website')
        self.window.geometry('740x760'); self.window.minsize(620, 650)
        set_window_background(self.window)
        self.window.transient(parent)
        add_brand_header(self.window, 'Edit website' if source else 'Add website',
                         'Set where the grant search should begin.', height=78)
        outer = ttk.Frame(self.window, style='App.TFrame', padding=20); outer.pack(fill='both', expand=True)
        body = ttk.Frame(outer, style='Card.TFrame', padding=20); body.pack(fill='both', expand=True)
        ttk.Label(body, text='Website details', style='Section.TLabel').pack(anchor='w', pady=(0, 16))
        ttk.Label(body, text='Website name', style='Card.TLabel').pack(anchor='w')
        self.name = tk.StringVar(value=self.source.get('name', ''))
        ttk.Entry(body, textvariable=self.name).pack(fill='x', pady=(4, 14))
        ttk.Label(body, text='Starting pages — one web address per line', style='Card.TLabel').pack(anchor='w')
        self.urls = tk.Text(body, height=4, wrap='none', relief='solid', bd=1,
                            highlightthickness=0, font=('Arial', 10), padx=8, pady=8)
        self.urls.pack(fill='x', pady=(4, 6)); self.urls.insert('1.0', '\n'.join(self.source.get('urls', [])))
        ttk.Label(body, text='The app stays on these websites and follows grant-related links.',
                  style='Muted.TLabel').pack(anchor='w')

        directories = self.source.get('directory_urls', [])
        urls = self.source.get('urls', [])
        if source and directories and set(directories) != set(urls):
            initial_type = 'mixed'
        elif source and not directories:
            initial_type = 'individual'
        else:
            initial_type = 'listing'
        self.page_type = tk.StringVar(value=initial_type)
        kind = ttk.LabelFrame(body, text='What do these pages contain?', padding=10)
        kind.pack(fill='x', pady=14)
        ttk.Radiobutton(kind, text='Lists of grants (recommended)', variable=self.page_type, value='listing').pack(anchor='w')
        ttk.Radiobutton(kind, text='Individual grant pages', variable=self.page_type, value='individual').pack(anchor='w')
        if initial_type == 'mixed':
            ttk.Radiobutton(kind, text='A mixture — keep the current classifications', variable=self.page_type, value='mixed').pack(anchor='w')

        advanced = ttk.LabelFrame(body, text='Optional details', padding=10); advanced.pack(fill='both', expand=True)
        ttk.Label(advanced, text='Region or service area').grid(row=0, column=0, sticky='w')
        self.region = tk.StringVar(value=self.source.get('region', ''))
        ttk.Entry(advanced, textvariable=self.region).grid(row=1, column=0, sticky='ew', pady=(3, 9))
        ttk.Label(advanced, text='Note for the coverage report').grid(row=2, column=0, sticky='w')
        self.note = tk.Text(advanced, height=2, wrap='word', relief='solid', bd=1,
                            highlightthickness=0, font=('Arial', 10), padx=8, pady=6)
        self.note.grid(row=3, column=0, sticky='ew', pady=(3, 9))
        self.note.insert('1.0', self.source.get('note', ''))
        ttk.Label(advanced, text='Website sections to skip — paths such as /news, one per line').grid(row=4, column=0, sticky='w')
        self.exclusions = tk.Text(advanced, height=2, wrap='none', relief='solid', bd=1,
                                  highlightthickness=0, font=('Arial', 10), padx=8, pady=6)
        self.exclusions.grid(row=5, column=0, sticky='ew', pady=(3, 0))
        self.exclusions.insert('1.0', '\n'.join(self.source.get('exclude_paths', [])))
        advanced.columnconfigure(0, weight=1)

        self.feedback = tk.StringVar(value='')
        ttk.Label(body, textvariable=self.feedback, wraplength=660,
                  style='Card.TLabel').pack(fill='x', pady=(10, 4))
        actions = ttk.Frame(body); actions.pack(fill='x')
        self.check_button = ttk.Button(actions, text='Check website', command=self.check,
                                       style='Secondary.TButton'); self.check_button.pack(side='left')
        ttk.Button(actions, text='Cancel', command=self.window.destroy,
                   style='Quiet.TButton').pack(side='right')
        ttk.Button(actions, text='Save website', command=self.save,
                   style='Primary.TButton').pack(side='right', padx=8)
        self.window.protocol('WM_DELETE_WINDOW', self.window.destroy)
        self.window.wait_visibility(); self.window.grab_set(); self.window.focus_set(); self.window.wait_window()

    @staticmethod
    def _lines(widget):
        return [line.strip() for line in widget.get('1.0', 'end').splitlines() if line.strip()]

    def value(self):
        candidate = {key: value for key, value in self.source.items() if not key.startswith('_')}
        candidate['name'] = self.name.get().strip()
        candidate['urls'] = self._lines(self.urls)
        candidate['domains'] = list(dict.fromkeys(grants.host(grants.normalize_url(url)) for url in candidate['urls']))
        if self.page_type.get() == 'listing':
            candidate['directory_urls'] = list(candidate['urls'])
        elif self.page_type.get() == 'individual':
            candidate['directory_urls'] = []
        else:
            candidate['directory_urls'] = list(self.source.get('directory_urls', []))
        region = self.region.get().strip(); note = self.note.get('1.0', 'end').strip()
        if region: candidate['region'] = region
        else: candidate.pop('region', None)
        if note: candidate['note'] = note
        else: candidate.pop('note', None)
        candidate['exclude_paths'] = self._lines(self.exclusions)
        return validate_source(candidate)

    def save(self):
        try:
            self.result = self.value()
        except ValueError as exc:
            self.feedback.set(str(exc)); return
        self.window.destroy()

    def check(self):
        try:
            source = self.value()
        except ValueError as exc:
            self.feedback.set(str(exc)); return
        self.feedback.set('Checking the first page…'); self.check_button.configure(state='disabled')
        def work():
            try:
                title, _, _, final, _ = grants.Client(12).fetch(source['urls'][0])
                message = f'Read successfully: {title or final}'
            except grants.FetchProblem as exc:
                message = f'Could not read it automatically: {exc} You can still save it for manual coverage.'
            except Exception:
                message = 'Could not read it automatically. You can still save it for manual coverage.'
            self.check_events.put(message)
        threading.Thread(target=work, daemon=True).start()
        self.window.after(100, self._poll_check)

    def _poll_check(self):
        try:
            message = self.check_events.get_nowait()
        except queue.Empty:
            if self.window.winfo_exists(): self.window.after(100, self._poll_check)
            return
        if self.window.winfo_exists():
            self.feedback.set(message); self.check_button.configure(state='normal')


class SourceManager:
    def __init__(self, parent, store, changed):
        self.store = store; self.changed = changed
        self.window = tk.Toplevel(parent); self.window.title('Manage websites')
        self.window.geometry('1080x700'); self.window.minsize(820, 560)
        set_window_background(self.window)
        self.window.transient(parent)
        add_brand_header(self.window, 'Manage websites',
                         'Step 1 of 4 · Choose the public sources included in the next search.')
        outer = ttk.Frame(self.window, style='App.TFrame', padding=20); outer.pack(fill='both', expand=True)
        body = ttk.Frame(outer, style='Card.TFrame', padding=18); body.pack(fill='both', expand=True)
        heading = ttk.Frame(body, style='Card.TFrame'); heading.pack(fill='x', pady=(0, 8))
        ttk.Label(heading, text='Websites to check', style='Section.TLabel').pack(side='left')
        self.summary = tk.StringVar()
        ttk.Label(heading, textvariable=self.summary, style='Muted.TLabel').pack(side='right')
        ttk.Label(body, text='Double-click a row to include or skip it. Built-in sources can always be restored.',
                  style='Muted.TLabel', wraplength=900).pack(anchor='w', pady=(0, 12))
        frame = ttk.Frame(body, style='Card.TFrame'); frame.pack(fill='both', expand=True)
        self.table = ttk.Treeview(frame, columns=('on', 'name', 'origin', 'address'), show='headings', selectmode='browse')
        for key, title, width in [('on','Status',100),('name','Website',260),('origin','Type',110),('address','Starting address',450)]:
            self.table.heading(key, text=title); self.table.column(key, width=width, minwidth=55)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set); scroll.pack(side='right', fill='y'); self.table.pack(fill='both', expand=True)
        self.table.bind('<Double-1>', lambda _event: self.toggle())
        self.table.bind('<Return>', lambda _event: self.edit())
        actions = ttk.Frame(body, style='Card.TFrame', padding=(0, 14, 0, 0)); actions.pack(fill='x')
        ttk.Button(actions, text='Add website', command=self.add,
                   style='Secondary.TButton').pack(side='left')
        self.edit_button = ttk.Button(actions, text='Edit', command=self.edit, style='Quiet.TButton')
        self.edit_button.pack(side='left', padx=(8, 0))
        self.toggle_button = ttk.Button(actions, text='Include / Skip', command=self.toggle,
                                         style='Quiet.TButton')
        self.toggle_button.pack(side='left', padx=8)
        self.restore_button = ttk.Button(actions, text='Restore', command=self.restore,
                                          style='Quiet.TButton')
        self.restore_button.pack(side='left')
        self.remove_button = ttk.Button(actions, text='Remove', command=self.remove,
                                         style='Danger.TButton')
        self.remove_button.pack(side='left', padx=8)
        ttk.Button(actions, text='Restore all defaults', command=self.restore_all,
                   style='Quiet.TButton').pack(side='left')
        ttk.Button(actions, text='Done', command=self.window.destroy,
                   style='Primary.TButton').pack(side='right')
        self.table.bind('<<TreeviewSelect>>', self.selection_changed)
        self.refresh(); self.window.wait_visibility(); self.window.grab_set(); self.window.focus_set(); self.window.wait_window()

    def refresh(self, select=None):
        self.table.delete(*self.table.get_children())
        config = self.store.effective_config()
        for source in config['sources']:
            identifier = source['id']
            is_enabled = source.get('enabled', True)
            values = ('Included' if is_enabled else 'Skipped', source['name'],
                      'Added by you' if source.get('_origin') == 'user' else 'Built in', source['urls'][0])
            self.table.insert('', 'end', iid=identifier, values=values,
                              tags=('included' if is_enabled else 'skipped',))
        self.table.tag_configure('included', foreground=COLOURS['text'])
        self.table.tag_configure('skipped', foreground='#8B858D', background='#F4F1F4')
        enabled = sum(source.get('enabled', True) for source in config['sources'])
        self.summary.set(f'{enabled} of {len(config["sources"])} websites selected')
        if select and self.table.exists(select): self.table.selection_set(select); self.table.focus(select)
        self.selection_changed()
        self.changed()

    def selection_changed(self, _event=None):
        state = 'normal' if self.table.selection() else 'disabled'
        for button in (self.edit_button, self.toggle_button, self.restore_button, self.remove_button):
            button.configure(state=state)

    def selected(self):
        selection = self.table.selection()
        return selection[0] if selection else None

    def source(self, identifier):
        return next(source for source in self.store.effective_config()['sources'] if source['id'] == identifier)

    def add(self):
        editor = SourceEditor(self.window)
        if editor.result:
            try:
                identifier = self.store.put_source(editor.result); self.refresh(identifier)
            except (ValueError, OSError) as exc: messagebox.showerror('Could not save website', str(exc), parent=self.window)

    def edit(self):
        identifier = self.selected()
        if not identifier: return
        editor = SourceEditor(self.window, self.source(identifier))
        if editor.result:
            try:
                self.store.put_source(editor.result, identifier); self.refresh(identifier)
            except (ValueError, OSError) as exc: messagebox.showerror('Could not save website', str(exc), parent=self.window)

    def toggle(self):
        identifier = self.selected()
        if not identifier: return
        source = self.source(identifier)
        try:
            self.store.set_enabled(identifier, not source.get('enabled', True)); self.refresh(identifier)
        except (ValueError, OSError) as exc: messagebox.showerror('Could not save setting', str(exc), parent=self.window)

    def remove(self):
        identifier = self.selected()
        if not identifier: return
        source = self.source(identifier)
        if self.store.is_builtin(identifier):
            question = f'Skip {source["name"]}? You can restore it later.'
        else:
            question = f'Remove {source["name"]}?'
        if messagebox.askyesno('Remove website', question, parent=self.window):
            self.store.remove(identifier); self.refresh()

    def restore(self):
        identifier = self.selected()
        if not identifier: return
        self.store.restore(identifier); self.refresh(identifier if self.table.exists(identifier) else None)

    def restore_all(self):
        if messagebox.askyesno('Restore all defaults?', 'Discard all website changes and remove websites added on this computer?', parent=self.window):
            self.store.restore(); self.refresh()


def score_explanation(record):
    """Plain-language score detail used by the desktop dialog and tests."""
    parts = [
        f"Activities: {record.get('Activities points', 0)} / 35",
        f"Mission: {record.get('Mission points', 0)} / 30",
        f"Regional focus: {record.get('Regional points', 0)} / 25",
        f"Applicant type: {record.get('Applicants points', 0)} / 10",
    ]
    return (f"TOTAL SCORE: {record.get('Screening score', 0)} / 100\n\n"
            + '\n'.join(parts)
            + "\n\nWHY THESE POINTS\n" + record.get('Screening evidence', 'No evidence recorded.')
            + "\n\nREVIEW FLAGS\n" + record.get('Screening flags', 'No flags recorded.')
            + "\n\nEVIDENCE LIMITS\n" + record.get('Evidence checks', 'No evidence limits recorded.'))


def search(config, events, stop):
    today = dt.date.today()
    sources = [s for s in config['sources'] if s.get('enabled', True)]
    records, coverage, logs = [], [], []
    def run(source):
        if stop.is_set():
            return [], [source['name'], 'Not searched - stopped by user', 0, 0, 0, 0,
                        'Search was stopped before this source began.', source['urls'][0], today], []
        events.put(('source', source['name']))
        return grants.crawl_source(source, config['profile'], 12, 2, 12, today, stop_event=stop)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            pending = {pool.submit(run, s): s for s in sources}
            for future in concurrent.futures.as_completed(pending):
                source = pending[future]
                try:
                    found, status, pages = future.result()
                except Exception as exc:
                    found, pages = [], []
                    status = [source['name'], 'Manual check needed', 0, 0, 0, 0,
                              f'Source could not be checked ({type(exc).__name__}).', source['urls'][0], today]
                records.extend(found); coverage.append(status); logs.extend(pages)
                events.put(('progress', len(coverage), len(sources), source['name'], status[3]))
        unique = {}
        for record in records:
            unique.setdefault(grants.normalize_url(record['Source URL']).rstrip('/'), record)
        order = {s['name']: i for i, s in enumerate(sources)}
        coverage.sort(key=lambda c: order[c[0]])
        events.put(('done', list(unique.values()), coverage, logs, today, stop.is_set()))
    except Exception as exc:
        events.put(('error', f'Search could not finish ({type(exc).__name__}). Please try again.'))


class GrantApp:
    def __init__(self, root):
        self.root = root
        root.title(APP_NAME); root.geometry('1180x820'); root.minsize(900, 650)
        configure_styles(root)
        self.events = queue.Queue(); self.stop = threading.Event()
        self.running = False; self.saving = False; self.result = None; self.saved = None
        self.source_store = SourceStore(grants.BASE / 'config.json')
        self.config = self.source_store.effective_config()
        add_brand_header(root, APP_NAME,
                         'Find, rank and review funding opportunities for regional Australia.')
        frame = ttk.Frame(root, style='App.TFrame', padding=(20, 16, 20, 18)); frame.pack(fill='both', expand=True)

        steps = ttk.Frame(frame, style='Card.TFrame', padding=(16, 11))
        steps.pack(fill='x', pady=(0, 14))
        self.step_widgets = []
        for number, label in enumerate(('Choose websites', 'Search', 'Review', 'Export'), 1):
            item = tk.Frame(steps, bg=COLOURS['surface'], bd=0)
            item.pack(side='left', fill='x', expand=True)
            badge = tk.Label(item, text=str(number), width=2, height=1, font=('Arial', 10, 'bold'),
                             bg='#E7E2E8', fg=COLOURS['muted'])
            badge.pack(side='left', padx=(4, 8))
            text = tk.Label(item, text=label, bg=COLOURS['surface'], fg=COLOURS['muted'],
                            font=('Arial', 10, 'bold'))
            text.pack(side='left')
            self.step_widgets.append((badge, text))

        search_card = ttk.Frame(frame, style='Card.TFrame', padding=16)
        search_card.pack(fill='x', pady=(0, 14))
        search_top = ttk.Frame(search_card, style='Card.TFrame'); search_top.pack(fill='x')
        search_copy = ttk.Frame(search_top, style='Card.TFrame'); search_copy.pack(side='left', fill='x', expand=True)
        ttk.Label(search_copy, text='Search public grant websites', style='Section.TLabel').pack(anchor='w')
        self.source_count = tk.StringVar()
        ttk.Label(search_copy, textvariable=self.source_count, style='Muted.TLabel').pack(anchor='w', pady=(3, 0))
        actions = ttk.Frame(search_top, style='Card.TFrame'); actions.pack(side='right')
        self.sources_button = ttk.Button(actions, text='Manage websites', command=self.manage_sources,
                                          style='Secondary.TButton')
        self.sources_button.pack(side='left', padx=(0, 8))
        self.stop_button = ttk.Button(actions, text='Stop search', command=self.cancel, state='disabled',
                                       style='Danger.TButton')
        self.stop_button.pack(side='left', padx=(0, 8))
        self.search_button = ttk.Button(actions, text='Search grants', command=self.start,
                                         style='Primary.TButton')
        self.search_button.pack(side='left')

        status_panel = tk.Frame(search_card, bg=COLOURS['lavender'], padx=12, pady=10)
        status_panel.pack(fill='x', pady=(14, 0))
        self.match_level = tk.StringVar(value='Balanced')
        initial_status = self.source_store.warning or 'Ready. Internet access is required. Searches may take several minutes.'
        self.status = tk.StringVar(value=initial_status)
        tk.Label(status_panel, textvariable=self.status, bg=COLOURS['lavender'],
                 fg=COLOURS['purple_dark'], anchor='w', justify='left',
                 font=('Arial', 10)).pack(fill='x')
        self.progress = ttk.Progressbar(search_card, maximum=1)
        self.progress.pack(fill='x', pady=(10, 0))

        results_card = ttk.Frame(frame, style='Card.TFrame', padding=16)
        results_card.pack(fill='both', expand=True)
        results_header = ttk.Frame(results_card, style='Card.TFrame'); results_header.pack(fill='x')
        ttk.Label(results_header, text='Review results', style='Section.TLabel').pack(side='left')
        export_actions = ttk.Frame(results_header, style='Card.TFrame'); export_actions.pack(side='right')
        self.open_button = ttk.Button(export_actions, text='Open saved file', command=self.open_saved,
                                      state='disabled', style='Quiet.TButton')
        self.open_button.pack(side='right')
        self.save_button = ttk.Button(export_actions, text='Save Excel or CSV', command=self.save,
                                      state='disabled', style='Secondary.TButton')
        self.save_button.pack(side='right', padx=(0, 8))

        filters = ttk.Frame(results_card, style='Card.TFrame', padding=(0, 12, 0, 7)); filters.pack(fill='x')
        ttk.Label(filters, text='Match level', style='Card.TLabel').pack(side='left', padx=(0, 7))
        self.level_box = ttk.Combobox(filters, textvariable=self.match_level,
                                      values=('Strict', 'Balanced', 'Broad'), state='readonly', width=10)
        self.level_box.pack(side='left'); self.level_box.bind('<<ComboboxSelected>>', self.change_level)
        self.details_button = ttk.Button(filters, text='Why this score?', command=self.show_score,
                                          state='disabled', style='Quiet.TButton')
        self.details_button.pack(side='left', padx=10)
        self.summary = tk.StringVar(value='No search yet. Closed rounds will be excluded from your shortlist.')
        ttk.Label(results_card, textvariable=self.summary, wraplength=1000,
                  style='Muted.TLabel').pack(fill='x', pady=(0, 10))

        self.selection_panel = tk.Frame(results_card, bg='#FAF7FB', padx=10, pady=8)
        self.selection_panel.pack(fill='x', pady=(0, 10))
        self.selection_hint = tk.Label(self.selection_panel, text='Select a result to see its score and review status.',
                                       bg='#FAF7FB', fg=COLOURS['muted'], font=('Arial', 10))
        self.selection_hint.pack(side='left')
        self.score_badge = tk.Label(self.selection_panel, text='', padx=10, pady=4,
                                    bg=COLOURS['success_wash'], fg=COLOURS['success'],
                                    font=('Arial', 9, 'bold'))
        self.priority_badge = tk.Label(self.selection_panel, text='', padx=10, pady=4,
                                       bg=COLOURS['warning_wash'], fg=COLOURS['warning'],
                                       font=('Arial', 9, 'bold'))

        table_frame = ttk.Frame(results_card, style='Card.TFrame'); table_frame.pack(fill='both', expand=True)
        self.table = ttk.Treeview(table_frame, columns=('title', 'score', 'priority', 'source', 'status'), show='headings', selectmode='browse')
        for key, title, width in [('title', 'Grant / program', 300), ('score', 'Score', 70), ('priority', 'Review priority', 150), ('source', 'Source', 150), ('status', 'Availability', 260)]:
            self.table.heading(key, text=title); self.table.column(key, width=width, minwidth=90)
        scroll = ttk.Scrollbar(table_frame, orient='vertical', command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set); scroll.pack(side='right', fill='y'); self.table.pack(fill='both', expand=True)
        self.table.bind('<Double-1>', self.open_source); self.table.bind('<<TreeviewSelect>>', self.select_result)
        self.links = {}; self.visible_records = {}
        ttk.Label(results_card,
                  text='Scores measure relevant wording, not eligibility. Double-click a result to open the funder page.',
                  style='Muted.TLabel', wraplength=900, padding=(0, 10, 0, 0)).pack(anchor='w')
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.sources_changed()
        self.set_stage(2)
        root.after(100, self.poll)

    def set_stage(self, active):
        """Keep the four-step journey visible without turning it into a wizard."""
        for index, (badge, label) in enumerate(self.step_widgets, 1):
            if index < active:
                badge.configure(bg=COLOURS['purple_dark'], fg='white', text='✓')
                label.configure(fg=COLOURS['purple_dark'])
            elif index == active:
                badge.configure(bg=COLOURS['pink'], fg='white', text=str(index))
                label.configure(fg=COLOURS['pink_dark'])
            else:
                badge.configure(bg='#E7E2E8', fg=COLOURS['muted'], text=str(index))
                label.configure(fg=COLOURS['muted'])

    def sources_changed(self):
        self.config = self.source_store.effective_config()
        enabled = sum(source.get('enabled', True) for source in self.config['sources'])
        self.source_count.set(f'{enabled} websites selected')

    def manage_sources(self):
        if self.running or self.saving:
            messagebox.showinfo('Please wait', 'Website settings cannot be changed during a search or while saving.', parent=self.root); return
        self.set_stage(1)
        SourceManager(self.root, self.source_store, self.sources_changed)
        self.set_stage(2 if not self.result else 3)
        self.status.set('Website choices saved. They will be used for the next search.')

    def start(self):
        if self.running or self.saving: return
        self.sources_changed()
        if not any(source.get('enabled', True) for source in self.config['sources']):
            messagebox.showinfo('Choose a website', 'Select at least one website under Manage websites before searching.', parent=self.root); return
        if self.result and not self.saved and not messagebox.askyesno('Start a new search?', 'The current results have not been saved. Replace them with a new search?', parent=self.root): return
        self.running = True; self.stop.clear(); self.result = None; self.saved = None
        self.set_stage(2)
        self.links.clear(); self.visible_records.clear(); self.table.delete(*self.table.get_children())
        self.select_result()
        self.search_button.configure(state='disabled'); self.stop_button.configure(state='normal')
        self.sources_button.configure(state='disabled')
        self.save_button.configure(state='disabled'); self.open_button.configure(state='disabled')
        self.progress.configure(value=0, maximum=max(1, sum(s.get('enabled', True) for s in self.config['sources'])))
        self.status.set('Searching public grant websites…'); self.summary.set('Keep the app open until the search completes.')
        threading.Thread(target=search, args=(self.config, self.events, self.stop), daemon=True).start()

    def cancel(self):
        self.stop.set(); self.stop_button.configure(state='disabled')
        self.status.set('Stopping after current page requests finish. Partial results can then be saved.')

    def change_level(self, _event=None):
        if not self.result:
            return
        self.saved = None; self.open_button.configure(state='disabled')
        self.refresh_results()
        self.status.set(f'{self.match_level.get()} match level selected. Save Excel or CSV to export this shortlist.')

    def refresh_results(self):
        if not self.result:
            return
        records, coverage, _, _ = self.result
        level = self.match_level.get().lower()
        current = sorted((r for r in records if grants.is_shortlisted(r, level)), key=grants.ranking_key)
        omitted = sum(not grants.is_closed(r) and not grants.is_shortlisted(r, level) for r in records)
        self.links.clear(); self.visible_records.clear(); self.table.delete(*self.table.get_children())
        self.details_button.configure(state='disabled')
        for i, record in enumerate(current):
            key = str(i); self.links[key] = record['Source URL']; self.visible_records[key] = record
            score = record.get('Screening score', 0)
            if record['Review priority'].startswith('Closed'):
                tag = 'closed'
            elif score >= 65:
                tag = 'strong'
            elif score >= 35:
                tag = 'review'
            else:
                tag = 'lead'
            self.table.insert('', 'end', iid=key,
                              values=(record['Grant / page'], score, record['Review priority'],
                                      record['Source'], record['Availability']), tags=(tag,))
        self.table.tag_configure('strong', background='#F0F8F4')
        self.table.tag_configure('review', background='#FFF9EA')
        self.table.tag_configure('lead', background='#FAF5FB')
        self.table.tag_configure('closed', foreground='#777177', background='#F2F0F2')
        pages = sum(c[3] for c in coverage); closed = sum(grants.is_closed(r) for r in records)
        self.summary.set(f'{len(current)} candidates at {self.match_level.get()} level · {omitted} pages omitted · {closed} closed/past rounds · {pages} pages read')

    def select_result(self, _event=None):
        selection = self.table.selection()
        self.details_button.configure(state='normal' if selection else 'disabled')
        if not selection or selection[0] not in self.visible_records:
            self.score_badge.pack_forget(); self.priority_badge.pack_forget()
            self.selection_hint.configure(text='Select a result to see its score and review status.')
            return
        record = self.visible_records[selection[0]]
        score = record.get('Screening score', 0)
        self.selection_hint.configure(text=record['Grant / page'])
        self.score_badge.configure(text=f'Score {score} / 100')
        if score >= 65:
            self.score_badge.configure(bg=COLOURS['success_wash'], fg=COLOURS['success'])
        elif score >= 35:
            self.score_badge.configure(bg=COLOURS['warning_wash'], fg=COLOURS['warning'])
        else:
            self.score_badge.configure(bg=COLOURS['pink_wash'], fg=COLOURS['pink_dark'])
        priority = record['Review priority']
        self.priority_badge.configure(text=priority)
        self.priority_badge.pack(side='right')
        self.score_badge.pack(side='right', padx=(8, 0))

    def show_score(self):
        selection = self.table.selection()
        if not selection:
            return
        record = self.visible_records[selection[0]]
        window = tk.Toplevel(self.root); window.title('Why this score?'); window.geometry('800x680'); window.minsize(620, 480)
        set_window_background(window); window.transient(self.root)
        add_brand_header(window, 'Why this score?', 'Review the evidence behind this result.', height=84)
        outer = ttk.Frame(window, style='App.TFrame', padding=20); outer.pack(fill='both', expand=True)
        body = ttk.Frame(outer, style='Card.TFrame', padding=20); body.pack(fill='both', expand=True)
        ttk.Label(body, text=record['Grant / page'], style='Title.TLabel', wraplength=720).pack(anchor='w')
        ttk.Label(body, text=f"{record['Review priority']} · {record['Source']}",
                  style='Muted.TLabel', padding=(0, 6)).pack(anchor='w')
        text_frame = ttk.Frame(body); text_frame.pack(fill='both', expand=True, pady=(10, 12))
        detail = tk.Text(text_frame, wrap='word', font=('Arial', 11), padx=14, pady=14,
                         relief='solid', bd=1, highlightthickness=0,
                         background='#FCFAFC', foreground=COLOURS['text'])
        scroll = ttk.Scrollbar(text_frame, orient='vertical', command=detail.yview)
        detail.configure(yscrollcommand=scroll.set); scroll.pack(side='right', fill='y'); detail.pack(fill='both', expand=True)
        detail.insert('1.0', score_explanation(record)); detail.configure(state='disabled')
        actions = ttk.Frame(body); actions.pack(fill='x')
        ttk.Button(actions, text='Open funder page', command=lambda: webbrowser.open(record['Source URL']),
                   style='Secondary.TButton').pack(side='left')
        ttk.Button(actions, text='Close', command=window.destroy,
                   style='Primary.TButton').pack(side='right')

    def poll(self):
        try:
            while True:
                event = self.events.get_nowait(); kind = event[0]
                if kind == 'progress':
                    self.progress.configure(value=event[1])
                    if not self.stop.is_set(): self.status.set(f'{event[1]} of {event[2]} sources checked · {event[3]}: {event[4]} pages read')
                elif kind == 'done':
                    self.running = False; self.result = event[1:5]
                    self.set_stage(3)
                    self.search_button.configure(state='normal'); self.stop_button.configure(state='disabled'); self.save_button.configure(state='normal')
                    self.sources_button.configure(state='normal')
                    self.refresh_results()
                    records, coverage, _, _ = self.result
                    pages = sum(c[3] for c in coverage)
                    self.status.set('Search stopped. Save Excel or CSV for partial results.' if event[5] else 'Search complete. Choose Save Excel or CSV to keep the results.')
                    if not pages: self.status.set('No pages could be read. Check your internet connection. Save Excel for the source coverage report.')
                elif kind == 'saved':
                    self.saving = False; self.saved = event[1]
                    self.set_stage(4)
                    self.search_button.configure(state='normal'); self.save_button.configure(state='normal'); self.open_button.configure(state='normal'); self.sources_button.configure(state='normal')
                    self.status.set(f'Saved: {self.saved}')
                elif kind in ('error', 'save_error'):
                    self.running = False; self.saving = False
                    self.set_stage(3 if self.result else 2)
                    self.search_button.configure(state='normal'); self.stop_button.configure(state='disabled')
                    self.sources_button.configure(state='normal')
                    self.save_button.configure(state='normal' if self.result else 'disabled')
                    self.status.set(event[1]); messagebox.showerror(APP_NAME, event[1], parent=self.root)
        except queue.Empty: pass
        self.root.after(100, self.poll)

    def save(self):
        if not self.result or self.saving: return
        level = self.match_level.get().lower()
        name = filedialog.asksaveasfilename(parent=self.root, title='Save grant results', defaultextension='.xlsx',
                                            filetypes=[('Excel workbook', '*.xlsx'), ('CSV shortlist', '*.csv')],
                                            initialfile=f'CorriLee-grants-{level}-{dt.datetime.now():%Y-%m-%d-%H%M%S}.xlsx')
        if not name: return
        path = Path(name)
        if path.suffix.lower() not in ('.xlsx', '.csv'):
            messagebox.showinfo('Choose a file type', 'The filename must end in .xlsx or .csv.', parent=self.root); return
        if path.exists():
            messagebox.showinfo('Choose a new filename', 'Existing reports are kept safe. Please choose a new filename.', parent=self.root); return
        self.saving = True; self.set_stage(4)
        self.save_button.configure(state='disabled'); self.search_button.configure(state='disabled')
        self.sources_button.configure(state='disabled')
        self.status.set(f'Preparing {path.suffix[1:].upper()} file…')
        def work():
            try:
                records, coverage, logs, today = self.result
                grants.make_report(path, list(records), coverage, logs, self.config['profile'], today, match_level=level)
                self.events.put(('saved', path))
            except OSError:
                self.events.put(('save_error', 'Could not save the file. Choose a writable folder and a new filename.'))
            except Exception:
                self.events.put(('save_error', 'Could not prepare the report. Your results are still available; please try again.'))
        threading.Thread(target=work, daemon=True).start()

    def open_saved(self):
        if not self.saved: return
        try:
            if sys.platform == 'win32': os.startfile(str(self.saved))
            else: subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(self.saved)])
        except OSError: messagebox.showinfo('Saved file', f'Open this file in a spreadsheet app:\n{self.saved}', parent=self.root)

    def open_source(self, _event=None):
        selection = self.table.selection()
        if selection: webbrowser.open(self.links[selection[0]])

    def close(self):
        if self.saving:
            messagebox.showinfo('Saving', 'Please wait until the report has finished saving.', parent=self.root); return
        if self.running:
            messagebox.showinfo('Search in progress', 'Click Stop search, then wait for current requests to finish before closing.', parent=self.root); return
        if self.result and not self.saved and not messagebox.askyesno('Close without saving?', 'Your search results have not been saved. Close anyway?', parent=self.root): return
        self.root.destroy()


def main():
    # Include certifi in packaged builds so HTTPS works on machines without Python.
    try:
        import certifi
        os.environ.setdefault('SSL_CERT_FILE', certifi.where())
    except ImportError: pass
    root = tk.Tk()
    try:
        GrantApp(root)
    except Exception:
        messagebox.showerror(APP_NAME, 'The app could not load its settings. Please download and extract a fresh copy of the complete app.', parent=root)
        root.destroy(); return 1
    root.mainloop()
    return 0


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        from smoke_test import run
        run(sys.argv[2])
    else:
        raise SystemExit(main())

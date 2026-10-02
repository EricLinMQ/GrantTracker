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


class SourceEditor:
    """Plain-language editor that translates UI choices into crawler fields."""
    def __init__(self, parent, source=None):
        self.source = dict(source or {})
        self.result = None
        self.check_events = queue.Queue()
        self.window = tk.Toplevel(parent)
        self.window.title('Edit website' if source else 'Add website')
        self.window.geometry('700x650'); self.window.minsize(560, 520)
        self.window.transient(parent)
        body = ttk.Frame(self.window, padding=20); body.pack(fill='both', expand=True)
        ttk.Label(body, text='Website name').pack(anchor='w')
        self.name = tk.StringVar(value=self.source.get('name', ''))
        ttk.Entry(body, textvariable=self.name).pack(fill='x', pady=(4, 14))
        ttk.Label(body, text='Starting pages — one web address per line').pack(anchor='w')
        self.urls = tk.Text(body, height=6, wrap='none')
        self.urls.pack(fill='x', pady=(4, 6)); self.urls.insert('1.0', '\n'.join(self.source.get('urls', [])))
        ttk.Label(body, text='The app stays on these websites and follows grant-related links.', foreground='#555555').pack(anchor='w')

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
        self.note = tk.Text(advanced, height=3, wrap='word'); self.note.grid(row=3, column=0, sticky='ew', pady=(3, 9))
        self.note.insert('1.0', self.source.get('note', ''))
        ttk.Label(advanced, text='Website sections to skip — paths such as /news, one per line').grid(row=4, column=0, sticky='w')
        self.exclusions = tk.Text(advanced, height=3, wrap='none'); self.exclusions.grid(row=5, column=0, sticky='ew', pady=(3, 0))
        self.exclusions.insert('1.0', '\n'.join(self.source.get('exclude_paths', [])))
        advanced.columnconfigure(0, weight=1)

        self.feedback = tk.StringVar(value='')
        ttk.Label(body, textvariable=self.feedback, wraplength=640).pack(fill='x', pady=(10, 4))
        actions = ttk.Frame(body); actions.pack(fill='x')
        self.check_button = ttk.Button(actions, text='Check website', command=self.check); self.check_button.pack(side='left')
        ttk.Button(actions, text='Cancel', command=self.window.destroy).pack(side='right')
        ttk.Button(actions, text='Save', command=self.save).pack(side='right', padx=8)
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
        self.window.geometry('900x580'); self.window.minsize(700, 440)
        self.window.transient(parent)
        body = ttk.Frame(self.window, padding=20); body.pack(fill='both', expand=True)
        ttk.Label(body, text='Choose which websites to check', font=('Arial', 18, 'bold')).pack(anchor='w')
        ttk.Label(body, text='Built-in websites can be changed or switched off. Websites you add are stored only on this computer.', wraplength=820).pack(anchor='w', pady=(5, 14))
        frame = ttk.Frame(body); frame.pack(fill='both', expand=True)
        self.table = ttk.Treeview(frame, columns=('on', 'name', 'origin', 'address'), show='headings', selectmode='browse')
        for key, title, width in [('on','Use',60),('name','Website',230),('origin','Type',100),('address','Starting address',420)]:
            self.table.heading(key, text=title); self.table.column(key, width=width, minwidth=55)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set); scroll.pack(side='right', fill='y'); self.table.pack(fill='both', expand=True)
        self.table.bind('<Double-1>', lambda _event: self.toggle())
        self.table.bind('<Return>', lambda _event: self.edit())
        actions = ttk.Frame(body, padding=(0, 12, 0, 0)); actions.pack(fill='x')
        ttk.Button(actions, text='Add website…', command=self.add).pack(side='left')
        ttk.Button(actions, text='Edit…', command=self.edit).pack(side='left', padx=6)
        ttk.Button(actions, text='Use / Skip', command=self.toggle).pack(side='left')
        ttk.Button(actions, text='Remove', command=self.remove).pack(side='left', padx=6)
        ttk.Button(actions, text='Restore', command=self.restore).pack(side='left')
        ttk.Button(actions, text='Restore all defaults', command=self.restore_all).pack(side='left', padx=6)
        ttk.Button(actions, text='Done', command=self.window.destroy).pack(side='right')
        self.summary = tk.StringVar(); ttk.Label(body, textvariable=self.summary).pack(anchor='w', pady=(10, 0))
        self.refresh(); self.window.wait_visibility(); self.window.grab_set(); self.window.focus_set(); self.window.wait_window()

    def refresh(self, select=None):
        self.table.delete(*self.table.get_children())
        config = self.store.effective_config()
        for source in config['sources']:
            identifier = source['id']
            values = ('Yes' if source.get('enabled', True) else 'No', source['name'],
                      'Added by you' if source.get('_origin') == 'user' else 'Built in', source['urls'][0])
            self.table.insert('', 'end', iid=identifier, values=values)
        enabled = sum(source.get('enabled', True) for source in config['sources'])
        self.summary.set(f'{enabled} of {len(config["sources"])} websites selected')
        if select and self.table.exists(select): self.table.selection_set(select); self.table.focus(select)
        self.changed()

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
        root.title(APP_NAME); root.geometry('980x700'); root.minsize(760, 540)
        self.events = queue.Queue(); self.stop = threading.Event()
        self.running = False; self.saving = False; self.result = None; self.saved = None
        self.source_store = SourceStore(grants.BASE / 'config.json')
        self.config = self.source_store.effective_config()
        style = ttk.Style(root)
        if 'clam' in style.theme_names(): style.theme_use('clam')
        style.configure('.', font=('Arial', 11))
        style.configure('Title.TLabel', font=('Arial', 23, 'bold'), foreground='#19334d')
        style.configure('Treeview', rowheight=32)
        style.configure('TButton', padding=(14, 9))
        frame = ttk.Frame(root, padding=24); frame.pack(fill='both', expand=True)
        ttk.Label(frame, text=APP_NAME, style='Title.TLabel').pack(anchor='w')
        ttk.Label(frame, text='Regional Australia · rules-based screening', padding=(0, 8)).pack(anchor='w')
        ttk.Label(frame, text='Search public grant pages, review the evidence, and save your shortlist as Excel or CSV.').pack(anchor='w')
        buttons = ttk.Frame(frame, padding=(0, 18)); buttons.pack(fill='x')
        self.search_button = ttk.Button(buttons, text='Search grants', command=self.start); self.search_button.pack(side='left')
        self.stop_button = ttk.Button(buttons, text='Stop search', command=self.cancel, state='disabled'); self.stop_button.pack(side='left', padx=8)
        self.save_button = ttk.Button(buttons, text='Save Excel or CSV…', command=self.save, state='disabled'); self.save_button.pack(side='left')
        self.open_button = ttk.Button(buttons, text='Open saved file', command=self.open_saved, state='disabled'); self.open_button.pack(side='left', padx=8)
        self.sources_button = ttk.Button(buttons, text='Manage websites…', command=self.manage_sources); self.sources_button.pack(side='left')
        filters = ttk.Frame(frame, padding=(0, 0, 0, 12)); filters.pack(fill='x')
        ttk.Label(filters, text='Match level:').pack(side='left', padx=(0, 5))
        self.match_level = tk.StringVar(value='Balanced')
        self.level_box = ttk.Combobox(filters, textvariable=self.match_level, values=('Strict', 'Balanced', 'Broad'), state='readonly', width=10)
        self.level_box.pack(side='left'); self.level_box.bind('<<ComboboxSelected>>', self.change_level)
        self.details_button = ttk.Button(filters, text='Why this score?', command=self.show_score, state='disabled'); self.details_button.pack(side='left', padx=12)
        self.source_count = tk.StringVar(); ttk.Label(filters, textvariable=self.source_count).pack(side='right')
        initial_status = self.source_store.warning or 'Ready. Internet access is required. Searches may take several minutes.'
        self.status = tk.StringVar(value=initial_status)
        ttk.Label(frame, textvariable=self.status, wraplength=880).pack(fill='x')
        self.progress = ttk.Progressbar(frame, maximum=1); self.progress.pack(fill='x', pady=(12, 16))
        self.summary = tk.StringVar(value='No search yet. Closed rounds will be excluded from your shortlist.')
        ttk.Label(frame, textvariable=self.summary, wraplength=880).pack(anchor='w', pady=(0, 10))
        table_frame = ttk.Frame(frame); table_frame.pack(fill='both', expand=True)
        self.table = ttk.Treeview(table_frame, columns=('title', 'score', 'priority', 'source', 'status'), show='headings', selectmode='browse')
        for key, title, width in [('title', 'Grant / program', 250), ('score', 'Score', 70), ('priority', 'Review priority', 150), ('source', 'Source', 130), ('status', 'Availability', 220)]:
            self.table.heading(key, text=title); self.table.column(key, width=width, minwidth=90)
        scroll = ttk.Scrollbar(table_frame, orient='vertical', command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set); scroll.pack(side='right', fill='y'); self.table.pack(fill='both', expand=True)
        self.table.bind('<Double-1>', self.open_source); self.table.bind('<<TreeviewSelect>>', self.select_result)
        self.links = {}; self.visible_records = {}
        ttk.Label(frame, text='Results are sorted from highest to lowest score. Scores measure relevant wording, not eligibility.\nDouble-click to open the funder. Excel includes the complete report; CSV exports the current shortlist.', wraplength=880, padding=(0, 14)).pack(anchor='w')
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.sources_changed()
        root.after(100, self.poll)

    def sources_changed(self):
        self.config = self.source_store.effective_config()
        enabled = sum(source.get('enabled', True) for source in self.config['sources'])
        self.source_count.set(f'{enabled} websites selected')

    def manage_sources(self):
        if self.running or self.saving:
            messagebox.showinfo('Please wait', 'Website settings cannot be changed during a search or while saving.', parent=self.root); return
        SourceManager(self.root, self.source_store, self.sources_changed)
        self.status.set('Website choices saved. They will be used for the next search.')

    def start(self):
        if self.running or self.saving: return
        self.sources_changed()
        if not any(source.get('enabled', True) for source in self.config['sources']):
            messagebox.showinfo('Choose a website', 'Select at least one website under Manage websites before searching.', parent=self.root); return
        if self.result and not self.saved and not messagebox.askyesno('Start a new search?', 'The current results have not been saved. Replace them with a new search?', parent=self.root): return
        self.running = True; self.stop.clear(); self.result = None; self.saved = None
        self.links.clear(); self.table.delete(*self.table.get_children())
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
            self.table.insert('', 'end', iid=key, values=(record['Grant / page'], record.get('Screening score', 0), record['Review priority'], record['Source'], record['Availability']))
        pages = sum(c[3] for c in coverage); closed = sum(grants.is_closed(r) for r in records)
        self.summary.set(f'{len(current)} candidates at {self.match_level.get()} level · {omitted} pages omitted · {closed} closed/past rounds · {pages} pages read')

    def select_result(self, _event=None):
        self.details_button.configure(state='normal' if self.table.selection() else 'disabled')

    def show_score(self):
        selection = self.table.selection()
        if not selection:
            return
        record = self.visible_records[selection[0]]
        window = tk.Toplevel(self.root); window.title('Why this score?'); window.geometry('760x620'); window.minsize(560, 420)
        body = ttk.Frame(window, padding=20); body.pack(fill='both', expand=True)
        ttk.Label(body, text=record['Grant / page'], style='Title.TLabel', wraplength=700).pack(anchor='w')
        ttk.Label(body, text=f"{record['Review priority']} · {record['Source']}", padding=(0, 6)).pack(anchor='w')
        text_frame = ttk.Frame(body); text_frame.pack(fill='both', expand=True, pady=(10, 12))
        detail = tk.Text(text_frame, wrap='word', font=('Arial', 11), padx=12, pady=12)
        scroll = ttk.Scrollbar(text_frame, orient='vertical', command=detail.yview)
        detail.configure(yscrollcommand=scroll.set); scroll.pack(side='right', fill='y'); detail.pack(fill='both', expand=True)
        detail.insert('1.0', score_explanation(record)); detail.configure(state='disabled')
        actions = ttk.Frame(body); actions.pack(fill='x')
        ttk.Button(actions, text='Open funder page', command=lambda: webbrowser.open(record['Source URL'])).pack(side='left')
        ttk.Button(actions, text='Close', command=window.destroy).pack(side='right')

    def poll(self):
        try:
            while True:
                event = self.events.get_nowait(); kind = event[0]
                if kind == 'progress':
                    self.progress.configure(value=event[1])
                    if not self.stop.is_set(): self.status.set(f'{event[1]} of {event[2]} sources checked · {event[3]}: {event[4]} pages read')
                elif kind == 'done':
                    self.running = False; self.result = event[1:5]
                    self.search_button.configure(state='normal'); self.stop_button.configure(state='disabled'); self.save_button.configure(state='normal')
                    self.sources_button.configure(state='normal')
                    self.refresh_results()
                    records, coverage, _, _ = self.result
                    pages = sum(c[3] for c in coverage)
                    self.status.set('Search stopped. Save Excel or CSV for partial results.' if event[5] else 'Search complete. Choose Save Excel or CSV to keep the results.')
                    if not pages: self.status.set('No pages could be read. Check your internet connection. Save Excel for the source coverage report.')
                elif kind == 'saved':
                    self.saving = False; self.saved = event[1]
                    self.search_button.configure(state='normal'); self.save_button.configure(state='normal'); self.open_button.configure(state='normal'); self.sources_button.configure(state='normal')
                    self.status.set(f'Saved: {self.saved}')
                elif kind in ('error', 'save_error'):
                    self.running = False; self.saving = False
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
        self.saving = True; self.save_button.configure(state='disabled'); self.search_button.configure(state='disabled')
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

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

APP_NAME = 'CorriLee Grant Ranker'


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
        self.config = grants.load_config(grants.BASE / 'config.json')
        style = ttk.Style(root)
        if 'clam' in style.theme_names(): style.theme_use('clam')
        style.configure('.', font=('Arial', 11))
        style.configure('Title.TLabel', font=('Arial', 23, 'bold'), foreground='#19334d')
        style.configure('Treeview', rowheight=32)
        style.configure('TButton', padding=(14, 9))
        frame = ttk.Frame(root, padding=24); frame.pack(fill='both', expand=True)
        ttk.Label(frame, text=APP_NAME, style='Title.TLabel').pack(anchor='w')
        ttk.Label(frame, text='Regional Australia · rules-based screening', padding=(0, 8)).pack(anchor='w')
        ttk.Label(frame, text='Search public grant pages, review the evidence, and save your Excel shortlist.').pack(anchor='w')
        buttons = ttk.Frame(frame, padding=(0, 18)); buttons.pack(fill='x')
        self.search_button = ttk.Button(buttons, text='Search grants', command=self.start); self.search_button.pack(side='left')
        self.stop_button = ttk.Button(buttons, text='Stop search', command=self.cancel, state='disabled'); self.stop_button.pack(side='left', padx=8)
        self.save_button = ttk.Button(buttons, text='Save Excel…', command=self.save, state='disabled'); self.save_button.pack(side='left')
        self.open_button = ttk.Button(buttons, text='Open saved file', command=self.open_saved, state='disabled'); self.open_button.pack(side='left', padx=8)
        ttk.Label(buttons, text='Match level:').pack(side='left', padx=(12, 5))
        self.match_level = tk.StringVar(value='Balanced')
        self.level_box = ttk.Combobox(buttons, textvariable=self.match_level, values=('Strict', 'Balanced', 'Broad'), state='readonly', width=10)
        self.level_box.pack(side='left'); self.level_box.bind('<<ComboboxSelected>>', self.change_level)
        self.status = tk.StringVar(value='Ready. Internet access is required. Searches may take several minutes.')
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
        self.table.bind('<Double-1>', self.open_source)
        self.links = {}
        ttk.Label(frame, text='Screening scores measure relevant wording, not eligibility. Possible conflicts rank below other candidates.\nDouble-click to open the funder. Excel includes score breakdowns, review flags and source coverage.', wraplength=880, padding=(0, 14)).pack(anchor='w')
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)

    def start(self):
        if self.running or self.saving: return
        if self.result and not self.saved and not messagebox.askyesno('Start a new search?', 'The current results have not been saved. Replace them with a new search?', parent=self.root): return
        self.running = True; self.stop.clear(); self.result = None; self.saved = None
        self.links.clear(); self.table.delete(*self.table.get_children())
        self.search_button.configure(state='disabled'); self.stop_button.configure(state='normal')
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
        self.status.set(f'{self.match_level.get()} match level selected. Save Excel to export this shortlist.')

    def refresh_results(self):
        if not self.result:
            return
        records, coverage, _, _ = self.result
        level = self.match_level.get().lower()
        current = sorted((r for r in records if grants.is_shortlisted(r, level)), key=grants.ranking_key)
        omitted = sum(not grants.is_closed(r) and not grants.is_shortlisted(r, level) for r in records)
        self.links.clear(); self.table.delete(*self.table.get_children())
        for i, record in enumerate(current):
            key = str(i); self.links[key] = record['Source URL']
            self.table.insert('', 'end', iid=key, values=(record['Grant / page'], record.get('Screening score', 0), record['Review priority'], record['Source'], record['Availability']))
        pages = sum(c[3] for c in coverage); closed = sum(grants.is_closed(r) for r in records)
        self.summary.set(f'{len(current)} candidates at {self.match_level.get()} level · {omitted} pages omitted · {closed} closed/past rounds · {pages} pages read')

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
                    self.refresh_results()
                    records, coverage, _, _ = self.result
                    pages = sum(c[3] for c in coverage)
                    self.status.set('Search stopped. Save Excel for partial results and coverage.' if event[5] else 'Search complete. Choose Save Excel to keep the results.')
                    if not pages: self.status.set('No pages could be read. Check your internet connection. Save Excel for the source coverage report.')
                elif kind == 'saved':
                    self.saving = False; self.saved = event[1]
                    self.search_button.configure(state='normal'); self.save_button.configure(state='normal'); self.open_button.configure(state='normal')
                    self.status.set(f'Saved: {self.saved}')
                elif kind in ('error', 'save_error'):
                    self.running = False; self.saving = False
                    self.search_button.configure(state='normal'); self.stop_button.configure(state='disabled')
                    self.save_button.configure(state='normal' if self.result else 'disabled')
                    self.status.set(event[1]); messagebox.showerror(APP_NAME, event[1], parent=self.root)
        except queue.Empty: pass
        self.root.after(100, self.poll)

    def save(self):
        if not self.result or self.saving: return
        level = self.match_level.get().lower()
        name = filedialog.asksaveasfilename(parent=self.root, title='Save grant results', defaultextension='.xlsx', filetypes=[('Excel workbook', '*.xlsx')], initialfile=f'CorriLee-grants-{level}-{dt.datetime.now():%Y-%m-%d-%H%M%S}.xlsx')
        if not name: return
        path = Path(name)
        if path.exists():
            messagebox.showinfo('Choose a new filename', 'Existing reports are kept safe. Please choose a new filename.', parent=self.root); return
        self.saving = True; self.save_button.configure(state='disabled'); self.search_button.configure(state='disabled')
        self.status.set('Preparing Excel file…')
        def work():
            try:
                records, coverage, logs, today = self.result
                grants.make_workbook(path, list(records), coverage, logs, self.config['profile'], today, match_level=level)
                self.events.put(('saved', path))
            except OSError:
                self.events.put(('save_error', 'Could not save the file. Choose a writable folder and a new filename.'))
            except Exception:
                self.events.put(('save_error', 'Could not prepare Excel. Your results are still available; please try again.'))
        threading.Thread(target=work, daemon=True).start()

    def open_saved(self):
        if not self.saved: return
        try:
            if sys.platform == 'win32': os.startfile(str(self.saved))
            else: subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(self.saved)])
        except OSError: messagebox.showinfo('Saved file', f'Open this file in Excel or another spreadsheet app:\n{self.saved}', parent=self.root)

    def open_source(self, _event=None):
        selection = self.table.selection()
        if selection: webbrowser.open(self.links[selection[0]])

    def close(self):
        if self.saving:
            messagebox.showinfo('Saving', 'Please wait until the Excel file has finished saving.', parent=self.root); return
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

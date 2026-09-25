"""Runs inside the packaged executable on each native build runner."""
import datetime as dt
import json
from pathlib import Path
import tempfile
import tkinter as tk
import zipfile
import grant_finder as g

def run(output):
    from desktop_app import GrantApp
    root = tk.Tk(); root.withdraw()
    try:
        app = GrantApp(root)
        today = dt.date(2026, 9, 25)
        source = {'name':'Fixture', 'urls':['https://example.org/grant'], 'domains':['example.org']}
        text = 'Regional community education grants. Eligible charities can apply. Applications close 3 December 2026.'
        current = g.assess('Community Grant', text, source['urls'][0], source, app.config['profile'], today)
        closed = g.assess('Audience Development Program', 'Application closed\n' + text, 'https://example.org/closed', source, app.config['profile'], today)
        app.events.put(('done', [current, closed], [], [], today, False)); app.poll(); root.update()
        assert len(app.table.get_children()) == 1
        assert app.table.item(app.table.get_children()[0])['values'][0] == 'Community Grant'
        assert int(app.table.item(app.table.get_children()[0])['values'][1]) == 60
        assert current['Screening score'] == 60
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'results.xlsx'
            g.make_workbook(path, [current,closed], [], [], app.config['profile'], today)
            with zipfile.ZipFile(path) as z:
                assert b'Audience Development Program' not in z.read('xl/worksheets/sheet1.xml')
                assert b'Audience Development Program' in z.read('xl/worksheets/sheet2.xml')
                assert b'Screening score / 100' in z.read('xl/worksheets/sheet1.xml')
                assert b'Activities points' in z.read('xl/worksheets/sheet4.xml')
        import certifi
        assert Path(certifi.where()).is_file()
        Path(output).write_text(json.dumps({'passed':True,'sources':len(app.config['sources']), 'gui':True,'excel':True,'closed_round_excluded':True, 'screening_score':True}))
    finally: root.destroy()

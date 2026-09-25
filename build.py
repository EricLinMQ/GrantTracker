"""Maintainer build entry point; end users receive the packaged app."""
import hashlib
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
NAME = 'CorriLee Grant Ranker'

def main():
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                    '--windowed', '--onedir', '--name', NAME,
                    '--add-data', f'{ROOT / "config.json"}:.',
                    '--collect-data', 'certifi', '--hidden-import', 'pypdf',
                    '--osx-bundle-identifier', 'org.corrilee.grantranker',
                    str(ROOT / 'desktop_app.py')], cwd=ROOT, check=True)
    label = 'Windows-x64' if sys.platform == 'win32' else f'macOS-{platform.machine()}'
    dist = ROOT / 'dist'; output = ROOT / 'downloads'; output.mkdir(exist_ok=True)
    if sys.platform == 'darwin':
        app = dist / f'{NAME}.app'
        subprocess.run([str(app / 'Contents/MacOS' / NAME), '--self-test', str(output / 'smoke-test.json')], check=True, timeout=90)
        target = output / f'CorriLee-Grant-Ranker-{label}.zip'
        subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(app), str(target)], check=True)
    else:
        app = dist / NAME
        subprocess.run([str(app / f'{NAME}.exe'), '--self-test', str(output / 'smoke-test.json')], check=True, timeout=90)
        shutil.copy2(ROOT / 'START-HERE.txt', app / 'START-HERE.txt')
        target = Path(shutil.make_archive(str(output / f'CorriLee-Grant-Ranker-{label}'), 'zip', dist, NAME))
    (output / (target.name + '.sha256')).write_text(hashlib.sha256(target.read_bytes()).hexdigest() + '  ' + target.name + '\n')
    shutil.copy2(ROOT / 'START-HERE.txt', output / 'START-HERE.txt')
    print(target)

if __name__ == '__main__': main()

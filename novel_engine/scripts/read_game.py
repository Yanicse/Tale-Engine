"""Local page of the game: reading, plus entering a turn through the official path (127.0.0.1 only, no external resource)."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser

PKG_PARENT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PKG_PARENT))
from novel_engine.contracts import ROOT
from novel_engine.engines import ENGINES, available, default_engine
from novel_engine.reader import Reader
from novel_engine.resume import ACTIVE

PAGE = PKG_PARENT / 'novel_engine/reader_page.html'
RESOURCES = PKG_PARENT / 'novel_engine/resources'
# Only these subfolders and types are served (local fonts and icons), never the rest of the repository.
TYPES_RESOURCES = {'fonts': {'.ttf': 'font/ttf', '.woff2': 'font/woff2'}, 'icons': {'.svg': 'image/svg+xml'}}
ENGINE = PKG_PARENT / 'novel_engine/scripts/play_narrative.py'
MODES = {'new': [], 'resume': ['--resume'], 'retry': ['--resume', '--retry'], 'reevaluate': ['--reevaluate']}


def engine_choices():
    """Engines the page may offer: all of them, with those that can run on this machine marked."""
    ready = available()
    return {'engines': [{'id': name, 'available': name in ready} for name in ENGINES],
            'default_engine': default_engine()}


class Turns:
    """One turn at a time, launched through the official path play_narrative.py (no resolution specific to this page)."""

    def __init__(self, campaign):
        self.campaign = str(campaign)
        self.lock = threading.Lock()
        self.state = {'in_progress': False, 'result': None, 'error': None}

    def command(self, text, engine, mode):
        base = [sys.executable, str(ENGINE), '--campaign', self.campaign, '--json', '--engine', engine]
        # Form --text=...: an action starting with "-" must not be taken for an option.
        return base + (['--text=' + text] if mode == 'new' else MODES[mode])

    def cast(self, text, engine, mode):
        if engine not in ENGINES or mode not in MODES:
            raise ValueError('Unknown engine or mode.')
        if mode == 'new' and not text.strip():
            raise ValueError('Empty action.')
        with self.lock:
            if self.state['in_progress']:
                return False
            self.state = {'in_progress': True, 'result': None, 'error': None}
        threading.Thread(target=self._execute, args=(self.command(text.strip(), engine, mode),), daemon=True).start()
        return True

    def _execute(self, command):
        result = error = None
        try:
            finished = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', cwd=ROOT)
            try:
                result = json.loads(finished.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                error = (finished.stderr or finished.stdout or 'No output.').strip()[-600:]
            else:
                if finished.returncode and not isinstance(result, dict):
                    error = 'Unexpected output.'
        except Exception as exc:
            error = type(exc).__name__ + ': ' + str(exc)
        if isinstance(result, dict):
            result = {k: result.get(k) for k in ('status', 'reason') if k in result}
        with self.lock:
            self.state = {'in_progress': False, 'result': result, 'error': error}

    def read(self):
        with self.lock:
            return dict(self.state)


def make_handler(reader, port, turns):
    allowed = {f'127.0.0.1:{port}', f'localhost:{port}'}

    class Handler(BaseHTTPRequestHandler):
        server_version = 'LecturePartie'

        def log_message(self, *args):
            pass

        def _send(self, status, body, content_type, cache='no-store'):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', cache)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(body)

        def resource(self, path):
            segments = path.split('/')[2:]
            if len(segments) != 2 or segments[0] not in TYPES_RESOURCES:
                return self._json({'error': 'Not found.'}, 404)
            name = segments[1]
            file = (RESOURCES / segments[0] / name).resolve()
            type_mime = TYPES_RESOURCES[segments[0]].get(file.suffix)
            if type_mime is None or file.parent != (RESOURCES / segments[0]).resolve() or not file.is_file():
                return self._json({'error': 'Not found.'}, 404)
            return self._send(200, file.read_bytes(), type_mime, 'public, max-age=3600')

        def _json(self, value, status=200):
            self._send(status, json.dumps(value, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')

        def do_GET(self):
            if self.headers.get('Host', '') not in allowed:
                return self._json({'error': 'Host refused.'}, 403)
            url = urlsplit(self.path)
            query = {key: values[0] for key, values in parse_qs(url.query).items()}
            try:
                if url.path in ('/', '/index.html'):
                    return self._send(200, PAGE.read_bytes(), 'text/html; charset=utf-8')
                if url.path.startswith('/resources/'):
                    return self.resource(url.path)
                routes = {
                    '/api/state': lambda: reader.state(),
                    '/api/config': lambda: {**reader.config(), **engine_choices()},
                    '/api/story': lambda: reader.story(query.get('before'), query.get('n', 20)),
                    '/api/search': lambda: reader.search(query.get('q', '')),
                    '/api/possessions': lambda: reader.possessions(),
                    '/api/npc': lambda: reader.npc(),
                    '/api/agenda': lambda: reader.agenda(),
                    '/api/summaries': lambda: reader.summaries(),
                    '/api/abilities': lambda: reader.abilities(query.get('q', '')),
                    '/api/creations': lambda: reader.creations(),
                    '/api/turn': lambda: turns.read(),
                }
                if url.path not in routes:
                    return self._json({'error': 'Not found.'}, 404)
                result = routes[url.path]()
                if result is None:
                    return self._json({'error': 'Creative format inactive.'}, 404)
                return self._json(result)
            except (ValueError, OSError, KeyError) as exc:
                return self._json({'error': str(exc)}, 500)
            except Exception as exc:  # A locked or migrating database must not stop the server.
                return self._json({'error': type(exc).__name__ + ': ' + str(exc)}, 503)

        def _refuse(self):
            self.send_response(405)
            self.send_header('Allow', 'GET')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def do_POST(self):
            # Only POST is accepted: launch a turn. Local host, local origin and JSON required (no form from another site).
            if self.path != '/api/turn':
                return self._refuse()
            try:  # The body is read first, to answer cleanly even to refused requests.
                size = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                size = 0
            raw = self.rfile.read(size) if 0 < size <= 20000 else b''
            origin = self.headers.get('Origin')
            if self.headers.get('Host', '') not in allowed or (origin and origin not in {'http://' + h for h in allowed}):
                return self._json({'error': 'Host refused.'}, 403)
            if not self.headers.get('Content-Type', '').startswith('application/json'):
                return self._json({'error': 'JSON required.'}, 415)
            try:
                if not raw:
                    raise ValueError('Invalid size.')
                body = json.loads(raw.decode('utf-8'))
                text = body.get('text', '')
                if not isinstance(text, str):
                    raise ValueError('Invalid text.')
                if not turns.cast(text, body.get('engine') or default_engine(), body.get('mode', 'new')):
                    return self._json({'error': 'A turn is already running.'}, 409)
                return self._json({'in_progress': True}, 202)
            except (ValueError, UnicodeDecodeError) as exc:
                return self._json({'error': str(exc)}, 400)

        do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _refuse

    return Handler


def make_server(path, port=8765):
    server = ThreadingHTTPServer(('127.0.0.1', port), lambda *args: None)
    port = server.server_address[1]
    server.RequestHandlerClass = make_handler(Reader(path), port, Turns(path))
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', help='Existing save; active campaign by default')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    if args.campaign:
        path = Path(args.campaign)
    else:
        # Direct reading of the selection: active_campaign() would open a write transaction.
        selected = json.loads(Path(ACTIVE).read_text(encoding='utf-8'))
        if selected['campaign_path'] is None:
            parser.error('No active campaign.')
        path = (ROOT / selected['campaign_path']).resolve()
        if not path.is_relative_to((ROOT / 'campaigns').resolve()):
            parser.error('Active save outside the campaigns/ folder.')
    server = make_server(path, args.port)
    url = f'http://127.0.0.1:{server.server_address[1]}/'
    print('Game ' + str(Path(path).name) + ' : ' + url + ' (Ctrl+C to stop)', file=sys.stderr)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())

import hmac
import logging
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import timedelta
from urllib.parse import urlsplit

from authlib.integrations.flask_client import OAuth
from flask import abort, jsonify, redirect, render_template_string, request, session


HTML = '''<!doctype html>
<html lang="hu"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>2B Bot konzol</title><style>body{background:#15171b;color:#ddd;font:16px system-ui;max-width:1100px;margin:24px auto;padding:16px}pre{background:#080a0d;padding:16px;height:60vh;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}input{width:70%;padding:12px}button{padding:12px;cursor:pointer}</style>
<h1>2B Bot webkonzol</h1><p>{{ email }} · Rendszerparancsok a bot jogosultságaival. Nem interaktív terminál.</p>
<pre id="log"></pre><form id="cmd"><input id="line" maxlength="4096" autocomplete="off" placeholder="Rendszerparancs vagy :reload / :reset / :restart / :stop"><button>Futtatás</button></form>
<p id="status"></p><button id="logout">Kijelentkezés</button>
<script>
const csrf={{ csrf|tojson }};let cursor=0,busy=false;
async function poll(){if(busy)return;busy=true;try{const r=await fetch('/console/logs?after='+cursor,{cache:'no-store'});if(r.status===401){location.href='/console';return}if(!r.ok)throw Error('Naplóhiba: '+r.status);const d=await r.json();cursor=d.cursor;const el=document.getElementById('log');if(d.lines.length){el.textContent=(el.textContent+d.lines.join('')).slice(-200000);el.scrollTop=el.scrollHeight}}catch(e){document.getElementById('status').textContent=e.message}finally{busy=false}}
document.getElementById('cmd').onsubmit=async e=>{e.preventDefault();const input=document.getElementById('line'),command=input.value;if(!command.trim())return;if(!confirm('Futtatod a bot jogosultságaival?\n'+command))return;try{const r=await fetch('/console/execute',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({command})});const d=await r.json();document.getElementById('status').textContent=d.message||d.error;if(r.ok)input.value=''}catch(e){document.getElementById('status').textContent=e.message}};
document.getElementById('logout').onclick=async()=>{await fetch('/console/logout',{method:'POST',headers:{'X-CSRF-Token':csrf}});location.href='/console'};poll();setInterval(poll,1000);
</script></html>'''


def install_console(app, bot, base_url, control):
    base_url = base_url.rstrip('/')
    required = ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'CONSOLE_SESSION_SECRET')
    enabled = all(os.getenv(key) for key in required)
    if enabled and (urlsplit(base_url).scheme != 'https' or len(os.environ['CONSOLE_SESSION_SECRET']) < 32):
        raise ValueError('A konzolhoz HTTPS BASE_URL és legalább 32 karakteres CONSOLE_SESSION_SECRET szükséges.')
    allowed = {x.strip().strip('<>').lower() for x in os.getenv('CONSOLE_ALLOWED_EMAILS', 'benedek.balint.kiss@gmail.com').split(',') if x.strip()}
    app.config.update(SECRET_KEY=os.getenv('CONSOLE_SESSION_SECRET') or secrets.token_hex(32), SESSION_COOKIE_NAME='__Host-2bconsole', SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_PATH='/', PERMANENT_SESSION_LIFETIME=timedelta(minutes=30))
    oauth = OAuth(app)
    google = oauth.register(name='google', client_id=os.getenv('GOOGLE_CLIENT_ID'), client_secret=os.getenv('GOOGLE_CLIENT_SECRET'), server_metadata_url='https://accounts.google.com/.well-known/openid-configuration', client_kwargs={'scope': 'openid email profile', 'code_challenge_method': 'S256'})
    lines, lock, running = deque(maxlen=2000), threading.Lock(), threading.Lock()
    counter = 0

    def append(text):
        nonlocal counter
        with lock:
            counter += 1
            lines.append((counter, text[:8192]))

    class Tee:
        def __init__(self, original):
            self.original = original
        def write(self, text):
            result = self.original.write(text)
            if text:
                append(text)
            return result
        def flush(self):
            self.original.flush()
        def __getattr__(self, name):
            return getattr(self.original, name)

    sys.stdout, sys.stderr = Tee(sys.stdout), Tee(sys.stderr)
    handler = logging.StreamHandler(sys.stderr)
    logging.getLogger('discord').addHandler(handler)
    logging.getLogger('discord').setLevel(logging.INFO)

    @app.before_request
    def guard():
        if not request.path.startswith('/console'):
            return
        if not enabled:
            abort(503, 'A Google-konzol még nincs beállítva.')
        if request.path in ('/console/login', '/console/callback'):
            return
        authenticated = session.get('email') in allowed and time.time() - session.get('login_at', 0) < 1800
        if not authenticated:
            if request.path == '/console':
                return redirect('/console/login')
            return jsonify(error='Újbóli bejelentkezés szükséges.'), 401
        if request.method == 'POST':
            expected = session.get('csrf', '')
            if request.headers.get('Origin') != base_url or not expected or not hmac.compare_digest(expected, request.headers.get('X-CSRF-Token', '')):
                abort(403)

    @app.after_request
    def headers(response):
        if request.path.startswith('/console'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Frame-Options'] = 'DENY'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.get('/console/login')
    def console_login():
        session.clear()
        return google.authorize_redirect(base_url + '/console/callback', prompt='select_account')

    @app.get('/console/callback')
    def console_callback():
        try:
            token = google.authorize_access_token()
            user = token.get('userinfo') or {}
            claims = token.get('id_token_claims') or {}
            email = str(user.get('email') or claims.get('email') or '').strip().strip('<>').lower()
            verified = user.get('email_verified', claims.get('email_verified', False))
            if isinstance(verified, str):
                verified = verified.strip().lower() == 'true'
            append(f'\n[Konzol] Google-válasz: email={email!r}, email_verified={verified!r}, engedélyezett={email in allowed}\n')
        except Exception as exc:
            session.clear()
            append(f'\n[Konzolhiba] Google-hitelesítés: {type(exc).__name__}: {exc}\n')
            abort(401, 'Sikertelen Google-hitelesítés.')
        if not email:
            session.clear()
            abort(401, 'A Google nem küldött e-mail-címet.')
        if verified is not True:
            session.clear()
            abort(403, 'A Google-fiók e-mail-címe nincs hitelesítve.')
        if email not in allowed:
            session.clear()
            abort(403, 'Ez a Google-fiók nincs engedélyezve.')
        session.clear()
        session.permanent = True
        session.update(email=email, login_at=time.time(), csrf=secrets.token_urlsafe(32))
        append(f'\n[Konzol] Bejelentkezés: {email}\n')
        return redirect('/console')

    @app.get('/console')
    def console_page():
        return render_template_string(HTML, email=session['email'], csrf=session['csrf'])

    @app.get('/console/logs')
    def console_logs():
        try:
            after = int(request.args.get('after', 0))
        except ValueError:
            abort(400)
        with lock:
            return jsonify(cursor=counter, lines=[text for number, text in lines if number > after])

    def execute(command, email):
        try:
            append(f'\n[{email}] $ {command}\n')
            if command in (':reload', ':reset', ':restart', ':stop'):
                control(command[1:])
                append('[Konzol] Vezérlőparancs elküldve.\n')
                return
            process = subprocess.Popen(command, shell=True, executable='/bin/sh', stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
            def read():
                decoder = __import__('codecs').getincrementaldecoder('utf-8')(errors='replace')
                try:
                    while True:
                        chunk = os.read(process.stdout.fileno(), 4096)
                        if not chunk:
                            break
                        append(decoder.decode(chunk))
                    tail = decoder.decode(b'', final=True)
                    if tail:
                        append(tail)
                finally:
                    process.stdout.close()
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                append('\n[Konzol] 60 másodperces időkorlát.\n')
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                reader.join(timeout=2)
            append(f'\n[Kilépési kód: {process.returncode}]\n')
        except Exception as exc:
            append(f'\n[Konzolhiba] {type(exc).__name__}: {exc}\n')
        finally:
            running.release()

    @app.post('/console/execute')
    def console_execute():
        data = request.get_json(silent=True)
        command = data.get('command') if isinstance(data, dict) else None
        if not isinstance(command, str) or not command.strip() or len(command) > 4096 or '\x00' in command:
            return jsonify(error='Érvénytelen parancs.'), 400
        if not running.acquire(blocking=False):
            return jsonify(error='Már fut egy parancs.'), 409
        try:
            threading.Thread(target=execute, args=(command, session['email']), daemon=True).start()
        except Exception:
            running.release()
            raise
        return jsonify(message='A parancs elindult.'), 202

    @app.post('/console/logout')
    def console_logout():
        session.clear()
        return jsonify(message='Kijelentkeztél.')

    @bot.tree.command(name='console', description='Google-belépéssel védett botkonzol')
    async def console_command(interaction):
        await interaction.response.send_message(base_url + '/console', ephemeral=True)

    if not enabled:
        print('A webkonzol letiltva: hiányzó Google OAuth- vagy munkamenet-beállítások.', flush=True)

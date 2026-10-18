import asyncio
import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from urllib.parse import urlencode

import discord
import requests
from discord.ext import tasks
from flask import abort, jsonify, redirect, render_template_string, request, session


PAGE = """<!doctype html><html lang="hu"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Spotify összekapcsolás</title><h1>Spotify összekapcsolás</h1>
<p>A csatlakoztatott Spotify-fiók aktuális zenéjét a bot nyilvánosan megjelenítheti.</p>
<button id="connect">Összekapcsolás Spotifyjal</button>
<p>Újraindításokhoz mentsd a frissítőtokent a Heroku SPOTIFY_REFRESH_TOKEN változójába. Titok: ne küldd el másnak!</p>
<button id="reveal">Frissítőtoken megjelenítése</button><br><textarea id="token" readonly rows="4" cols="65" hidden></textarea>
<p id="message"></p><a href="/console">Vissza a konzolhoz</a>
<script>const csrf={{ csrf|tojson }};
async function post(path){const r=await fetch(path,{method:'POST',headers:{'X-CSRF-Token':csrf}});if(!r.ok)throw Error('Sikertelen művelet. Részletek a szervernaplóban.');return r.json()}
document.getElementById('connect').onclick=async()=>{try{const d=await post('/console/spotify/connect');location.href=d.url}catch(e){document.getElementById('message').textContent=e.message}};
document.getElementById('reveal').onclick=async()=>{if(!confirm('Megjeleníted a titkos frissítőtokent ezen a képernyőn?'))return;try{const d=await post('/console/spotify/token');const el=document.getElementById('token');el.hidden=false;el.value=d.refresh_token;document.getElementById('message').textContent='Másold a Heroku Config Varsba, majd zárd be ezt az oldalt.'}catch(e){document.getElementById('message').textContent=e.message}};
</script></html>"""


def install_spotify(app, bot, base_url):
    client_id = os.getenv('SPOTIFY_CLIENT_ID', '').strip()
    client_secret = os.getenv('SPOTIFY_CLIENT_SECRET', '').strip()
    version = os.getenv('BOT_VERSION', 'v1.5.0')
    callback = base_url.rstrip('/') + '/console/spotify/callback'
    scope = 'user-read-currently-playing'
    lock = threading.RLock()
    state = dict(refresh=os.getenv('SPOTIFY_REFRESH_TOKEN', '').strip(), access='', expires=0, retry=0, data=None, sampled=0, known=False)
    original_presence = bot.change_presence
    fallback = dict(activity=discord.Activity(type=discord.ActivityType.watching, name=f'/ping | {version}'), status=discord.Status.online)
    last_presence = None
    live_messages = {}

    def exchange(data):
        response = requests.post('https://accounts.spotify.com/api/token', data=data, auth=(client_id, client_secret), timeout=10, allow_redirects=False)
        if response.status_code != 200:
            print(f'[Spotify] Tokenvégpont HTTP {response.status_code}', flush=True)
            raise RuntimeError('Tokenkérés elutasítva')
        result = response.json()
        if not result.get('access_token'):
            raise ValueError('Hiányzó hozzáférési token')
        return result

    def save_token(result):
        state['access'] = result['access_token']
        state['expires'] = time.monotonic() + max(1, int(result.get('expires_in', 3600)) - 60)
        refresh = result.get('refresh_token')
        if refresh and refresh != state['refresh']:
            state['refresh'] = refresh
            print('[Spotify] Új frissítőtoken: mentsd a védett Spotify-oldalról a Heroku Config Varsba.', flush=True)

    @app.get('/console/spotify')
    def spotify_page():
        return render_template_string(PAGE, csrf=session['csrf'])

    @app.post('/console/spotify/connect')
    def spotify_connect():
        if not client_id or not client_secret:
            abort(503, 'A Spotify-kapcsolat nincs beállítva.')
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        session['spotify_oauth'] = dict(state=nonce, verifier=verifier, created=time.time())
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
        return jsonify(url='https://accounts.spotify.com/authorize?' + urlencode(dict(client_id=client_id, response_type='code', redirect_uri=callback, scope=scope, state=nonce, code_challenge_method='S256', code_challenge=challenge, show_dialog='true')))

    @app.get('/console/spotify/callback')
    def spotify_callback():
        pending = session.pop('spotify_oauth', {})
        received = request.args.get('state', '')
        expected = pending.get('state', '')
        if not expected or not hmac.compare_digest(expected, received) or time.time() - pending.get('created', 0) > 600:
            abort(400, 'Sikertelen Spotify-összekapcsolás. Kezdd újra a védett Spotify-oldalon.')
        code = request.args.get('code')
        if request.args.get('error') or not code:
            abort(400, 'A Spotify-összekapcsolás nem fejeződött be.')
        try:
            result = exchange(dict(grant_type='authorization_code', code=code, redirect_uri=callback, code_verifier=pending['verifier']))
            if scope not in result.get('scope', '').split() or not result.get('refresh_token'):
                raise ValueError('Hiányzó engedély vagy frissítőtoken')
            with lock:
                save_token(result)
                state.update(retry=0, data=None, sampled=0, known=False)
            print('[Spotify] Összekapcsolás sikeres.', flush=True)
        except Exception as exc:
            print(f'[Spotify] Összekapcsolási hiba: {type(exc).__name__}', flush=True)
            abort(400, 'Sikertelen Spotify-összekapcsolás. Részletek a szervernaplóban.')
        return redirect('/console/spotify')

    @app.post('/console/spotify/token')
    def spotify_token():
        with lock:
            token = state['refresh']
        if not token:
            abort(409, 'Előbb kapcsold össze a Spotify-fiókot.')
        return jsonify(refresh_token=token)

    def poll():
        with lock:
            if not client_id or not client_secret or not state['refresh'] or time.monotonic() < state['retry']:
                return
            try:
                if not state['access'] or time.monotonic() >= state['expires']:
                    save_token(exchange(dict(grant_type='refresh_token', refresh_token=state['refresh'])))
                response = requests.get('https://api.spotify.com/v1/me/player/currently-playing', headers={'Authorization': 'Bearer ' + state['access']}, timeout=10, allow_redirects=False)
                state.update(data=None, known=False)
                if response.status_code == 200:
                    result = response.json()
                    if result.get('currently_playing_type') == 'track' and result.get('item'):
                        state['data'] = result
                    state.update(sampled=time.monotonic(), known=True)
                elif response.status_code == 204:
                    state.update(sampled=time.monotonic(), known=True)
                else:
                    print(f'[Spotify] Lejátszáslekérés HTTP {response.status_code}', flush=True)
                    if response.status_code == 401:
                        state['expires'] = 0
                    delay = max(10, float(response.headers.get('Retry-After', 30))) if response.status_code == 429 else 30
                    state['retry'] = time.monotonic() + delay
            except Exception as exc:
                state.update(data=None, known=False, retry=time.monotonic() + 60)
                print(f'[Spotify] API-hiba: {type(exc).__name__}. Újrapróbálás később.', flush=True)

    def snapshot():
        if not lock.acquire(blocking=False):
            return None, float('inf'), False
        try:
            age = time.monotonic() - state['sampled']
            known = state['known'] and age < 35
            return (state['data'] if known else None), age, known
        finally:
            lock.release()

    async def remember_presence(**kwargs):
        fallback.update(kwargs)
    bot.change_presence = remember_presence

    @tasks.loop(seconds=10)
    async def update():
        nonlocal last_presence
        if not bot.is_ready():
            return
        await asyncio.to_thread(poll)
        data, age, known = snapshot()
        payload = dict(fallback)
        if data and data.get('is_playing'):
            payload['activity'] = discord.Activity(type=discord.ActivityType.listening, name=f'{version} | Spotify | /nowplaying')
        activity = payload.get('activity')
        key = (repr(activity.to_dict()) if activity else None, str(payload.get('status')), payload.get('afk', False))
        if key != last_presence:
            try:
                await original_presence(**payload)
                last_presence = key
            except Exception as exc:
                print(f'[Spotify] Állapotfrissítési hiba: {type(exc).__name__}', flush=True)

    async def ready():
        nonlocal last_presence
        last_presence = None
        if not update.is_running():
            update.start()
        if not refresh_messages.is_running():
            refresh_messages.start()
    bot.add_listener(ready, 'on_ready')

    def track_key(data):
        item = data.get('item') or {}
        return item.get('id') or item.get('uri') or item.get('external_urls', {}).get('spotify')

    def position(data, age):
        item = data['item']
        duration = max(1, int(item.get('duration_ms') or 1))
        elapsed = max(0, int(data.get('progress_ms') or 0))
        if data.get('is_playing'):
            elapsed += max(0, int(age * 1000))
        return min(duration, elapsed), duration

    def make_embed(data, age):
        item = data['item']
        elapsed, duration = position(data, age)
        def stamp(ms):
            minutes, seconds = divmod(ms // 1000, 60)
            return f'{minutes:02d}:{seconds:02d}'
        filled = min(20, int(elapsed / duration * 20))
        url = item.get('external_urls', {}).get('spotify')
        embed = discord.Embed(title=str(item.get('name') or 'Ismeretlen zenecím')[:256], url=url, description=discord.utils.escape_markdown(', '.join(a.get('name', '') for a in item.get('artists', [])))[:1024] or 'Ismeretlen előadó', color=0x1DB954)
        album = item.get('album') or {}
        if album.get('name'):
            embed.add_field(name='Album', value=discord.utils.escape_markdown(album['name'])[:1024], inline=False)
        if album.get('images'):
            embed.set_thumbnail(url=album['images'][0]['url'])
        bar = '━' * filled + '●' + '─' * (20 - filled)
        label = 'Lejátszás · becsült pozíció' if data.get('is_playing') else 'Szüneteltetve'
        embed.add_field(name=label, value=f'{stamp(elapsed)} {bar} {stamp(duration)}', inline=False)
        embed.set_footer(text=f'{version} | Spotify · Frissítés: 5 mp · Szám végén vagy váltáskor törlődik.')
        return embed

    @tasks.loop(seconds=5)
    async def refresh_messages():
        if not bot.is_ready() or not live_messages:
            return
        data, age, known = snapshot()
        if not known:
            return
        for message_id, (message, original_track) in list(live_messages.items()):
            ended = not data or track_key(data) != original_track
            if data and not ended:
                elapsed, duration = position(data, age)
                ended = elapsed >= duration
            try:
                if ended:
                    await message.delete()
                    live_messages.pop(message_id, None)
                else:
                    await message.edit(embed=make_embed(data, age))
            except (discord.NotFound, discord.Forbidden):
                live_messages.pop(message_id, None)
                print('[Spotify] Üzenetkövetés leállt: az üzenet nem elérhető vagy nincs jogosultság.', flush=True)
            except discord.HTTPException as exc:
                print(f'[Spotify] Üzenetfrissítési HTTP-hiba: {exc.status}', flush=True)
            except Exception as exc:
                print(f'[Spotify] Üzenetfrissítési hiba: {type(exc).__name__}', flush=True)

    @bot.tree.command(name='nowplaying', description='A csatlakoztatott Spotify-fiók aktuális zenéje')
    @discord.app_commands.checks.cooldown(1, 10.0, key=lambda interaction: interaction.user.id)
    async def nowplaying(interaction: discord.Interaction):
        data, age, known = snapshot()
        if not data or not data.get('is_playing') or not track_key(data):
            await interaction.response.send_message('Nincs elérhető aktív Spotify-zene. Összekapcsolás után várj körülbelül 10 másodpercet; API-hibánál ellenőrizd a Heroku naplóját.', ephemeral=True)
            return
        if len(live_messages) >= 20:
            await interaction.response.send_message('Egyszerre legfeljebb 20 élő zenekártya követhető. Próbáld újra később.', ephemeral=True)
            return
        url = data['item'].get('external_urls', {}).get('spotify')
        view = discord.ui.View()
        if url:
            view.add_item(discord.ui.Button(label='Megnyitás Spotifyon', url=url))
        await interaction.response.send_message(embed=make_embed(data, age), view=view)
        response = await interaction.original_response()
        message = bot.get_partial_messageable(response.channel.id).get_partial_message(response.id)
        live_messages[message.id] = (message, track_key(data))

    @nowplaying.error
    async def nowplaying_error(interaction: discord.Interaction, error):
        if isinstance(error, discord.app_commands.CommandOnCooldown):
            text = f'Várj még {error.retry_after:.0f} másodpercet az újabb lekérésig.'
        else:
            print(f'[Spotify] Parancshiba: {type(error).__name__}', flush=True)
            text = 'A zenekártya nem érhető el. Részletek a szervernaplóban.'
        if not interaction.response.is_done():
            await interaction.response.send_message(text, ephemeral=True)

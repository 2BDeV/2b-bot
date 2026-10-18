import asyncio
import os
import sys
import threading

import discord
from discord.ext import tasks
import dc_bot
from waitress import serve
from web_console import install_console


VERSION = os.getenv('BOT_VERSION', 'v1.5.0')
SPOTIFY_USER_ID = int(os.getenv('SPOTIFY_USER_ID', '943865425496797225'))
bot = dc_bot.bot
bot.intents.presences = True
bot.intents.members = True
_original_presence = bot.change_presence
_fallback = {
    'activity': discord.Activity(type=discord.ActivityType.watching, name=f'/ping | {VERSION}'),
    'status': discord.Status.online,
}
_last_presence = None


def current_spotify():
    candidates = []
    for guild in bot.guilds:
        if guild.unavailable:
            continue
        member = guild.get_member(SPOTIFY_USER_ID)
        if member is None:
            continue
        for activity in member.activities:
            if isinstance(activity, discord.Spotify) and activity.end > discord.utils.utcnow():
                candidates.append(activity)
    return max(candidates, key=lambda activity: activity.start, default=None)


async def remember_presence(**kwargs):
    _fallback.update(kwargs)


bot.change_presence = remember_presence


@tasks.loop(seconds=5)
async def spotify_presence():
    global _last_presence
    if not bot.is_ready():
        return
    spotify = current_spotify()
    payload = dict(_fallback)
    if spotify is not None:
        payload['activity'] = discord.Activity(
            type=discord.ActivityType.listening,
            name=f'{VERSION} | Spotify',
        )
    activity = payload.get('activity')
    key = (repr(activity.to_dict()) if activity else None,
           str(payload.get('status')), payload.get('afk', False))
    if key == _last_presence:
        return
    try:
        await _original_presence(**payload)
    except Exception as exc:
        print(f'[Spotify] Állapotfrissítési hiba: {type(exc).__name__}', flush=True)
        return
    _last_presence = key


async def spotify_ready():
    global _last_presence
    _last_presence = None
    if not spotify_presence.is_running():
        spotify_presence.start()
    print(f'[Spotify] Figyelt felhasználó: {SPOTIFY_USER_ID}', flush=True)


bot.add_listener(spotify_ready, 'on_ready')


def format_time(seconds):
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    return f'{minutes:02d}:{seconds:02d}'


@bot.tree.command(name='nowplaying', description='A figyelt felhasználó aktuális Spotify-zenéje')
async def nowplaying(interaction: discord.Interaction):
    spotify = current_spotify()
    if spotify is None:
        await interaction.response.send_message(
            'Most nem látok aktív Spotify-hallgatást a figyelt felhasználónál. '
            'Közös szerver, megosztott Spotify-aktivitás és engedélyezett presence szükséges.',
            ephemeral=True,
        )
        return
    duration = max(1, int(spotify.duration.total_seconds()))
    elapsed = max(0, min(duration, int((discord.utils.utcnow() - spotify.start).total_seconds())))
    filled = min(20, int(elapsed / duration * 20))
    progress = '━' * filled + '●' + '─' * (20 - filled)
    artists = discord.utils.escape_markdown(', '.join(spotify.artists))[:1024]
    embed = discord.Embed(
        title=spotify.title[:256],
        url=spotify.track_url,
        description=artists,
        color=0x1DB954,
    )
    if spotify.album:
        embed.add_field(name='Album', value=discord.utils.escape_markdown(spotify.album)[:1024], inline=False)
    if spotify.album_cover_url:
        embed.set_thumbnail(url=spotify.album_cover_url)
    embed.add_field(
        name='Lejátszási állapot a lekérés pillanatában',
        value=f'{format_time(elapsed)} {progress} {format_time(duration)}',
        inline=False,
    )
    embed.set_footer(text=f'{VERSION} | Spotify · Az idővonal nem frissül automatikusan.')
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label='Megnyitás Spotifyon', url=spotify.track_url))
    await interaction.response.send_message(embed=embed, view=view)


def control(command):
    if command in ('reload', 'reset'):
        async def sync():
            if command == 'reset':
                bot.tree.clear_commands(guild=None)
            result = await bot.tree.sync()
            print(f'Szinkronizált parancsok: {len(result)}', flush=True)
        asyncio.run_coroutine_threadsafe(sync(), bot.loop).result(timeout=30)
    elif command == 'restart':
        def restart():
            import time
            time.sleep(1)
            os.execv(sys.executable, [sys.executable, 'console_launcher.py'])
        threading.Thread(target=restart, daemon=True).start()
    elif command == 'stop':
        asyncio.run_coroutine_threadsafe(bot.close(), bot.loop).result(timeout=30)


if __name__ == '__main__':
    install_console(dc_bot.app, bot, dc_bot.BASE_URL, control)
    threading.Thread(target=lambda: serve(dc_bot.app, host='0.0.0.0', port=int(os.getenv('PORT', '5000')), threads=8), daemon=True).start()
    bot.run(dc_bot.TOKEN)

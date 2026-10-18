import asyncio
import os
import sys
import threading

import dc_bot
from waitress import serve
from web_console import install_console
from spotify_api import install_spotify


def control(command):
    if command in ('reload', 'reset'):
        async def sync():
            if command == 'reset':
                dc_bot.bot.tree.clear_commands(guild=None)
            result = await dc_bot.bot.tree.sync()
            print(f'Szinkronizált parancsok: {len(result)}', flush=True)
        asyncio.run_coroutine_threadsafe(sync(), dc_bot.bot.loop).result(timeout=30)
    elif command == 'restart':
        def restart():
            import time
            time.sleep(1)
            os.execv(sys.executable, [sys.executable, 'console_launcher.py'])
        threading.Thread(target=restart, daemon=True).start()
    elif command == 'stop':
        asyncio.run_coroutine_threadsafe(dc_bot.bot.close(), dc_bot.bot.loop).result(timeout=30)


if __name__ == '__main__':
    install_console(dc_bot.app, dc_bot.bot, dc_bot.BASE_URL, control)
    install_spotify(dc_bot.app, dc_bot.bot, dc_bot.BASE_URL)
    threading.Thread(target=lambda: serve(dc_bot.app, host='0.0.0.0', port=int(os.getenv('PORT', '5000')), threads=8), daemon=True).start()
    dc_bot.bot.run(dc_bot.TOKEN)

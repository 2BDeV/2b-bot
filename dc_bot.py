import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import time
import os
from flask import Flask
import threading
from dotenv import load_dotenv
import sys
import signal

# ---- Load environment variables ----
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
GUILD_ID = int(os.getenv("GUILD_ID"))
URL = os.getenv("URL")
BACKUP_URL = os.getenv("BACKUP_URL")  # ÚJ!
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "5"))

# ---- Ensure required variables exist ----
required_vars = [TOKEN, CHANNEL_ID, GUILD_ID, URL, BACKUP_URL]
if not all(required_vars):
    raise ValueError("❌ Missing required environment variables in .env")

# ---- Flask app ----
app = Flask(__name__)
def run_flask():
    app.run(host="0.0.0.0", port=5000, debug=False)

# ---- Discord bot ----
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

last_status = {URL: None, BACKUP_URL: None}  # több URL követése

async def check_website(target_url: str):
    try:
        async with aiohttp.ClientSession() as session:
            start_time = time.perf_counter()
            async with session.get(target_url, timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as resp:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                return 200 <= resp.status < 300, elapsed_ms
    except Exception:
        return False, None

# ---- Slash commands ----
@bot.tree.command(name="ping", description="Check website status")
async def ping_command(interaction: discord.Interaction):
    await interaction.response.defer()

    statuses = []
    for site in [URL, BACKUP_URL]:
        is_accessible, response_time = await check_website(site)
        desc = f"**URL:** {site}\n**Response Time:** `{response_time:.2f} ms`" if response_time else f"**URL:** {site}\nResponse time not measured"
        statuses.append((site, is_accessible, desc))

    embed = discord.Embed(title="🌐 Website Status", color=0x00ffff)
    for site, ok, desc in statuses:
        embed.add_field(
            name=("✅ ONLINE" if ok else "❌ OFFLINE") + f" — {site}",
            value=desc,
            inline=False
        )
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="test", description="Test command to check slash commands")
async def test_command(interaction: discord.Interaction):
    await interaction.response.send_message("✅ Slash commands are working!")

@bot.tree.command(name="listcommands", description="List all registered global slash commands")
async def list_commands(interaction: discord.Interaction):
    await interaction.response.defer()
    commands_list = bot.tree.get_commands()
    if not commands_list:
        await interaction.followup.send("⚠️ No slash commands found.")
        return
    desc = "\n".join(f"• {cmd.name} - {cmd.description}" for cmd in commands_list)
    embed = discord.Embed(title="Global Slash Commands", description=desc, color=0x00ffff)
    await interaction.followup.send(embed=embed)

# ---- Monitoring ----
@tasks.loop(seconds=CHECK_INTERVAL)
async def monitor_websites():
    global last_status
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print("⚠️ Channel not found, check CHANNEL_ID")
        return

    for site in [URL, BACKUP_URL]:
        is_accessible, response_time = await check_website(site)

        # presence csak a fő URL-re menjen
        if site == URL:
            activity = discord.Activity(
                type=discord.ActivityType.watching if is_accessible else discord.ActivityType.playing,
                name=f"/ping | v1.4.5" if is_accessible else f"Website offline: {URL}"
            )
            status = discord.Status.online if is_accessible else discord.Status.dnd
            await bot.change_presence(activity=activity, status=status)

        # Ha első futás → csak rögzítjük
        if last_status[site] is None:
            last_status[site] = is_accessible
            print(f"Initial status for {site}: {'ONLINE' if is_accessible else 'OFFLINE'}")
            continue

        # Ha változott az állapot → küldünk értesítést
        if is_accessible != last_status[site]:
            embed = discord.Embed(
                title="🟢 Back Online!" if is_accessible else "🔴 Offline!",
                description=f"**{site}** is now {'accessible' if is_accessible else 'not accessible'}",
                color=0x00ff00 if is_accessible else 0xff0000,
                timestamp=discord.utils.utcnow()
            )
            if response_time and is_accessible:
                embed.add_field(name="Response Time", value=f"{response_time:.2f} ms", inline=True)
            await channel.send(embed=embed)
            last_status[site] = is_accessible

# ---- Bot events ----
@bot.event
async def on_ready():
    print(f"✅ Bot logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"🔄 Globally synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"❌ Sync error: {e}")

    if not monitor_websites.is_running():
        monitor_websites.start()

# ---- Console handler ----
def console_handler():
    while True:
        cmd = input("📥 Command (reload / reset / restart / stop): ").strip().lower()

        if cmd == "reload":
            async def reload_commands():
                try:
                    synced = await bot.tree.sync()
                    print(f"🔄 Reloaded {len(synced)} command(s).")
                except Exception as e:
                    print(f"❌ Reload failed: {e}")
            asyncio.run_coroutine_threadsafe(reload_commands(), bot.loop)

        elif cmd == "reset":
            async def full_reset_commands():
                current_commands = bot.tree.get_commands()
                for cmd in current_commands:
                    bot.tree.remove_command(cmd.name)
                    print(f"🗑️ Removed command: {cmd.name}")
                synced = await bot.tree.sync()
                print(f"🔄 Globally synced {len(synced)} slash command(s)")
            print("🔄 Resetting all global commands...")
            future = asyncio.run_coroutine_threadsafe(full_reset_commands(), bot.loop)
            try:
                future.result(timeout=30)
            except Exception as e:
                print(f"❌ Reset failed: {e}")
            else:
                print("✅ Reset completed.")

        elif cmd == "restart":
            print("🔄 Restarting bot...")
            os.execv(sys.executable, ["python"] + sys.argv)

        elif cmd == "stop":
            print("🛑 Shutting down bot...")
            os.kill(os.getpid(), signal.SIGINT)

        else:
            print("⚠️ Unknown command! Available: reload, reset, restart, stop")

# ---- Main ----
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    console_thread = threading.Thread(target=console_handler, daemon=True)
    console_thread.start()

    bot.run(TOKEN)

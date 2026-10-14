import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiohttp
import asyncio
import time
import os
from flask import Flask, request, jsonify, render_template_string
import threading
from dotenv import load_dotenv
import sys
import signal
import secrets
import random
import chess

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
GUILD_ID = int(os.getenv("GUILD_ID"))
URL = os.getenv("URL")
BACKUP_URL = os.getenv("BACKUP_URL")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "5"))
BASE_URL = os.getenv("BASE_URL", "2bbot-heroku.2bdevon.top").rstrip("/")

if not all([TOKEN, CHANNEL_ID, GUILD_ID, URL, BACKUP_URL]):
    raise ValueError("Missing required environment variables")

app = Flask(__name__)

def run_flask():
    app.run(host="0.0.0.0", port=5000, debug=False)

games = {}
games_lock = threading.Lock()

PIECE_VALUES = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330, chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}

def evaluate(board):
    score = 0
    for pt, val in PIECE_VALUES.items():
        score += len(board.pieces(pt, chess.WHITE)) * val
        score -= len(board.pieces(pt, chess.BLACK)) * val
    return score if board.turn == chess.WHITE else -score

def negamax(board, depth, alpha, beta):
    if board.is_checkmate():
        return -99999 - depth
    if board.is_game_over():
        return 0
    if depth == 0:
        return evaluate(board)
    best = -10**9
    for move in board.legal_moves:
        board.push(move)
        score = -negamax(board, depth - 1, -beta, -alpha)
        board.pop()
        best = max(best, score)
        alpha = max(alpha, score)
        if alpha >= beta:
            break
    return best

def best_bot_move(board, depth=3):
    best_move, best_score, alpha = None, -10**9, -10**9
    moves = list(board.legal_moves)
    random.shuffle(moves)
    for move in moves:
        board.push(move)
        score = -negamax(board, depth - 1, -10**9, -alpha)
        board.pop()
        if score > best_score:
            best_score, best_move = score, move
        alpha = max(alpha, score)
    return best_move

def create_game(mode, creator_id, creator_name, opp_id=None, opp_name=None, creator_color="random"):
    if creator_color == "random":
        creator_color = random.choice(["white", "black"])
    other = "black" if creator_color == "white" else "white"
    gid = secrets.token_urlsafe(6)
    players = {
        creator_color: {"token": secrets.token_urlsafe(10), "name": creator_name, "id": creator_id, "joined": False}
    }
    if mode == "bot":
        players[other] = {"token": None, "name": "Bot", "id": None, "joined": True}
    else:
        players[other] = {"token": secrets.token_urlsafe(10), "name": opp_name, "id": opp_id, "joined": False}
    game = {"id": gid, "mode": mode, "board": chess.Board(), "players": players, "resigned": None, "last_move": None}
    with games_lock:
        games[gid] = game
        if mode == "bot" and other == "white":
            move = best_bot_move(game["board"])
            game["last_move"] = move.uci()
            game["board"].push(move)
    return game

def color_of(game, token):
    for color, player in game["players"].items():
        if player["token"] and player["token"] == token:
            return color
    return None

def game_link(game, color):
    return f"{BASE_URL}/chess/{game['id']}?t={game['players'][color]['token']}"

def build_state(game, my_color):
    board = game["board"]
    started = all(player["joined"] for player in game["players"].values())
    turn = "white" if board.turn == chess.WHITE else "black"
    over, result, text = False, None, ""
    if game["resigned"]:
        over = True
        winner = "black" if game["resigned"] == "white" else "white"
        result = "1-0" if winner == "white" else "0-1"
        text = f"{game['players'][game['resigned']]['name']} feladta. Nyert: {game['players'][winner]['name']}."
    elif board.is_game_over():
        over = True
        result = board.result()
        if board.is_checkmate():
            winner = "black" if board.turn == chess.WHITE else "white"
            text = f"Matt! Nyert: {game['players'][winner]['name']}."
        else:
            text = "Döntetlen."
    elif not started:
        waiting = [player["name"] for player in game["players"].values() if not player["joined"]]
        text = "Várakozás erre a játékosra: " + ", ".join(waiting)
    else:
        text = f"{game['players'][turn]['name']} jön ({'világos' if turn == 'white' else 'sötét'})."
        if board.is_check():
            text += " Sakk!"
    return {"fen": board.fen(), "turn": turn, "color": my_color, "started": started, "over": over, "result": result, "text": text, "last_move": game["last_move"], "white": game["players"]["white"]["name"], "black": game["players"]["black"]["name"]}

@app.route("/chess/<gid>")
def chess_page(gid):
    if gid not in games:
        return "Ez a játék nem létezik.", 404
    return render_template_string(CHESS_HTML, gid=gid)

@app.route("/api/chess/<gid>/state")
def chess_state(gid):
    game = games.get(gid)
    if not game:
        return jsonify(error="not found"), 404
    with games_lock:
        color = color_of(game, request.args.get("t"))
        if color is None:
            return jsonify(error="Érvénytelen link."), 403
        game["players"][color]["joined"] = True
        return jsonify(build_state(game, color))

@app.route("/api/chess/<gid>/move", methods=["POST"])
def chess_move(gid):
    game = games.get(gid)
    if not game:
        return jsonify(error="not found"), 404
    data = request.get_json(force=True)
    with games_lock:
        color = color_of(game, data.get("t"))
        if color is None:
            return jsonify(error="Érvénytelen link."), 403
        state = build_state(game, color)
        if not state["started"] or state["over"]:
            return jsonify(error="A játék nem aktív."), 400
        if state["turn"] != color:
            return jsonify(error="Nem te jössz."), 400
        board = game["board"]
        try:
            move = chess.Move.from_uci(data["from"] + data["to"])
            piece = board.piece_at(move.from_square)
            if piece and piece.piece_type == chess.PAWN and chess.square_rank(move.to_square) in (0, 7):
                move = chess.Move(move.from_square, move.to_square, promotion=chess.QUEEN)
        except Exception:
            return jsonify(error="Hibás lépés."), 400
        if move not in board.legal_moves:
            return jsonify(error="Szabálytalan lépés."), 400
        board.push(move)
        game["last_move"] = move.uci()
        if game["mode"] == "bot" and not board.is_game_over():
            bot_move = best_bot_move(board)
            board.push(bot_move)
            game["last_move"] = bot_move.uci()
        return jsonify(build_state(game, color))

@app.route("/api/chess/<gid>/resign", methods=["POST"])
def chess_resign(gid):
    game = games.get(gid)
    if not game:
        return jsonify(error="not found"), 404
    data = request.get_json(force=True)
    with games_lock:
        color = color_of(game, data.get("t"))
        if color is None:
            return jsonify(error="Érvénytelen link."), 403
        game["resigned"] = color
        return jsonify(build_state(game, color))

CHESS_HTML = '''<!DOCTYPE html><html lang="hu"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sakk</title><link rel="stylesheet" href="https://unpkg.com/@chrisoakman/chessboardjs@1.0.0/dist/chessboard-1.0.0.min.css"><style>body{background:#1e1f22;color:#eee;font-family:sans-serif;text-align:center;margin:0;padding:20px}#board{width:min(90vw,520px);margin:15px auto}#status{font-size:1.2em;margin:10px}button{background:#ed4245;color:#fff;border:0;padding:10px 20px;border-radius:6px;cursor:pointer}.highlight{box-shadow:inset 0 0 3px 3px #f5d442}</style></head><body><h2>♟️ Sakk</h2><div id="players"></div><div id="status">Betöltés...</div><div id="board"></div><button onclick="resign()">Feladás</button><script src="https://code.jquery.com/jquery-3.7.1.min.js"></script><script src="https://unpkg.com/@chrisoakman/chessboardjs@1.0.0/dist/chessboard-1.0.0.min.js"></script><script>const gid="{{ gid }}";const token=new URLSearchParams(location.search).get("t");let board=null,state=null,lastFen=null;function canMove(){return state&&state.started&&!state.over&&state.turn===state.color}function onDragStart(source,piece){if(!canMove())return false;if(state.color==="white"&&piece.startsWith("b"))return false;if(state.color==="black"&&piece.startsWith("w"))return false}async function onDrop(source,target){if(source===target)return;const r=await fetch(`/api/chess/${gid}/move`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({t:token,from:source,to:target})});const data=await r.json();if(data.error){board.position(lastFen);return}apply(data)}function highlight(lm){$("#board .square-55d63").removeClass("highlight");if(!lm)return;$("#board .square-"+lm.slice(0,2)).addClass("highlight");$("#board .square-"+lm.slice(2,4)).addClass("highlight")}function apply(s){state=s;if(!board){board=Chessboard("board",{draggable:true,position:s.fen,orientation:s.color,pieceTheme:"https://chessboardjs.com/img/chesspieces/wikipedia/{piece}.png",onDragStart,onDrop,onSnapEnd:()=>board.position(lastFen)})}if(s.fen!==lastFen){board.position(s.fen);lastFen=s.fen}highlight(s.last_move);document.getElementById("status").innerText=s.text;document.getElementById("players").innerText=`⚪ ${s.white} vs ⚫ ${s.black} | Te: ${s.color==="white"?"világos":"sötét"}`}async function poll(){try{const r=await fetch(`/api/chess/${gid}/state?t=${token}`);const data=await r.json();if(data.error){document.getElementById("status").innerText=data.error;return}apply(data)}catch(e){}}async function resign(){if(!confirm("Biztos feladod?"))return;const r=await fetch(`/api/chess/${gid}/resign`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({t:token})});apply(await r.json())}poll();setInterval(poll,1000)</script></body></html>'''

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
last_status = {URL: None, BACKUP_URL: None}

async def check_website(target_url):
    try:
        async with aiohttp.ClientSession() as session:
            start = time.perf_counter()
            async with session.get(target_url, timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as resp:
                elapsed = (time.perf_counter() - start) * 1000
                return 200 <= resp.status < 300, elapsed
    except Exception:
        return False, None

class JoinView(discord.ui.View):
    def __init__(self, game, opponent_id):
        super().__init__(timeout=3600)
        self.game = game
        self.opponent_id = opponent_id

    @discord.ui.button(label="Csatlakozás a játékhoz", style=discord.ButtonStyle.success, emoji="♟️")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message("Ez a meghívás nem neked szól.", ephemeral=True)
            return
        color = next(c for c, p in self.game["players"].items() if p["id"] == interaction.user.id)
        await interaction.response.send_message(f"Itt a linked: {game_link(self.game, color)}\nTe {'világos' if color == 'white' else 'sötét'} vagy.", ephemeral=True)

@bot.tree.command(name="chess", description="Sakkjáték indítása böngészőben")
@app_commands.describe(mode="Játékmód", opponent="Ellenfél (1v1 módnál)", color="Szín")
@app_commands.choices(mode=[app_commands.Choice(name="Ember vs Bot", value="bot"), app_commands.Choice(name="Ember vs Ember (1v1)", value="pvp")], color=[app_commands.Choice(name="Világos", value="white"), app_commands.Choice(name="Sötét", value="black"), app_commands.Choice(name="Véletlen", value="random")])
async def chess_command(interaction: discord.Interaction, mode: app_commands.Choice[str], opponent: discord.Member = None, color: app_commands.Choice[str] = None):
    color_value = color.value if color else "random"
    if mode.value == "bot":
        game = await asyncio.to_thread(create_game, "bot", interaction.user.id, interaction.user.display_name, None, None, color_value)
        my_color = next(c for c, p in game["players"].items() if p["id"] == interaction.user.id)
        await interaction.response.send_message(f"Ember vs Bot játék kész!\nTe {'világos' if my_color == 'white' else 'sötét'} vagy.\n{game_link(game, my_color)}", ephemeral=True)
        return
    if opponent is None:
        await interaction.response.send_message("1v1 módnál add meg az ellenfelet!", ephemeral=True)
        return
    if opponent.bot or opponent.id == interaction.user.id:
        await interaction.response.send_message("Válassz egy másik, nem bot felhasználót!", ephemeral=True)
        return
    game = create_game("pvp", interaction.user.id, interaction.user.display_name, opponent.id, opponent.display_name, color_value)
    my_color = next(c for c, p in game["players"].items() if p["id"] == interaction.user.id)
    await interaction.response.send_message(f"{opponent.mention}, {interaction.user.display_name} sakkozni hív! A játék akkor indul, ha mindketten megnyitjátok a linketeket.", view=JoinView(game, opponent.id))
    await interaction.followup.send(f"Te {'világos' if my_color == 'white' else 'sötét'} vagy.\n{game_link(game, my_color)}", ephemeral=True)

@bot.tree.command(name="ping", description="Check website status")
async def ping_command(interaction: discord.Interaction):
    await interaction.response.defer()
    statuses = []
    for site in [URL, BACKUP_URL]:
        ok, response_time = await check_website(site)
        desc = f"URL: {site}\nResponse Time: {response_time:.2f} ms" if response_time else f"URL: {site}\nResponse time not measured"
        statuses.append((site, ok, desc))
    embed = discord.Embed(title="Website Status", color=0x00ffff)
    for site, ok, desc in statuses:
        embed.add_field(name=("ONLINE" if ok else "OFFLINE") + f" — {site}", value=desc, inline=False)
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="test", description="Test command")
async def test_command(interaction: discord.Interaction):
    await interaction.response.send_message("Slash commands are working!")

@bot.tree.command(name="listcommands", description="List slash commands")
async def list_commands(interaction: discord.Interaction):
    await interaction.response.defer()
    cmds = bot.tree.get_commands()
    if not cmds:
        await interaction.followup.send("No slash commands found.")
        return
    desc = "\n".join(f"{cmd.name} - {cmd.description}" for cmd in cmds)
    await interaction.followup.send(embed=discord.Embed(title="Global Slash Commands", description=desc, color=0x00ffff))

@tasks.loop(seconds=CHECK_INTERVAL)
async def monitor_websites():
    global last_status
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        return
    for site in [URL, BACKUP_URL]:
        ok, response_time = await check_website(site)
        if site == URL:
            activity = discord.Activity(type=discord.ActivityType.watching if ok else discord.ActivityType.playing, name="/ping | v1.5.0" if ok else f"Website offline: {URL}")
            await bot.change_presence(activity=activity, status=discord.Status.online if ok else discord.Status.dnd)
        if last_status[site] is None:
            last_status[site] = ok
            continue
        if ok != last_status[site]:
            embed = discord.Embed(title="Back Online!" if ok else "Offline!", description=f"{site} is now {'accessible' if ok else 'not accessible'}", color=0x00ff00 if ok else 0xff0000, timestamp=discord.utils.utcnow())
            if response_time and ok:
                embed.add_field(name="Response Time", value=f"{response_time:.2f} ms", inline=True)
            await channel.send(embed=embed)
            last_status[site] = ok

@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"Sync error: {e}")
    if not monitor_websites.is_running():
        monitor_websites.start()

def console_handler():
    while True:
        cmd = input("Command (reload / reset / restart / stop): ").strip().lower()
        if cmd == "reload":
            async def reload_commands():
                try:
                    synced = await bot.tree.sync()
                    print(f"Reloaded {len(synced)} commands.")
                except Exception as e:
                    print(f"Reload failed: {e}")
            asyncio.run_coroutine_threadsafe(reload_commands(), bot.loop)
        elif cmd == "reset":
            async def reset_commands():
                for cmd in bot.tree.get_commands():
                    bot.tree.remove_command(cmd.name)
                synced = await bot.tree.sync()
                print(f"Synced {len(synced)} commands")
            asyncio.run_coroutine_threadsafe(reset_commands(), bot.loop)
        elif cmd == "restart":
            os.execv(sys.executable, ["python"] + sys.argv)
        elif cmd == "stop":
            os.kill(os.getpid(), signal.SIGINT)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=console_handler, daemon=True).start()
    bot.run(TOKEN)

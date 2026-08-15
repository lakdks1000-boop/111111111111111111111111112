import discord
from discord.ext import commands, tasks
from discord.ui import Button, View, Modal, TextInput
import sqlite3
from flask import Flask, render_template, request, redirect, session, url_for, jsonify
import threading
import os
import time
import requests
import asyncio
from waitress import serve

# ==========================================
# 1. 환경 변수 및 설정
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
PORT = int(os.getenv("PORT", 5000))
FLASK_SECRET = os.getenv("FLASK_SECRET", os.urandom(24).hex())

BACKUP_CHANNEL_ID = 1538060612754735224
DB_FILE = "partner.db"

db_lock = threading.Lock()

# ==========================================
# 2. 데이터베이스 초기화 및 접근 유틸리티
# ==========================================
def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS server_settings 
                     (guild_id TEXT PRIMARY KEY, title TEXT, description TEXT, button_text TEXT, 
                      webhook_message TEXT, interval_minutes INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS partners 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT, webhook_url TEXT, last_sent REAL)""")
        conn.commit()
        conn.close()

init_db()

def execute_db(query, params=()):
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        c = conn.cursor()
        c.execute(query, params)
        conn.commit()
        conn.close()

def fetch_db(query, params=(), fetchall=False):
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        c = conn.cursor()
        c.execute(query, params)
        result = c.fetchall() if fetchall else c.fetchone()
        conn.close()
        return result

# ==========================================
# 3. 디스코드 봇 로직
# ==========================================
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

async def trigger_backup():
    await bot.wait_until_ready()
    channel = bot.get_channel(BACKUP_CHANNEL_ID)
    if channel:
        try:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            await channel.send(
                content=f"🔄 **DB 자동 백업 업데이트** ({timestamp})\n설정 변경 또는 새 파트너가 추가되어 데이터를 백업합니다.", 
                file=discord.File(DB_FILE)
            )
        except Exception as e:
            print(f"[Backup Error] {e}")

class WebhookModal(Modal, title="파트너 웹훅 입력"):
    webhook_url = TextInput(
        label="웹훅 URL",
        placeholder="https://discord.com/api/webhooks/...",
        style=discord.TextStyle.short,
        required=True
    )

    def __init__(self, guild_id):
        super().__init__()
        self.guild_id = str(guild_id)

    async def on_submit(self, interaction: discord.Interaction):
        url = self.webhook_url.value.strip()
        if not url.startswith("https://discord.com/api/webhooks/"):
            return await interaction.response.send_message("❌ 올바른 디스코드 웹훅 URL을 입력해주세요.", ephemeral=True)

        execute_db("INSERT INTO partners (guild_id, webhook_url, last_sent) VALUES (?, ?, ?)",
                   (self.guild_id, url, 0))
        
        await interaction.response.send_message("✅ 파트너 웹훅이 성공적으로 등록되었습니다!", ephemeral=True)
        bot.loop.create_task(trigger_backup())

class PersistentPartnerView(View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(custom_id="partner_create_btn", style=discord.ButtonStyle.primary)
    async def create_partner_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(WebhookModal(interaction.guild_id))

@bot.tree.command(name="임베드", description="파트너 생성 패널을 출력합니다.")
@discord.app_commands.default_permissions(administrator=True)
async def setup_embed(interaction: discord.Interaction):
    row = fetch_db("SELECT title, description, button_text FROM server_settings WHERE guild_id = ?", (str(interaction.guild_id),))
    
    title = row[0] if row else "최상급 부스트 | 파트너 생성 패널"
    desc = row[1] if row else "버튼을 눌러 파트너 채널 생성을 시작하세요.\n웹훅만 입력해주세요."
    btn_text = row[2] if row else "생성하기"

    embed = discord.Embed(title=title, description=desc, color=0x2b2d31)
    embed.set_footer(text=f"CopyRight 2026. {interaction.guild.name}. All rights reserved.")
    
    view = PersistentPartnerView()
    view.children[0].label = btn_text

    await interaction.response.send_message(embed=embed, view=view)

@tasks.loop(minutes=1)
async def webhook_scheduler():
    current_time = time.time()
    settings_data = fetch_db("SELECT guild_id, webhook_message, interval_minutes FROM server_settings", fetchall=True)
    settings = {row[0]: {"msg": row[1], "interval": (row[2] or 60) * 60} for row in settings_data}
    partners = fetch_db("SELECT id, guild_id, webhook_url, last_sent FROM partners", fetchall=True)

    for p_id, g_id, url, last_sent in partners:
        if g_id in settings:
            interval = settings[g_id]["interval"]
            msg = settings[g_id]["msg"]
            if current_time - last_sent >= interval:
                try:
                    res = requests.post(url, json={"content": msg}, timeout=5)
                    if res.status_code in [200, 204]:
                        execute_db("UPDATE partners SET last_sent = ? WHERE id = ?", (current_time, p_id))
                except Exception as e:
                    print(f"Webhook Failed for {url}: {e}")

@bot.event
async def on_ready():
    bot.add_view(PersistentPartnerView())
    await bot.tree.sync()
    if not webhook_scheduler.is_running():
        webhook_scheduler.start()
    print(f"✅ Bot Ready: {bot.user}")

# ==========================================
# 4. Flask 웹 대시보드 로직
# ==========================================
app = Flask(__name__, template_folder=".")
app.secret_key = FLASK_SECRET

def get_admin_guilds(token):
    headers = {"Authorization": f"Bearer {token}"}
    res = requests.get("https://discord.com/api/users/@me/guilds", headers=headers)
    if res.status_code != 200:
        return []
    guilds = res.json()
    return [g for g in guilds if (int(g.get("permissions", 0)) & 0x8) == 0x8]

@app.route("/")
def index():
    if "token" in session:
        return redirect(url_for("dashboard"))
    oauth_url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds"
    return render_template("index.html", logged_in=False, oauth_url=oauth_url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return redirect(url_for("index"))
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI
    }
    r = requests.post("https://discord.com/api/oauth2/token", data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    res_json = r.json()
    if "access_token" in res_json:
        session["token"] = res_json["access_token"]
    return redirect(url_for("dashboard"))

@app.route("/dashboard")
def dashboard():
    if "token" not in session:
        return redirect(url_for("index"))
    return render_template("index.html", logged_in=True)

@app.route("/api/guilds")
def api_guilds():
    if "token" not in session:
        return jsonify([])
    guilds = get_admin_guilds(session["token"])
    return jsonify([{"id": g["id"], "name": g["name"], "icon": g.get("icon")} for g in guilds])

@app.route("/api/settings/<guild_id>", methods=["GET", "POST"])
def api_settings(guild_id):
    if "token" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    admin_guilds = [g["id"] for g in get_admin_guilds(session["token"])]
    if guild_id not in admin_guilds:
        return jsonify({"error": "Forbidden"}), 403

    if request.method == "POST":
        data = request.json
        execute_db("""INSERT OR REPLACE INTO server_settings 
                     (guild_id, title, description, button_text, webhook_message, interval_minutes) 
                     VALUES (?, ?, ?, ?, ?, ?)""", 
                  (guild_id, data["title"], data["description"], data["button_text"], data["webhook_message"], data["interval_minutes"]))
        
        bot.loop.create_task(trigger_backup())
        return jsonify({"status": "success"})
    else:
        row = fetch_db("SELECT title, description, button_text, webhook_message, interval_minutes FROM server_settings WHERE guild_id = ?", (guild_id,))
        if row:
            return jsonify({"title": row[0], "description": row[1], "button_text": row[2], "webhook_message": row[3], "interval_minutes": row[4]})
        return jsonify({"title": "최상급 부스트 | 파트너 생성 패널", 
                        "description": "버튼을 눌러 파트너 채널 생성을 시작하세요.\n웹훅만 입력해주세요.", 
                        "button_text": "생성하기", 
                        "webhook_message": "이곳에 파트너에게 보낼 메시지를 입력하세요.", 
                        "interval_minutes": 60})

# ==========================================
# 5. 서버 실행 진입점
# ==========================================
def run_flask():
    serve(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    if not BOT_TOKEN or not CLIENT_SECRET:
        print("❌ 환경 변수(BOT_TOKEN, CLIENT_ID, CLIENT_SECRET)가 설정되지 않았습니다.")
    else:
        threading.Thread(target=run_flask, daemon=True).start()
        bot.run(BOT_TOKEN)

import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
from flask import Flask, render_template, request, redirect, session, url_for, jsonify
import threading
import os
import time
import requests
import asyncio

# === 설정 변수 (환경 변수에서 가져오기) ===
BOT_TOKEN = os.environ.get('BOT_TOKEN', 'MTUzODA2MjQ1Mjc0OTYzMTYwOQ.GDBAJ_.WOJ39dXALnE8dD98q341jekVMYJuV1a45_s05s')
CLIENT_ID = os.environ.get('CLIENT_ID', '1538062452749631609')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET', 'asA-YyDw8HRTL4lZK-H5zlyD8Xa7qtFF')
REDIRECT_URI = os.environ.get('REDIRECT_URI', 'http://localhost:5000/callback')
BACKUP_CHANNEL_ID = 1538060612754735224
DB_FILE = 'partner.db'

# === DB 초기화 ===
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # 서버별 임베드 설정 테이블
    c.execute('''CREATE TABLE IF NOT EXISTS server_settings 
                 (guild_id TEXT PRIMARY KEY, title TEXT, description TEXT, button_text TEXT, 
                  webhook_message TEXT, interval_minutes INTEGER)''')
    # 파트너(웹훅) 목록 테이블
    c.execute('''CREATE TABLE IF NOT EXISTS partners 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT, webhook_url TEXT, last_sent REAL)''')
    conn.commit()
    conn.close()

init_db()

# === 디스코드 봇 설정 ===
intents = discord.Intents.default()
bot = commands.Bot(command_prefix='!', intents=intents)

async def backup_database():
    """DB 변경 시 지정된 채널로 파일 업로드"""
    await bot.wait_until_ready()
    channel = bot.get_channel(BACKUP_CHANNEL_ID)
    if channel:
        try:
            await channel.send(content="데이터베이스 백업 업데이트", file=discord.File(DB_FILE))
        except Exception as e:
            print(f"DB 백업 실패: {e}")

# --- UI 컴포넌트 (모달 & 버튼) ---
class WebhookModal(discord.ui.Modal, title='웹훅 입력'):
    webhook_url = discord.ui.TextInput(
        label='웹훅 URL',
        placeholder='https://discord.com/api/webhooks/...',
        style=discord.TextStyle.short,
        required=True
    )

    def __init__(self, guild_id):
        super().__init__()
        self.guild_id = str(guild_id)

    async def on_submit(self, interaction: discord.Interaction):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT INTO partners (guild_id, webhook_url, last_sent) VALUES (?, ?, ?)",
                  (self.guild_id, self.webhook_url.value, 0))
        conn.commit()
        conn.close()
        
        await interaction.response.send_message("파트너 웹훅이 성공적으로 등록되었습니다.", ephemeral=True)
        # DB 수정 발생 -> 백업
        bot.loop.create_task(backup_database())

class PartnerView(discord.ui.View):
    def __init__(self, button_text="생성하기"):
        super().__init__(timeout=None)
        self.add_item(PartnerButton(button_text))

class PartnerButton(discord.ui.Button):
    def __init__(self, label):
        super().__init__(label=label, style=discord.ButtonStyle.primary, custom_id="partner_create_btn")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(WebhookModal(interaction.guild_id))

@bot.tree.command(name="임베드", description="파트너 생성 패널을 출력합니다.")
async def setup_embed(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT title, description, button_text FROM server_settings WHERE guild_id = ?", (str(interaction.guild_id),))
    row = c.fetchone()
    conn.close()

    title = row[0] if row else "최상급 부스트 | 파트너 생성 패널"
    desc = row[1] if row else "버튼을 눌러 파트너 채널 생성을 시작하세요."
    btn_text = row[2] if row else "생성하기"

    embed = discord.Embed(title=title, description=desc, color=0x2b2d31)
    embed.set_footer(text=f"CopyRight 2026. {interaction.guild.name}. All rights reserved.")
    
    view = PartnerView(button_text=btn_text)
    await interaction.response.send_message(embed=embed, view=view)

@tasks.loop(minutes=1)
async def send_webhooks():
    """주기적으로 웹훅 메시지 전송"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    current_time = time.time()
    c.execute("SELECT guild_id, webhook_message, interval_minutes FROM server_settings")
    settings = {row[0]: {'msg': row[1], 'interval': row[2] * 60 if row[2] else 3600} for row in c.fetchall()}
    
    c.execute("SELECT id, guild_id, webhook_url, last_sent FROM partners")
    partners = c.fetchall()

    for p_id, g_id, url, last_sent in partners:
        if g_id in settings:
            interval = settings[g_id]['interval']
            msg = settings[g_id]['msg']
            if current_time - last_sent >= interval:
                # 웹훅 전송
                try:
                    requests.post(url, json={"content": msg})
                    # 전송 시간 업데이트
                    c.execute("UPDATE partners SET last_sent = ? WHERE id = ?", (current_time, p_id))
                except:
                    pass
    conn.commit()
    conn.close()

@bot.event
async def on_ready():
    await bot.tree.sync()
    send_webhooks.start()
    print(f'Bot Ready: {bot.user}')

# === Flask 웹 대시보드 ===
app = Flask(__name__, template_folder='.')
app.secret_key = os.urandom(24)

OAUTH2_URL = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds"

@app.route('/')
def index():
    if 'token' in session:
        return redirect(url_for('dashboard'))
    return f'<a href="{OAUTH2_URL}">Discord로 로그인</a>'

@app.route('/callback')
def callback():
    code = request.args.get('code')
    data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    r = requests.post('https://discord.com/api/oauth2/token', data=data, headers=headers)
    session['token'] = r.json().get('access_token')
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    if 'token' not in session:
        return redirect(url_for('index'))
    # 본인이 관리자인 서버 목록 가져오기 로직 생략(간소화)
    # 실제로는 /users/@me/guilds API 호출하여 관리자 권한 확인
    return render_template('index.html')

@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    # 시연용 하드코딩 서버 ID (실제로는 OAuth2 세션 기반 서버 ID 연동 필요)
    guild_id = "YOUR_GUILD_ID" 
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    if request.method == 'POST':
        data = request.json
        c.execute('''INSERT OR REPLACE INTO server_settings 
                     (guild_id, title, description, button_text, webhook_message, interval_minutes) 
                     VALUES (?, ?, ?, ?, ?, ?)''', 
                  (guild_id, data['title'], data['description'], data['button_text'], data['webhook_message'], data['interval_minutes']))
        conn.commit()
        conn.close()
        # 설정 변경 시 백업 트리거
        asyncio.run_coroutine_threadsafe(backup_database(), bot.loop)
        return jsonify({"status": "success"})
    
    else:
        c.execute("SELECT title, description, button_text, webhook_message, interval_minutes FROM server_settings WHERE guild_id = ?", (guild_id,))
        row = c.fetchone()
        conn.close()
        if row:
            return jsonify({"title": row[0], "description": row[1], "button_text": row[2], "webhook_message": row[3], "interval_minutes": row[4]})
        return jsonify({"title": "", "description": "", "button_text": "생성하기", "webhook_message": "", "interval_minutes": 60})

# --- 서버 실행 ---
def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    bot.run(BOT_TOKEN)

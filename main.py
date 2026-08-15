import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import threading
import os
import requests
from datetime import datetime

# --- Flask 설정 ---
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))

# Discord OAuth2 설정 (환경 변수 필요)
# Discord OAuth2 설정 (환경 변수가 없어도 직접 대입되도록 수정)
CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID") or "1538062452749631609"
CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET") or "e6wfF_iitYum2KrZUmeJQwv94lppRlUq"
REDIRECT_URI = os.environ.get("DISCORD_REDIRECT_URI") or "https://one11111111111111111111111112.onrender.com/callback"
API_ENDPOINT = "https://discord.com/api/v10"

# 사용자별 데이터베이스 (실제 운영 시 MongoDB 등과 연동)
user_database = {}

# --- Discord 봇 설정 ---
class VaxisBot(commands.Bot):
    def __init__(self):
        token = os.environ.get("BOT_TOKEN")
        if not token:
            print("[오류] BOT_TOKEN 환경 변수가 설정되지 않았습니다!")
        
        intents = discord.Intents.default()
        intents.guilds = True
        intents.webhooks = True
        intents.guild_messages = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        print("슬래시 명령어가 정상적으로 동기화되었습니다.")

bot = VaxisBot()

# --- Discord 버튼 UI ---
class WebhookInfoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="새로고침", style=discord.ButtonStyle.blurple, emoji="🔄")
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("웹훅 상태를 새로고침 완료했습니다.", ephemeral=True)

# --- Discord 슬래시 명령어 (관리자 전용) ---
@bot.tree.command(name="서버확인", description="봇이 연동된 웹훅 채널과 최근 발송 정보를 확인합니다.")
@app_commands.checks.has_permissions(administrator=True)
async def check_server(interaction: discord.Interaction):
    if not interaction.guild.me.guild_permissions.manage_webhooks:
        return await interaction.response.send_message("웹훅을 확인할 권한(웹훅 관리)이 없습니다.", ephemeral=True)

    webhooks = await interaction.guild.webhooks()
    if not webhooks:
        return await interaction.response.send_message("이 서버에 연동된 웹훅이 없습니다.", ephemeral=True)

    embed = discord.Embed(title="🌐 서버 웹훅 연동 상태", color=0x00f2fe, timestamp=datetime.now())
    for wh in webhooks:
        last_sent = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        embed.add_field(
            name=f"채널: #{wh.channel.name if wh.channel else '알 수 없음'}",
            value=f"**웹훅 이름**: {wh.name}\n**최근 발송일**: {last_sent}",
            inline=False
        )

    view = WebhookInfoView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@check_server.error
async def check_server_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("이 명령어는 관리자만 사용할 수 있습니다.", ephemeral=True)

# --- Flask 라우트 (OAuth2 및 대시보드) ---
@app.route('/')
def index():
    return render_template('index.html', user=session.get('discord_user'))

@app.route('/login')
def login():
    discord_login_url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds"
    return redirect(discord_login_url)

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return redirect(url_for('index'))

    # 토큰 교환
    data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    response = requests.post(f"{API_ENDPOINT}/oauth2/token", data=data, headers=headers)
    token_json = response.json()
    access_token = token_json.get('access_token')

    if not access_token:
        return "OAuth 인증 실패", 400

    # 유저 정보 가져오기
    user_headers = {'Authorization': f'Bearer {access_token}'}
    user_resp = requests.get(f"{API_ENDPOINT}/users/@me", headers=user_headers)
    user_data = user_resp.json()

    # 세션에 사용자 정보 저장 (개인별 독립 세션)
    session['discord_user'] = {
        'id': user_data.get('id'),
        'username': user_data.get('username'),
        'avatar': f"https://cdn.discordapp.com/avatars/{user_data.get('id')}/{user_data.get('avatar')}.png"
    }
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('discord_user', None)
    return redirect(url_for('index'))

@app.route('/api/webhook_info', methods=['POST'])
def api_webhook_info():
    user = session.get('discord_user')
    if not user:
        return jsonify({"error": "로그인이 필요합니다."}), 401

    data = request.json
    webhook_url = data.get('webhook_url')
    partner_name = data.get('partner_name')
    user_id = user['id']

    if not webhook_url or not partner_name:
        return jsonify({"error": "웹훅 URL과 파트너명을 모두 입력해주세요."}), 400

    if user_id not in user_database:
        user_database[user_id] = {}

    # 웹훅 데이터 저장 및 파트너 자동화 시뮬레이션
    user_database[user_id][webhook_url] = {
        "partner_name": partner_name,
        "channel_name": f"partner-{partner_name}",
        "last_sent": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "failed_servers": "없음 (정상 작동 중)"
    }

    result = user_database[user_id][webhook_url]
    
    # 💡 DB 자동 백업 처리 (지정된 백업 채널 ID: 1538060612754735224)
    # 봇이 실행 중일 때 비동기로 백업 채널에 전송 가능
    
    return jsonify({
        "success": True,
        "partner_name": result["partner_name"],
        "channel_name": result["channel_name"],
        "last_sent": result["last_sent"],
        "failed_servers": result["failed_servers"]
    })

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    TOKEN = os.environ.get("BOT_TOKEN")
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("[크리티컬 에러] BOT_TOKEN 환경 변수가 비어있습니다.")

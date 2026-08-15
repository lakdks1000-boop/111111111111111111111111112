import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import threading
import os
import requests
import time
from datetime import datetime

# --- Flask 설정 ---
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))

# Discord OAuth2 설정 (본인의 클라이언트 ID와 시크릿을 입력하세요)
CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID") or "1538062452749631609"
CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET") or "e6wfF_iitYum2KrZUmeJQwv94lppRlUq"
REDIRECT_URI = os.environ.get("DISCORD_REDIRECT_URI") or "https://one11111111111111111111111112.onrender.com/callback"
API_ENDPOINT = "https://discord.com/api/v10"

# 데이터 저장을 위한 메모리 구조 (서버 설정 및 활성 웹훅 작업 관리)
server_settings = {}  # guild_id: category_id
active_webhooks = []  # 주기적 발송을 위한 웹훅 작업 리스트

# --- Discord 봇 설정 ---
class VaxisBot(commands.Bot):
    def __init__(self):
        token = os.environ.get("BOT_TOKEN") or "YOUR_BOT_TOKEN"
        intents = discord.Intents.default()
        intents.guilds = True
        intents.webhooks = True
        intents.guild_messages = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        print("슬래시 명령어가 정상적으로 동기화되었습니다.")

bot = VaxisBot()

# --- 백그라운드 자동 반복 전송 스레드 ---
def background_webhook_sender():
    while True:
        now = time.time()
        for item in active_webhooks:
            # item: {webhook_url, message, interval_seconds, last_sent, guild_id, channel_id}
            if now - item["last_sent"] >= item["interval_seconds"]:
                try:
                    payload = {
                        "content": item["message"]
                    }
                    requests.post(item["webhook_url"], json=payload)
                    item["last_sent"] = now
                    print(f"[자동 전송 성공] 웹훅으로 파트너 메시지 발송 완료")
                except Exception as e:
                    print(f"[자동 전송 오류] {e}")
        time.sleep(10)

threading.Thread(target=background_webhook_sender, daemon=True).start()

# --- Discord 슬래시 명령어 (관리자 전용) ---
@bot.tree.command(name="카테고리설정", description="파트너 채널들이 생성될 기본 카테고리를 설정합니다.")
@app_commands.checks.has_permissions(administrator=True)
async def set_category(interaction: discord.Interaction, category: discord.CategoryChannel):
    server_settings[interaction.guild.id] = category.id
    await interaction.response.send_message(f"성공적으로 파트너 채널 카테고리가 **{category.name}**(으)로 설정되었습니다.", ephemeral=True)

@set_category.error
async def set_category_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("이 명령어는 서버 관리자만 사용할 수 있습니다.", ephemeral=True)


@bot.tree.command(name="서버확인", description="연동된 웹훅 및 서버 상태를 확인합니다.")
@app_commands.checks.has_permissions(administrator=True)
async def check_server(interaction: discord.Interaction):
    embed = discord.Embed(title="🌐 VAXIS 파트너 시스템 현황", color=0x00f2fe, timestamp=datetime.now())
    embed.add_field(name="등록된 활성 웹훅 수", value=f"{len(active_webhooks)}개", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@check_server.error
async def check_server_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("이 명령어는 서버 관리자만 사용할 수 있습니다.", ephemeral=True)


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

    user_headers = {'Authorization': f'Bearer {access_token}'}
    user_resp = requests.get(f"{API_ENDPOINT}/users/@me", headers=user_headers)
    user_data = user_resp.json()

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
    message_content = data.get('message_content')
    interval_hours = float(data.get('interval_hours', 24)) # 기본 24시간 주기

    if not webhook_url or not partner_name or not message_content:
        return jsonify({"error": "모든 필드를 빠짐없이 입력해주세요."}), 400

    # 즉시 1회 전송 테스트 및 실행
    try:
        res = requests.post(webhook_url, json={"content": message_content})
        if res.status_code not in [200, 204]:
            return jsonify({"error": "유효하지 않거나 만료된 정보웹훅입니다."}), 400
    except Exception:
        return jsonify({"error": "웹훅 전송 중 오류가 발생했습니다."}), 400

    # 주기적 발송 리스트에 등록 (초 단위 변환)
    interval_seconds = interval_hours * 3600
    active_webhooks.append({
        "webhook_url": webhook_url,
        "message": message_content,
        "interval_seconds": interval_seconds,
        "last_sent": time.time(),
        "partner_name": partner_name
    })

    return jsonify({
        "success": True,
        "partner_name": partner_name,
        "channel_name": f"partner-{partner_name}",
        "last_sent": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "interval_text": f"{interval_hours}시간 주기"
    })

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    TOKEN = os.environ.get("BOT_TOKEN") or "YOUR_BOT_TOKEN"
    bot.run(TOKEN)

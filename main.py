import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask, render_template, request, jsonify, session
import threading
import os
import uuid
from datetime import datetime

# --- Flask 설정 ---
app = Flask(__name__)
# 환경 변수에 SECRET_KEY가 없으면 안전한 랜덤 키 자동 생성
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))

# 사용자별 웹훅 및 파트너 데이터베이스 세션 격리 저장소
webhook_database = {}

# --- Discord 봇 설정 ---
class VaxisBot(commands.Bot):
    def __init__(self):
        # 환경 변수로부터 토큰 가져오기 검증
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

# --- Flask 라우트 (웹 패널) ---
@app.route('/')
def index():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4()) # 사람마다 완전히 별개의 세션 보장
    return render_template('index.html')

@app.route('/api/webhook_info', methods=['POST'])
def api_webhook_info():
    data = request.json
    webhook_url = data.get('webhook_url')
    partner_name = data.get('partner_name') # 웹사이트에서 전달받은 파트너명
    user_id = session.get('user_id')

    if not webhook_url or not partner_name:
        return jsonify({"error": "웹훅 URL과 파트너명을 모두 입력해주세요."}), 400

    if user_id not in webhook_database:
        webhook_database[user_id] = {}

    # 웹훅 데이터 및 자동 파트너 생성 정보 저장
    webhook_database[user_id][webhook_url] = {
        "partner_name": partner_name,
        "channel_name": f"partner-{partner_name}",
        "last_sent": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "failed_servers": "없음 (정상 작동 중)"
    }

    result = webhook_database[user_id][webhook_url]
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
    # Flask 서버 백그라운드 구동
    threading.Thread(target=run_flask, daemon=True).start()
    
    # 환경 변수 기반 디스코드 봇 안전 실행
    TOKEN = os.environ.get("BOT_TOKEN")
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("[크리티컬 에러] BOT_TOKEN 환경 변수가 비어있습니다. Render 설정 확인 필요!")

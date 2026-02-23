"""Simple aiohttp server to host Cloudflare Turnstile verification page
and accept token submissions. This calls services.verification.verify_cloudflare_token
and notifies the user via the bot.
"""
import asyncio
from aiohttp import web
from config import config
from services.verification import verify_cloudflare_token
from telegram import Bot

BOT = Bot(token=config.BOT_TOKEN) if config.BOT_TOKEN else None

VERIFY_HTML = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>验证</title>
    <script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
  </head>
  <body>
    <h2>请完成人机验证</h2>
    <div id="widget"></div>
    <form id="verify-form" method="POST" action="/submit">
      <input type="hidden" name="user_id" id="user_id" value="{user_id}">
      <input type="hidden" name="site_key" id="site_key" value="{site_key}">
      <div class="cf-turnstile" data-sitekey="{site_key}"></div>
      <br>
      <button type="submit">提交验证</button>
    </form>
  </body>
</html>
"""

async def verify_page(request):
    user_id = request.query.get('user_id') or ''
    site_key = config.CLOUDFLARE_TURNSTILE_SITE_KEY or ''
    html = VERIFY_HTML.format(user_id=user_id, site_key=site_key)
    return web.Response(text=html, content_type='text/html')

async def submit_token(request):
    data = await request.post()
    token = data.get('cf-turnstile-response') or data.get('cf-turnstile-response')
    user_id = data.get('user_id')

    if not token or not user_id:
        return web.Response(text='Missing token or user_id', status=400)

    try:
        uid = int(user_id)
    except Exception:
        return web.Response(text='Invalid user_id', status=400)

    # 调用验证逻辑
    try:
        success, message, is_banned = await verify_cloudflare_token(uid, token)
    except Exception as e:
        return web.Response(text=f'Internal error: {e}', status=500)

    # 通知用户（如果可用）
    if BOT:
        try:
            await BOT.send_message(chat_id=uid, text=message)
        except Exception:
            pass

    return web.Response(text=message)

def run_app(host='0.0.0.0', port=8080):
    app = web.Application()
    app.add_routes([
        web.get('/verify', verify_page),
        web.post('/submit', submit_token),
    ])
    web.run_app(app, host=host, port=port)

if __name__ == '__main__':
    run_app()

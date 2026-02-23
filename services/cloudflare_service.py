"""Cloudflare Turnstile 验证服务"""
import aiohttp
from config import config

CLOUDFLARE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify_cloudflare_token(token: str) -> bool:
    """
    验证 Cloudflare Turnstile 令牌
    
    Args:
        token: Cloudflare 返回的验证令牌
        
    Returns:
        bool: 验证是否成功
    """
    if not config.CLOUDFLARE_TURNSTILE_SECRET_KEY:
        print("Cloudflare Turnstile 密钥未配置")
        return False
    
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "secret": config.CLOUDFLARE_TURNSTILE_SECRET_KEY,
                "response": token
            }
            
            async with session.post(CLOUDFLARE_VERIFY_URL, data=payload, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status != 200:
                    print(f"Cloudflare 验证失败: HTTP {response.status}")
                    return False
                
                result = await response.json()
                
                if result.get("success"):
                    return True
                else:
                    error_codes = result.get("error-codes", [])
                    print(f"Cloudflare 验证返回错误: {error_codes}")
                    return False
                    
    except aiohttp.ClientError as e:
        print(f"Cloudflare 连接错误: {e}")
        return False
    except Exception as e:
        print(f"Cloudflare 验证异常: {e}")
        return False


def get_cloudflare_iframe_html(site_key: str) -> str:
    """
    获取 Cloudflare Turnstile iframe HTML
    
    Args:
        site_key: Cloudflare 网站密钥
        
    Returns:
        str: HTML 代码
    """
    return f'''
<html>
<head>
    <title>验证</title>
    <script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
</head>
<body>
    <form method="POST" id="verification-form">
        <div class="cf-turnstile" 
             data-sitekey="{site_key}"
             data-callback="onSubmit"
             data-expired-callback="onExpired"
             data-error-callback="onError">
        </div>
        <button type="submit" disabled id="submit-btn">验证</button>
    </form>
    
    <script>
        function onSubmit(token) {{
            document.getElementById('submit-btn').disabled = false;
        }}
        
        function onExpired() {{
            document.getElementById('submit-btn').disabled = true;
        }}
        
        function onError() {{
            document.getElementById('submit-btn').disabled = true;
        }}
        
        document.getElementById('verification-form').addEventListener('submit', function(e) {{
            e.preventDefault();
            const formData = new FormData();
            formData.append('cf-turnstile-response', document.querySelector('[name=cf-turnstile-response]').value);
            // 此时应该将令牌发送到 Telegram Bot 的回调端点
        }});
    </script>
</body>
</html>
'''


def get_cloudflare_verification_message(site_key: str) -> str:
    """
    获取包含 Cloudflare 验证链接的消息
    
    Args:
        site_key: Cloudflare 网站密钥
        
    Returns:
        str: 验证消息
    """
    return (
        "🔒 请完成人机验证以继续\n\n"
        f"验证服务: Cloudflare Turnstile\n"
        "验证方式: 安全验证\n\n"
        "点击下方链接打开验证页面:\n"
        "[开始验证](https://your-domain.com/verify)\n\n"
        f"网站密钥: {site_key}\n"
        "验证后您将可以继续使用服务。"
    )

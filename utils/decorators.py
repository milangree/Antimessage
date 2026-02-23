from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes
from database.models import is_admin
from config import config

def admin_only(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not config.ADMIN_IDS:
            # 无声忽略
            return
        if not await is_admin(user.id):
            # 非管理员无声忽略，不显示任何消息
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

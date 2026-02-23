import re
import secrets
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from services.verification import verify_answer, create_verification, verify_image_answer, create_image_verification, verify_cloudflare_token
from services.gemini_service import gemini_service
from database import models as db
from utils.media_converter import sticker_to_image
from services.thread_manager import get_or_create_thread
from .user_handler import _resend_message
from config import config
from rss import data_manager as rss_data_manager, settings as rss_settings
from rss import enable_feature as rss_enable_feature, disable_feature as rss_disable_feature

RSS_PANEL_CACHE_KEY = "rss_panel_cache"
RSS_FEEDS_PER_PAGE = 4
RSS_DOC_URL = "https://github.com/milangree/Antimessage#-rss-%E8%AE%A2%E9%98%85%E5%8A%9F%E8%83%BD"


async def _build_main_panel_keyboard():
    """构建标准的主面板键盘布局，确保AI设置按钮始终显示"""
    keyboard = [
        [InlineKeyboardButton("黑名单管理", callback_data="panel_blacklist_page_1"), InlineKeyboardButton("所有用户信息", callback_data="panel_stats")],
        [InlineKeyboardButton("被过滤消息", callback_data="panel_filtered_page_1"), InlineKeyboardButton("自动回复管理", callback_data="panel_autoreply")],
        [InlineKeyboardButton("豁免名单管理", callback_data="panel_exemptions_page_1"), InlineKeyboardButton("RSS 功能管理", callback_data="panel_rss")],
        [InlineKeyboardButton("🎯 验证模式", callback_data="cmd_verification_mode"), InlineKeyboardButton("AI 模型设置", callback_data="panel_ai_settings")],
        [InlineKeyboardButton("🔙 返回管理员菜单", callback_data="menu_admin"), InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_start")],
    ]
    return InlineKeyboardMarkup(keyboard)

def _cache_rss_reference(application, kind, payload):
    token = secrets.token_hex(6)
    cache = application.bot_data.setdefault(RSS_PANEL_CACHE_KEY, {})
    if len(cache) >= 500:
        cache.clear()
    cache[token] = (kind, payload)
    return token


def _resolve_rss_reference(application, token, expected_kind):
    cache = application.bot_data.get(RSS_PANEL_CACHE_KEY, {})
    value = cache.get(token)
    if not value:
        return None
    kind, payload = value
    if kind != expected_kind:
        return None
    return payload


def _collect_rss_feeds():
    entries = []
    subscriptions = rss_data_manager.get_subscriptions()
    for chat_id, user_data in subscriptions.items():
        feeds = user_data.get("rss_feeds", {})
        for feed_url, feed_data in feeds.items():
            entries.append((chat_id, feed_url, feed_data))
    entries.sort(key=lambda item: (item[0], item[2].get("title", "")))
    return entries


def _build_rss_panel_view():
    enabled = rss_settings.is_enabled()
    status_text = "已启用" if enabled else "已关闭"
    lines = [
        "RSS 订阅功能控制台",
        "",
        f"当前状态: {status_text}",
        f"数据文件: {rss_settings.get_data_file()}",
        f"检查间隔: {rss_settings.get_check_interval()} 秒",
        "",
        "常用命令（私聊使用）：",
        "/rss_add <url>",
        "/rss_remove <url|ID>",
        "/rss_list",
        "/rss_addkeyword <ID> <关键词>",
        "/rss_removekeyword <ID> <关键词>",
        "/rss_listkeywords <ID>",
        "/rss_removeallkeywords <ID>",
        "/rss_setfooter [文本]",
        "/rss_togglepreview",
    ]

    keyboard = [
        [
            InlineKeyboardButton(
                "关闭 RSS 功能" if enabled else "开启 RSS 功能",
                callback_data="panel_rss_toggle",
            )
        ],
        [InlineKeyboardButton("查看订阅列表", callback_data="panel_rss_list_page_1")],
        [InlineKeyboardButton("查看 RSS 文档", url=RSS_DOC_URL)],
        [InlineKeyboardButton("返回主面板", callback_data="panel_back")],
    ]

    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


def _build_rss_list_view(application, page: int):
    feeds = _collect_rss_feeds()
    total = len(feeds)

    if total == 0:
        keyboard = [
            [InlineKeyboardButton("返回 RSS 控制台", callback_data="panel_rss")],
            [InlineKeyboardButton("返回主面板", callback_data="panel_back")],
        ]
        return "当前没有任何 RSS 订阅。", InlineKeyboardMarkup(keyboard)

    per_page = RSS_FEEDS_PER_PAGE
    total_pages = (total + per_page - 1) // per_page
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    subset = feeds[start : start + per_page]

    lines = [f"RSS 订阅列表 (第 {page}/{total_pages} 页)", ""]
    keyboard_rows = []

    for idx, (chat_id, feed_url, feed_data) in enumerate(subset, start=start + 1):
        title = feed_data.get("title", "未命名订阅")
        keywords = feed_data.get("keywords", [])
        keywords_text = ", ".join(keywords) if keywords else "无"
        lines.extend(
            [
                f"{idx}. 用户 {chat_id}",
                f"   标题: {title}",
                f"   链接: {feed_url}",
                f"   关键词: {keywords_text}",
                "",
            ]
        )
        token = _cache_rss_reference(
            application,
            "feed",
            {"chat_id": chat_id, "feed_url": feed_url},
        )
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    f"管理 #{idx}",
                    callback_data=f"panel_rss_feed_{token}",
                )
            ]
        )

    nav_buttons = []
    if page > 1:
        nav_buttons.append(
            InlineKeyboardButton("上一页", callback_data=f"panel_rss_list_page_{page-1}")
        )
    if page < total_pages:
        nav_buttons.append(
            InlineKeyboardButton("下一页", callback_data=f"panel_rss_list_page_{page+1}")
        )
    if nav_buttons:
        keyboard_rows.append(nav_buttons)

    keyboard_rows.append([InlineKeyboardButton("返回 RSS 控制台", callback_data="panel_rss")])
    keyboard_rows.append([InlineKeyboardButton("返回主面板", callback_data="panel_back")])

    return "\n".join(lines).strip(), InlineKeyboardMarkup(keyboard_rows)


def _build_rss_feed_detail(application, chat_id: str, feed_url: str):
    subscriptions = rss_data_manager.get_subscriptions()
    feed_data = (
        subscriptions.get(chat_id, {})
        .get("rss_feeds", {})
        .get(feed_url)
    )
    if not feed_data:
        return None, None

    title = feed_data.get("title", "未命名订阅")
    keywords = feed_data.get("keywords", [])

    lines = [
        "订阅详情",
        "",
        f"用户 ID: {chat_id}",
        f"标题: {title}",
        f"链接: {feed_url}",
    ]

    if keywords:
        lines.append("关键词：")
        lines.extend([f"- {kw}" for kw in keywords])
    else:
        lines.append("关键词：无（推送所有更新）")

    keyboard_rows = []
    remove_token = _cache_rss_reference(
        application,
        "feed",
        {"chat_id": chat_id, "feed_url": feed_url},
    )
    keyboard_rows.append(
        [InlineKeyboardButton("移除该订阅", callback_data=f"panel_rss_remove_{remove_token}")]
    )

    for kw in keywords:
        kw_token = _cache_rss_reference(
            application,
            "keyword",
            {"chat_id": chat_id, "feed_url": feed_url, "keyword": kw},
        )
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    f"删除关键词：{kw}",
                    callback_data=f"panel_rss_kwrm_{kw_token}",
                )
            ]
        )

    keyboard_rows.append([InlineKeyboardButton("返回订阅列表", callback_data="panel_rss_list_page_1")])
    keyboard_rows.append([InlineKeyboardButton("返回 RSS 控制台", callback_data="panel_rss")])

    return "\n".join(lines), InlineKeyboardMarkup(keyboard_rows)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    # 处理用户菜单
    if data == "menu_user":
        # 非管理员不允许访问菜单
        if not await db.is_admin(user_id):
            await query.answer("非管理员无法使用此功能。", show_alert=True)
            return
        return
    
    # 处理返回主菜单
    if data == "menu_start":
        # 非管理员不允许访问菜单
        if not await db.is_admin(user_id):
            await query.answer("非管理员无法使用此功能。", show_alert=True)
            return
        
        keyboard = [
            [InlineKeyboardButton("🔧 管理员菜单", callback_data="menu_admin")]
        ]
        
        menu_text = (
            "**主菜单**\n\n"
            "欢迎使用双向聊天机器人。\n"
            "你可以直接在这里发送消息，管理员会尽快回复你。\n\n"
            "请选择一个菜单："
        )
        await query.edit_message_text(
            menu_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return
    
    # 处理管理员菜单 - 直接打开管理面板
    if data == "menu_admin":
        if not await db.is_admin(user_id):
            await query.answer("你没有权限访问管理员菜单", show_alert=True)
            return
        
        # 直接转到管理面板
        data = "panel_main"
    
    # 关闭菜单
    if data == "menu_close":
        await query.edit_message_text("✓ 已关闭菜单")
        return
    
    # 处理用户命令（通过按钮）
    if data == "cmd_getid":
        is_admin = await db.is_admin(user_id)
        
        # 非管理员不允许使用菜单
        if not is_admin:
            await query.answer("非管理员无法使用菜单系统，请使用 `/getid` 命令。", show_alert=True)
            return
        
        user = query.from_user
        message_text = (
            f"**您的用户信息:**\n\n"
            f"用户ID: `{user.id}`\n"
            f"名字: {user.first_name}\n"
            f"用户名: @{user.username or '无'}"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 返回管理面板", callback_data="panel_back")]]
        await query.edit_message_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return
    
    if data == "cmd_verification_mode":
        is_admin = await db.is_admin(user_id)
        
        # 非管理员不允许使用菜单
        if not is_admin:
            await query.answer("非管理员无法使用菜单系统，请使用 `/verification_mode` 命令。", show_alert=True)
            return
        
        user_verification_mode = await db.get_user_verification_mode(user_id)
        from config import config
        
        if user_verification_mode:
            mode_text = "图片验证码" if user_verification_mode == "image" else "文本验证"
            is_custom = "✓ 已自定义" if user_verification_mode else ""
        else:
            mode_text = "图片验证码" if config.VERIFICATION_USE_IMAGE else "文本验证"
            is_custom = "（默认设置）"
        
        from config import config as _cfg

        keyboard = [
            [InlineKeyboardButton("🖼️ 图片（数字）", callback_data="set_verification_image_digits"),
             InlineKeyboardButton("📝 文本验证", callback_data="set_verification_text")],
            [InlineKeyboardButton("🔤 纯字母图片验证码", callback_data="set_verification_image_letters"),
             InlineKeyboardButton("🔠 字母数字混合图片验证码", callback_data="set_verification_image_mixed")],
        ]

        # 如果启用了 Cloudflare 验证，显示切换按钮
        if _cfg.VERIFICATION_USE_CLOUDFLARE:
            keyboard.append([InlineKeyboardButton("☁️ Cloudflare 验证", callback_data="set_verification_cloudflare")])

        keyboard.append([InlineKeyboardButton("🔄 使用默认设置", callback_data="set_verification_default")])
        keyboard.append([InlineKeyboardButton("🔙 返回管理面板", callback_data="panel_back"),
             InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_start")])
        
        message_text = (
            "**验证模式设置**\n\n"
            f"当前模式: {mode_text} {is_custom}\n\n"
            "请选择您的验证方式："
        )
        
        await query.edit_message_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return
    
    # 处理AI审查设置
    if data == "set_ai_check_on":
        if not await db.is_admin(user_id):
            await query.answer("你没有权限", show_alert=True)
            return
        await db.set_ai_check_disabled(user_id, False)
        await query.answer("✓ 已启用 AI 内容审查")
        # 重新显示AI模型设置页面
        data = "panel_ai_settings"
    
    if data == "set_ai_check_off":
        if not await db.is_admin(user_id):
            await query.answer("你没有权限", show_alert=True)
            return
        await db.set_ai_check_disabled(user_id, True)
        await query.answer("✓ 已禁用 AI 内容审查")
        # 重新显示AI模型设置页面
        data = "panel_ai_settings"
    
    
    if data == "cmd_blacklist":
        if not await db.is_admin(user_id):
            await query.answer("你没有权限", show_alert=True)
            return
        # 转发到管理面板黑名单页面
        data = "panel_blacklist_page_1"
    
    if data == "cmd_stats":
        if not await db.is_admin(user_id):
            await query.answer("你没有权限", show_alert=True)
            return
        # 转发到管理面板统计页面
        data = "panel_stats"
    
    if data == "cmd_view_filtered":
        if not await db.is_admin(user_id):
            await query.answer("你没有权限", show_alert=True)
            return
        # 转发到管理面板被过滤消息页面
        data = "panel_filtered_page_1"
    
    if data == "cmd_autoreply":
        if not await db.is_admin(user_id):
            await query.answer("你没有权限", show_alert=True)
            return
        # 转发到管理面板自动回复页面
        data = "panel_autoreply"
    
    if data == "set_autoreply_on":
        if not await db.is_admin(user_id):
            await query.answer("你没有权限", show_alert=True)
            return
        await db.set_autoreply_enabled(True)
        await query.answer("✓ 已启用自动回复")
        await query.edit_message_text("✓ 已启用自动回复功能")
        return
    
    if data == "set_autoreply_off":
        if not await db.is_admin(user_id):
            await query.answer("你没有权限", show_alert=True)
            return
        await db.set_autoreply_enabled(False)
        await query.answer("✓ 已禁用自动回复")
        await query.edit_message_text("✓ 已禁用自动回复功能")
        return
    
    if data == "cmd_exemptions":
        if not await db.is_admin(user_id):
            await query.answer("你没有权限", show_alert=True)
            return
        # 转发到管理面板豁免名单页面
        data = "panel_exemptions_page_1"
    
    # 验证模式选择
    if data.startswith("set_verification_"):
        is_admin = await db.is_admin(user_id)
        
        # 非管理员不允许使用菜单
        if not is_admin:
            await query.answer("非管理员无法使用菜单系统，请使用 `/verification_mode` 命令。", show_alert=True)
            return
        
        # 支持更细化的图片验证码类型：digits, letters, mixed
        mode_key = data[len("set_verification_"):]

        if mode_key.startswith("image"):
            # mode_key 可能为: image_digits, image_letters, image_mixed, 或 image
            parts = mode_key.split("_")
            image_type = None
            if len(parts) > 1:
                image_type = parts[1]

            # 保存为通用的 image 模式，同时单独保存图片验证码子类型
            await db.set_user_verification_mode(user_id, "image")
            await db.set_user_verification_image_type(user_id, image_type)
            await query.answer("✓ 已设置为图片验证码")
            if image_type == "letters":
                msg = "✓ 已设置验证模式为 **纯字母图片验证码**\n\n下次人机验证时将使用纯字母验证码。"
            elif image_type == "mixed":
                msg = "✓ 已设置验证模式为 **字母数字混合图片验证码**\n\n下次人机验证时将使用字母数字混合验证码。"
            else:
                msg = "✓ 已设置验证模式为 **图片验证码（数字）**\n\n下次人机验证时将使用数字图片验证码。"
        elif mode_key == "text":
            await db.set_user_verification_mode(user_id, "text")
            await query.answer("✓ 已设置为文本验证")
            msg = "✓ 已设置验证模式为 **文本验证**\n\n下次人机验证时将使用常识性问答。"
        elif mode_key == "cloudflare":
            await db.set_user_verification_mode(user_id, "cloudflare")
            await query.answer("✓ 已设置为 Cloudflare 验证")
            msg = "✓ 已设置验证模式为 **Cloudflare 验证**\n\n下次人机验证时将使用 Cloudflare Turnstile 验证（如果已全局启用）。"
        elif mode_key == "default":
            await db.set_user_verification_mode(user_id, None)
            from config import config
            default_mode = "图片验证码" if config.VERIFICATION_USE_IMAGE else "文本验证"
            msg = f"✓ 已重置为默认设置\n\n默认验证模式: {default_mode}"
            await query.answer("✓ 已重置为默认设置")
        else:
            return
        
        keyboard = [
            [InlineKeyboardButton("🔙 返回验证模式设置", callback_data="cmd_verification_mode")],
            [InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_start")]
        ]
        
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return
    
    # 其他现有的回调处理...
    if data.startswith("block_user_"):

        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            target_user_id = int(data.split("_")[2])
        except (ValueError, IndexError):
            await query.answer("无效的用户ID。", show_alert=True)
            return
        
        from services.blacklist import block_user
        reason = "通过话题用户卡片按钮"
        response = await block_user(target_user_id, reason, user_id, permanent=True)
        
        # 更新内联按钮为解封用户
        keyboard = [[InlineKeyboardButton("解封用户", callback_data=f"admin_unblock_{target_user_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_reply_markup(reply_markup=reply_markup)
        except:
            pass
        
        await query.answer(f"已封禁用户\n\n{response}", show_alert=True)
        return
    
    if data.startswith("admin_unblock_"):
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            target_user_id = int(data.split("_")[2])
        except (ValueError, IndexError):
            await query.answer("无效的用户ID。", show_alert=True)
            return
        
        from services.blacklist import unblock_user
        response = await unblock_user(target_user_id)
        
        # 更新内联按钮为封禁用户
        keyboard = [[InlineKeyboardButton("封禁用户", callback_data=f"block_user_{target_user_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_reply_markup(reply_markup=reply_markup)
        except:
            pass
        
        await query.answer(f"已解封用户\n\n{response}", show_alert=True)
        return
    
    if data.startswith("already_banned_"):
        await query.answer("该用户已被永久封禁", show_alert=True)
        return

    # 允许用户/Cloudflare 验证界面切换到文本/图片验证的回调（全局处理）
    if data == "switch_verification_text":
        try:
            question, keyboard = await create_verification(user_id)
            try:
                await query.message.delete()
            except:
                pass
            await query.message.reply_text(text=question, reply_markup=keyboard)
        except Exception as e:
            print(f"切换到文本验证失败: {e}")
        return

    if data == "switch_verification_image":
        try:
            image_io, caption, keyboard = await create_image_verification(user_id)
            try:
                await query.message.delete()
            except:
                pass
            await query.message.reply_photo(photo=image_io, caption=caption, reply_markup=keyboard)
        except Exception as e:
            print(f"切换到图片验证失败: {e}")
        return

    if data.startswith("cloudflare_verify_"):
        # Cloudflare 验证处理
        user_id_str = data.split("_", 2)[2]
        try:
            target_user_id = int(user_id_str)
        except:
            await query.answer("❌ 用户ID无效", show_alert=True)
            return
        
        # 此处应该打开 Cloudflare Turnstile 验证窗口
        # 在实际应用中，应该返回包含 Cloudflare iframe 的网页链接或直接打开 Web App
        await query.answer(
            "🔐 请在打开的验证窗口中完成Cloudflare验证",
            show_alert=False
        )
        
        # 发送包含验证链接的消息
        verification_link = (
            "请点击下方链接完成 Cloudflare Turnstile 验证:\n"
            "[开始验证](https://your-domain.com/verify)\n\n"
            "验证完成后，您将自动通过验证。"
        )
        
        try:
            await query.message.reply_text(
                verification_link,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
        except:
            pass
        
        return

    if data.startswith("verify_image_"):
        answer = data.split("_", 2)[2]
        success, verify_message, is_banned, new_verification = await verify_image_answer(user_id, answer)

        if is_banned:
            await query.edit_message_text(text=verify_message, reply_markup=None)
            return

        if new_verification:
            new_image_bytes, new_message_text, new_keyboard = new_verification
            try:
                await query.edit_message_caption(
                    caption=f"{verify_message}\n\n{new_message_text}",
                    reply_markup=new_keyboard
                )
                await query.edit_message_media(
                    media=InputMediaPhoto(media=new_image_bytes, caption=f"{verify_message}\n\n{new_message_text}"),
                    reply_markup=new_keyboard
                )
            except Exception as e:
                print(f"编辑消息失败: {e}")
                try:
                    await query.message.delete()
                except:
                    pass
                await query.message.reply_photo(
                    photo=new_image_bytes,
                    caption=f"{verify_message}\n\n{new_message_text}",
                    reply_markup=new_keyboard
                )
            return

        try:
            await query.edit_message_text(text=verify_message)
        except:
            pass

        if success:
            try:
                await query.message.delete()
            except:
                pass

            if 'pending_update' in context.user_data:
                pending_update = context.user_data.pop('pending_update')
                message = pending_update.message
                image_bytes = None

                if message.photo:
                    photo_file = await message.photo[-1].get_file()
                    image_bytes = await photo_file.download_as_bytearray()
                elif message.sticker and not message.sticker.is_animated and not message.sticker.is_video:
                    sticker_file = await message.sticker.get_file()
                    sticker_bytes = await sticker_file.download_as_bytearray()
                    image_bytes = await sticker_to_image(sticker_bytes)

                should_forward = True
                if message.video or message.animation:
                    pass
                else:
                    is_exempted = await db.is_exempted(user_id)
                    ai_check_disabled = await db.is_ai_check_disabled(user_id)

                    if not is_exempted and not ai_check_disabled:
                        analyzing_message = await context.bot.send_message(
                            chat_id=message.chat_id,
                            text="正在通过AI分析内容是否包含垃圾信息...",
                            reply_to_message_id=message.message_id
                        )
                        analysis_result = await gemini_service.analyze_message(message, image_bytes)
                        if analysis_result.get("is_spam"):
                            should_forward = False
                            media_type = None
                            media_file_id = None
                            if message.photo:
                                media_type = "photo"
                                media_file_id = message.photo[-1].file_id
                            elif message.sticker:
                                media_type = "sticker"
                                media_file_id = message.sticker.file_id

                            await db.save_filtered_message(
                                user_id=user_id,
                                message_id=message.message_id,
                                content=message.text or message.caption,
                                reason=analysis_result.get("reason"),
                                media_type=media_type,
                                media_file_id=media_file_id,
                            )
                            reason = analysis_result.get("reason", "未提供原因")
                            await analyzing_message.edit_text(f"您的消息已被系统拦截，因此未被转发\n\n原因：{reason}")
                        else:
                            await analyzing_message.delete()

                if should_forward:
                    thread_id, is_new = await get_or_create_thread(pending_update, context)
                    if not thread_id:
                        await pending_update.message.reply_text("无法创建或找到您的话题，请联系管理员。")
                        return

                    try:
                        if not is_new:
                            await _resend_message(pending_update, context, thread_id)
                    except BadRequest as e:
                        if "Message thread not found" in e.message:
                            await db.update_user_thread_id(user_id, None)
                            await db.update_user_verification(user_id, False)

                            context.user_data['pending_update'] = pending_update
                            question, keyboard = await create_verification(user_id)

                            full_message = (
                                "您的话题已被关闭，请重新进行验证以发送消息。\n\n"
                                f"{question}"
                            )

                            await pending_update.message.reply_text(
                                text=full_message,
                                reply_markup=keyboard
                            )
                        else:
                            print(f"发送消息时发生未知错误: {e}")
                            await pending_update.message.reply_text("发送消息时发生未知错误，请稍后再试。")
            else:
                await query.message.reply_text("现在您可以发送消息了！")

    if data.startswith("verify_"):
        answer = data.split("_", 1)[1]
        success, message, is_banned, new_question = await verify_answer(user_id, answer)
        
        if is_banned:
            await query.edit_message_text(text=message, reply_markup=None)
            return
        
        if new_question:
            new_question_text, new_keyboard = new_question
            await query.edit_message_text(
                text=f"{message}\n\n{new_question_text}",
                reply_markup=new_keyboard
            )
            return
        
        await query.edit_message_text(text=message)

        if success:
            if 'pending_update' in context.user_data:
                pending_update = context.user_data.pop('pending_update')
                message = pending_update.message
                image_bytes = None

                if message.photo:
                    photo_file = await message.photo[-1].get_file()
                    image_bytes = await photo_file.download_as_bytearray()
                elif message.sticker and not message.sticker.is_animated and not message.sticker.is_video:
                    sticker_file = await message.sticker.get_file()
                    sticker_bytes = await sticker_file.download_as_bytearray()
                    image_bytes = await sticker_to_image(sticker_bytes)

                should_forward = True
                if message.video or message.animation:
                    pass
                else:
                    is_exempted = await db.is_exempted(user_id)
                    ai_check_disabled = await db.is_ai_check_disabled(user_id)
                    
                    if not is_exempted and not ai_check_disabled:
                        analyzing_message = await context.bot.send_message(
                            chat_id=message.chat_id,
                            text="正在通过AI分析内容是否包含垃圾信息...",
                            reply_to_message_id=message.message_id
                        )
                        analysis_result = await gemini_service.analyze_message(message, image_bytes)
                        if analysis_result.get("is_spam"):
                            should_forward = False
                            media_type = None
                            media_file_id = None
                            if message.photo:
                                media_type = "photo"
                                media_file_id = message.photo[-1].file_id
                            elif message.sticker:
                                media_type = "sticker"
                                media_file_id = message.sticker.file_id

                            await db.save_filtered_message(
                                user_id=user_id,
                                message_id=message.message_id,
                                content=message.text or message.caption,
                                reason=analysis_result.get("reason"),
                                media_type=media_type,
                                media_file_id=media_file_id,
                            )
                            reason = analysis_result.get("reason", "未提供原因")
                            await analyzing_message.edit_text(f"您的消息已被系统拦截，因此未被转发\n\n原因：{reason}")
                        else:
                            await analyzing_message.delete()

                if should_forward:
                    thread_id, is_new = await get_or_create_thread(pending_update, context)
                    if not thread_id:
                        await pending_update.message.reply_text("无法创建或找到您的话题，请联系管理员。")
                        return
                    
                    try:
                        if not is_new:
                            await _resend_message(pending_update, context, thread_id)
                    except BadRequest as e:
                        if "Message thread not found" in e.message:
                            await db.update_user_thread_id(user_id, None)
                            await db.update_user_verification(user_id, False)
                            
                            context.user_data['pending_update'] = pending_update
                            question, keyboard = await create_verification(user_id)
                            
                            full_message = (
                                "您的话题已被关闭，请重新进行验证以发送消息。\n\n"
                                f"{question}"
                            )
                            
                            await pending_update.message.reply_text(
                                text=full_message,
                                reply_markup=keyboard
                            )
                        else:
                            print(f"发送消息时发生未知错误: {e}")
                            await pending_update.message.reply_text("发送消息时发生未知错误，请稍后再试。")
            else:
                await query.message.reply_text("现在您可以发送消息了！")
    
    elif data == "panel_main" or data == "panel_back":
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        total_users = await db.get_total_users_count()
        blocked_users = await db.get_blocked_users_count()
        exempted_users = await db.get_exemptions_count()
        is_enabled = await db.get_autoreply_enabled()
        
        message = (
            f"管理面板\n\n"
            f"统计信息:\n\n"
            f"总用户数: {total_users}\n"
            f"黑名单用户数: {blocked_users}\n"
            f"豁免用户数: {exempted_users}\n"
            f"自动回复状态: {'已启用' if is_enabled else '已禁用'}\n\n"
            f"请选择要查看的功能："
        )
        
        keyboard = await _build_main_panel_keyboard()
        
        await query.edit_message_text(
            message,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    
    elif data.startswith("panel_blacklist_page_"):
        from services import blacklist
        
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[3])
        except (ValueError, IndexError):
            await query.answer("无效的页码。", show_alert=True)
            return
        
        message, keyboard = await blacklist.get_blacklist_keyboard(page=page)
        
        if keyboard:
            keyboard_buttons = list(keyboard.inline_keyboard)
            keyboard_buttons.append([InlineKeyboardButton("返回主面板", callback_data="panel_back")])
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])
            await query.edit_message_text(text=message, reply_markup=back_keyboard)
    
    elif data == "panel_stats":
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        from services.blacklist import get_all_users_keyboard
        
        page = 1
        message, keyboard = await get_all_users_keyboard(
            page=page,
            callback_prefix="panel_stats_all_users_page_",
            back_callback="panel_back",
            back_text="返回主面板"
        )
        
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])
            await query.edit_message_text(text=message, reply_markup=back_keyboard, parse_mode='Markdown')
    
    elif data.startswith("panel_stats_all_users_page_"):
        from services.blacklist import get_all_users_keyboard
        
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[5])
        except (ValueError, IndexError):
            await query.answer("无效的页码。", show_alert=True)
            return
        
        message, keyboard = await get_all_users_keyboard(
            page=page,
            callback_prefix="panel_stats_all_users_page_",
            back_callback="panel_back",
            back_text="返回主面板"
        )
        
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
    
    elif data.startswith("panel_stats_blacklist_page_"):
        from services.blacklist import get_blacklist_keyboard_detailed
        
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[4])
        except (ValueError, IndexError):
            await query.answer("无效的页码。", show_alert=True)
            return
        
        message, keyboard = await get_blacklist_keyboard_detailed(page=page)
        
        if keyboard:
            keyboard_buttons = [list(row) for row in keyboard.inline_keyboard]
            for i, row in enumerate(keyboard_buttons):
                for j, button in enumerate(row):
                    if button.callback_data == "stats_back_to_menu":
                        keyboard_buttons[i][j] = InlineKeyboardButton("返回主面板", callback_data="panel_back")
                        break
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])
            await query.edit_message_text(text=message, reply_markup=back_keyboard, parse_mode='Markdown')
    
    elif data.startswith("panel_filtered_page_"):
        from .admin_handler import _format_filtered_messages, _get_filtered_messages_keyboard
        
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[3])
        except (ValueError, IndexError):
            await query.answer("无效的页码。", show_alert=True)
            return
        
        MESSAGES_PER_PAGE = 5

        total_count = await db.get_filtered_messages_count()
        
        if total_count == 0:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])
            await query.edit_message_text("没有找到被过滤的消息。", reply_markup=back_keyboard)
            return
        
        total_pages = (total_count + MESSAGES_PER_PAGE - 1) // MESSAGES_PER_PAGE

        if page < 1:
            page = 1
        elif page > total_pages:
            page = total_pages

        offset = (page - 1) * MESSAGES_PER_PAGE

        messages = await db.get_filtered_messages(MESSAGES_PER_PAGE, offset)
        
        if not messages:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])
            await query.edit_message_text("没有找到被过滤的消息。", reply_markup=back_keyboard)
            return

        response = await _format_filtered_messages(messages, page, total_pages)

        keyboard = await _get_filtered_messages_keyboard(page, total_pages, callback_prefix="panel_filtered_page_")
        
        if keyboard:
            keyboard_buttons = [list(row) for row in keyboard.inline_keyboard]
            keyboard_buttons.append([InlineKeyboardButton("返回主面板", callback_data="panel_back")])
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
        else:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])

        await query.edit_message_text(response, reply_markup=keyboard)
    
    elif data == "panel_autoreply":
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        is_enabled = await db.get_autoreply_enabled()
        status_text = "已启用" if is_enabled else "已禁用"
        
        message = (
            f"自动回复管理\n\n"
            f"当前状态: {status_text}\n\n"
            f"请选择操作："
        )
        
        keyboard = [
            [
                InlineKeyboardButton(
                    "关闭自动回复" if is_enabled else "开启自动回复",
                    callback_data="panel_autoreply_toggle"
                )
            ],
            [InlineKeyboardButton("管理知识库", callback_data="panel_autoreply_kb_list_page_1")],
            [InlineKeyboardButton("添加知识条目", callback_data="panel_autoreply_kb_add")],
            [InlineKeyboardButton("返回主面板", callback_data="panel_back")],
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "panel_rss":
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return

        message, keyboard = _build_rss_panel_view()
        await query.edit_message_text(message, reply_markup=keyboard)

    elif data == "panel_ai_settings":
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
            
        async with db.db_manager.get_connection() as conn:
             cursor = await conn.execute("""
                SELECT key, value FROM settings 
                WHERE key IN (
                    'ai_provider', 
                    'gemini_model_filter', 'gemini_model_verification', 'gemini_model_autoreply',
                    'openai_model_filter', 'openai_model_verification', 'openai_model_autoreply'
                )
             """)
             settings = {row[0]: row[1] for row in await cursor.fetchall()}
             
        current_provider = settings.get('ai_provider', 'gemini')
        
        provider_name = "Gemini" if current_provider == 'gemini' else "OpenAI"
        
        message = (
            f"🤖 **AI 模型设置**\n\n"
            f"当前提供商: `{provider_name}`\n\n"
            f"**Gemini 模型**:\n"
            f"• 审查: `{settings.get('gemini_model_filter', 'N/A')}`\n"
            f"• 验证: `{settings.get('gemini_model_verification', 'N/A')}`\n"
            f"• 回复: `{settings.get('gemini_model_autoreply', 'N/A')}`\n\n"
            f"**OpenAI 模型**:\n"
            f"• 审查: `{settings.get('openai_model_filter', 'N/A')}`\n"
            f"• 验证: `{settings.get('openai_model_verification', 'N/A')}`\n"
            f"• 回复: `{settings.get('openai_model_autoreply', 'N/A')}`\n\n"
            f"请选择要配置的项目:"
        )
        
        is_disabled = await db.is_ai_check_disabled(user_id)
        ai_status = "已禁用 ❌" if is_disabled else "已启用 ✓"
        
        keyboard = [
            [
                InlineKeyboardButton(f"{'✅ ' if current_provider == 'gemini' else ''}使用 Gemini", callback_data="ai_set_provider_gemini"),
                InlineKeyboardButton(f"{'✅ ' if current_provider == 'openai' else ''}使用 OpenAI", callback_data="ai_set_provider_openai")
            ],
            [
                InlineKeyboardButton("配置 Gemini 模型", callback_data="ai_config_models_gemini"),
                InlineKeyboardButton("配置 OpenAI 模型", callback_data="ai_config_models_openai")
            ],
            [
                InlineKeyboardButton(f"启用 AI 审查 {'✓' if not is_disabled else ''}", callback_data="set_ai_check_on"),
                InlineKeyboardButton(f"禁用 AI 审查 {'❌' if is_disabled else ''}", callback_data="set_ai_check_off")
            ],
            [InlineKeyboardButton("返回主面板", callback_data="panel_back")]
        ]
        
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("ai_set_provider_"):
        if not await db.is_admin(user_id): return
        
        new_provider = data.split("_")[3]
        async with db.db_manager.get_connection() as conn:
            await conn.execute("UPDATE settings SET value = ? WHERE key = 'ai_provider'", (new_provider,))
            await conn.commit()
            
        await query.answer(f"已切换 AI 提供商为 {new_provider.upper()}")
        
        async with db.db_manager.get_connection() as conn:
             cursor = await conn.execute("""
                SELECT key, value FROM settings 
                WHERE key IN (
                    'ai_provider', 
                    'gemini_model_filter', 'gemini_model_verification', 'gemini_model_autoreply',
                    'openai_model_filter', 'openai_model_verification', 'openai_model_autoreply'
                )
             """)
             settings = {row[0]: row[1] for row in await cursor.fetchall()}
             
        current_provider = settings.get('ai_provider', 'gemini')
        provider_name = "Gemini" if current_provider == 'gemini' else "OpenAI"
        
        message = (
            f"🤖 **AI 模型设置**\n\n"
            f"当前提供商: `{provider_name}`\n\n"
            f"**Gemini 模型**:\n"
            f"• 审查: `{settings.get('gemini_model_filter', 'N/A')}`\n"
            f"• 验证: `{settings.get('gemini_model_verification', 'N/A')}`\n"
            f"• 回复: `{settings.get('gemini_model_autoreply', 'N/A')}`\n\n"
            f"**OpenAI 模型**:\n"
            f"• 审查: `{settings.get('openai_model_filter', 'N/A')}`\n"
            f"• 验证: `{settings.get('openai_model_verification', 'N/A')}`\n"
            f"• 回复: `{settings.get('openai_model_autoreply', 'N/A')}`\n\n"
            f"请选择要配置的项目:"
        )
        
        keyboard = [
            [
                InlineKeyboardButton(f"{'✅ ' if current_provider == 'gemini' else ''}使用 Gemini", callback_data="ai_set_provider_gemini"),
                InlineKeyboardButton(f"{'✅ ' if current_provider == 'openai' else ''}使用 OpenAI", callback_data="ai_set_provider_openai")
            ],
            [
                InlineKeyboardButton("配置 Gemini 模型", callback_data="ai_config_models_gemini"),
                InlineKeyboardButton("配置 OpenAI 模型", callback_data="ai_config_models_openai")
            ],
            [InlineKeyboardButton("返回主面板", callback_data="panel_back")]
        ]
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("ai_config_models_"):
        if not await db.is_admin(user_id): return
        
        provider_type = data.split("_")[3]
        
        message = f"请选择要配置的 {provider_type.upper()} 功能模型:"
        
        keyboard = [
            [InlineKeyboardButton("内容审查模型", callback_data=f"ai_select_model_{provider_type}_filter")],
            [InlineKeyboardButton("验证码生成模型", callback_data=f"ai_select_model_{provider_type}_verification")],
            [InlineKeyboardButton("自动回复模型", callback_data=f"ai_select_model_{provider_type}_autoreply")],
            [InlineKeyboardButton("返回设置", callback_data="panel_ai_settings")]
        ]
        
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("ai_select_model_"):
        if not await db.is_admin(user_id): return
        
        parts = data.split("_")
        provider_type = parts[3]
        feature_type = parts[4]
        
        from services.ai_service import ai_service
        
        await query.answer("正在获取模型列表...", show_alert=False)
        
        try:
            models = await ai_service.get_available_models(provider_type)
        except Exception as e:
            await query.answer(f"获取模型失败: {e}", show_alert=True)
            return

        if not models:
             await query.answer("未能获取到模型列表，请检查 API Key 配置。", show_alert=True)
             return
        
        keyboard = []
        
        p_code = 'g' if provider_type == 'gemini' else 'o'
        f_map = {'filter': 'f', 'verification': 'v', 'autoreply': 'a'}
        f_code = f_map.get(feature_type, 'f')

        for model in models[:20]:
             keyboard.append([InlineKeyboardButton(model, callback_data=f"setm:{p_code}:{f_code}:{model}")])
        
        keyboard.append([InlineKeyboardButton("返回上一级", callback_data=f"ai_config_models_{provider_type}")])
        
        feature_name_map = {
            'filter': '内容审查',
            'verification': '验证码生成',
            'autoreply': '自动回复'
        }
        feature_name = feature_name_map.get(feature_type, feature_type)
        
        await query.edit_message_text(
            f"请选择 {provider_type.upper()} {feature_name} 模型:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("setm:"):
        if not await db.is_admin(user_id): return
        
        try:
            _, p_code, f_code, model_name = data.split(":", 3)
        except ValueError:
            await query.answer("无效的请求数据", show_alert=True)
            return
            
        p_map = {'g': 'gemini', 'o': 'openai'}
        f_map = {'f': 'filter', 'v': 'verification', 'a': 'autoreply'}
        
        provider_type = p_map.get(p_code, 'gemini')
        feature_type = f_map.get(f_code, 'filter')
        
        setting_key = f"{provider_type}_model_{feature_type}"
        
        async with db.db_manager.get_connection() as conn:
            await conn.execute("UPDATE settings SET value = ? WHERE key = ?", (model_name, setting_key))
            await conn.commit()
            
        await query.answer(f"已设置 {provider_type.upper()} {feature_type} 模型为 {model_name}")
        
        message = f"请选择要配置的 {provider_type.upper()} 功能模型:"
        keyboard = [
            [InlineKeyboardButton("内容审查模型", callback_data=f"ai_select_model_{provider_type}_filter")],
            [InlineKeyboardButton("验证码生成模型", callback_data=f"ai_select_model_{provider_type}_verification")],
            [InlineKeyboardButton("自动回复模型", callback_data=f"ai_select_model_{provider_type}_autoreply")],
            [InlineKeyboardButton("返回设置", callback_data="panel_ai_settings")]
        ]
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))

    
    elif data == "panel_rss_toggle":
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return

        app = context.application
        if rss_settings.is_enabled():
            changed = rss_disable_feature(app)
            if changed:
                await query.answer("RSS 功能已关闭", show_alert=True)
        else:
            changed = rss_enable_feature(app)
            if changed:
                await query.answer("RSS 功能已开启", show_alert=True)

        message, keyboard = _build_rss_panel_view()
        await query.edit_message_text(message, reply_markup=keyboard)
    
    elif data.startswith("panel_rss_list_page_"):
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return

        try:
            page = int(data.split("_")[-1])
        except (ValueError, IndexError):
            await query.answer("无效的页码。", show_alert=True)
            return

        message, keyboard = _build_rss_list_view(context.application, page)
        await query.edit_message_text(message, reply_markup=keyboard)
    
    elif data.startswith("panel_rss_feed_"):
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return

        token = data.split("_")[-1]
        ref = _resolve_rss_reference(context.application, token, "feed")
        if not ref:
            await query.answer("未找到订阅引用，请重新打开列表。", show_alert=True)
            return

        chat_id = str(ref["chat_id"])
        feed_url = ref["feed_url"]
        message, keyboard = _build_rss_feed_detail(context.application, chat_id, feed_url)
        if not message:
            await query.answer("订阅不存在或已被移除。", show_alert=True)
            message, keyboard = _build_rss_list_view(context.application, 1)
        await query.edit_message_text(message, reply_markup=keyboard)
    
    elif data.startswith("panel_rss_remove_"):
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return

        token = data.split("_")[-1]
        ref = _resolve_rss_reference(context.application, token, "feed")
        if not ref:
            await query.answer("未找到订阅引用。", show_alert=True)
            return

        chat_id = str(ref["chat_id"])
        feed_url = ref["feed_url"]
        data_file = context.application.bot_data.get("rss_data_file", config.RSS_DATA_FILE)
        success = rss_data_manager.remove_feed(chat_id, feed_url, data_file)
        if success:
            await query.answer("订阅已移除。", show_alert=True)
        else:
            await query.answer("订阅不存在。", show_alert=True)

        message, keyboard = _build_rss_list_view(context.application, 1)
        await query.edit_message_text(message, reply_markup=keyboard)
    
    elif data.startswith("panel_rss_kwrm_"):
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return

        token = data.split("_")[-1]
        ref = _resolve_rss_reference(context.application, token, "keyword")
        if not ref:
            await query.answer("未找到关键词引用。", show_alert=True)
            return

        chat_id = str(ref["chat_id"])
        feed_url = ref["feed_url"]
        keyword = ref["keyword"]
        data_file = context.application.bot_data.get("rss_data_file", config.RSS_DATA_FILE)
        success = rss_data_manager.remove_keyword(chat_id, feed_url, keyword, data_file)
        if success:
            await query.answer(f"已移除关键词: {keyword}", show_alert=True)
        else:
            await query.answer("关键词不存在。", show_alert=True)

        message, keyboard = _build_rss_feed_detail(context.application, chat_id, feed_url)
        if not message:
            message, keyboard = _build_rss_list_view(context.application, 1)
        await query.edit_message_text(message, reply_markup=keyboard)
    
    elif data == "panel_autoreply_toggle":
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        is_enabled = await db.get_autoreply_enabled()
        await db.set_autoreply_enabled(not is_enabled)
        new_status = "已启用" if not is_enabled else "已禁用"
        await query.answer(f"自动回复已{new_status}", show_alert=True)
        
        is_enabled = await db.get_autoreply_enabled()
        status_text = "已启用" if is_enabled else "已禁用"
        
        message = (
            f"自动回复管理\n\n"
            f"当前状态: {status_text}\n\n"
            f"请选择操作："
        )
        
        keyboard = [
            [
                InlineKeyboardButton(
                    "关闭自动回复" if is_enabled else "开启自动回复",
                    callback_data="panel_autoreply_toggle"
                )
            ],
            [InlineKeyboardButton("管理知识库", callback_data="panel_autoreply_kb_list_page_1")],
            [InlineKeyboardButton("添加知识条目", callback_data="panel_autoreply_kb_add")],
            [InlineKeyboardButton("返回主面板", callback_data="panel_back")],
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data.startswith("panel_autoreply_kb_list_page_"):
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[5])
        except (ValueError, IndexError):
            page = 1
        
        entries = await db.get_all_knowledge_entries()
        if not entries:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])
            await query.edit_message_text("知识库为空", reply_markup=back_keyboard)
            return
        
        MESSAGES_PER_PAGE = 5
        total_pages = (len(entries) + MESSAGES_PER_PAGE - 1) // MESSAGES_PER_PAGE
        if page < 1:
            page = 1
        elif page > total_pages:
            page = total_pages
        
        start_idx = (page - 1) * MESSAGES_PER_PAGE
        end_idx = start_idx + MESSAGES_PER_PAGE
        page_entries = entries[start_idx:end_idx]
        
        message = f"知识库条目 (第 {page}/{total_pages} 页)\n\n"
        keyboard = []
        
        for entry in page_entries:
            title = entry['title'][:30] + "..." if len(entry['title']) > 30 else entry['title']
            keyboard.append([
                InlineKeyboardButton(
                    f"{title}",
                    callback_data=f"panel_autoreply_kb_view_{entry['id']}"
                )
            ])
            keyboard.append([
                InlineKeyboardButton(
                    "编辑",
                    callback_data=f"panel_autoreply_kb_edit_{entry['id']}"
                ),
                InlineKeyboardButton(
                    "删除",
                    callback_data=f"panel_autoreply_kb_delete_{entry['id']}"
                )
            ])
        
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("上一页", callback_data=f"panel_autoreply_kb_list_page_{page-1}"))
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton("下一页", callback_data=f"panel_autoreply_kb_list_page_{page+1}"))
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        keyboard.append([InlineKeyboardButton("返回主面板", callback_data="panel_back")])
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data.startswith("panel_autoreply_kb_view_"):
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            entry_id = int(data.split("_")[4])
        except (ValueError, IndexError):
            await query.answer("无效的条目ID", show_alert=True)
            return
        
        entry = await db.get_knowledge_entry(entry_id)
        if not entry:
            await query.answer("条目不存在", show_alert=True)
            return
        
        message = (
            f"知识条目详情\n\n"
            f"ID: {entry['id']}\n"
            f"标题: {entry['title']}\n"
            f"内容: {entry['content']}\n\n"
            f"创建时间: {entry['created_at']}\n"
            f"更新时间: {entry['updated_at']}"
        )
        
        keyboard = [
            [
                InlineKeyboardButton("编辑", callback_data=f"panel_autoreply_kb_edit_{entry_id}"),
                InlineKeyboardButton("删除", callback_data=f"panel_autoreply_kb_delete_{entry_id}")
            ],
            [InlineKeyboardButton("返回列表", callback_data="panel_autoreply_kb_list_page_1")],
            [InlineKeyboardButton("返回主面板", callback_data="panel_back")],
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data.startswith("panel_autoreply_kb_edit_"):
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            entry_id = int(data.split("_")[4])
        except (ValueError, IndexError):
            await query.answer("无效的条目ID", show_alert=True)
            return
        
        entry = await db.get_knowledge_entry(entry_id)
        if not entry:
            await query.answer("条目不存在", show_alert=True)
            return
        
        back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])
        await query.edit_message_text(
            f"编辑知识条目\n\n"
            f"ID: {entry['id']}\n"
            f"标题: {entry['title']}\n"
            f"内容: {entry['content']}\n\n"
            f"请使用以下格式发送编辑命令：\n"
            f"`/autoreply edit {entry_id} <新标题> <新内容>`\n\n"
            f"示例：\n"
            f"`/autoreply edit {entry_id} 新标题 新内容`",
            parse_mode='Markdown',
            reply_markup=back_keyboard
        )
    
    elif data.startswith("panel_autoreply_kb_delete_"):
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            entry_id = int(data.split("_")[4])
        except (ValueError, IndexError):
            await query.answer("无效的条目ID", show_alert=True)
            return
        
        entry = await db.get_knowledge_entry(entry_id)
        if not entry:
            await query.answer("条目不存在", show_alert=True)
            return
        
        await db.delete_knowledge_entry(entry_id)
        await query.answer(f"已删除: {entry['title']}", show_alert=True)
        
        entries = await db.get_all_knowledge_entries()
        if not entries:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])
            await query.edit_message_text("知识库为空", reply_markup=back_keyboard)
            return
        
        page = 1
        MESSAGES_PER_PAGE = 5
        total_pages = (len(entries) + MESSAGES_PER_PAGE - 1) // MESSAGES_PER_PAGE
        
        start_idx = (page - 1) * MESSAGES_PER_PAGE
        end_idx = start_idx + MESSAGES_PER_PAGE
        page_entries = entries[start_idx:end_idx]
        
        message = f"知识库条目 (第 {page}/{total_pages} 页)\n\n"
        keyboard = []
        
        for entry in page_entries:
            title = entry['title'][:30] + "..." if len(entry['title']) > 30 else entry['title']
            keyboard.append([
                InlineKeyboardButton(
                    f"{title}",
                    callback_data=f"panel_autoreply_kb_view_{entry['id']}"
                )
            ])
            keyboard.append([
                InlineKeyboardButton(
                    "编辑",
                    callback_data=f"panel_autoreply_kb_edit_{entry['id']}"
                ),
                InlineKeyboardButton(
                    "删除",
                    callback_data=f"panel_autoreply_kb_delete_{entry['id']}"
                )
            ])
        
        nav_buttons = []
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton("下一页", callback_data=f"panel_autoreply_kb_list_page_{page+1}"))
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        keyboard.append([InlineKeyboardButton("返回主面板", callback_data="panel_back")])
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "panel_autoreply_kb_add":
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])
        await query.edit_message_text(
            "添加知识条目\n\n"
            "请使用以下格式发送新条目：\n"
            "`/autoreply add <标题> <内容>`\n\n"
            "示例：\n"
            "`/autoreply add 常见问题 这是问题的答案`",
            parse_mode='Markdown',
            reply_markup=back_keyboard
        )
    
    
    elif data.startswith("unblock_"):
        from services.blacklist import verify_unblock_answer
        answer = data.split("_", 1)[1]
        message, success = await verify_unblock_answer(user_id, answer)
        
        await query.edit_message_text(text=message, reply_markup=None)
        
    elif data.startswith("admin_unblock_"):
        from services import blacklist
        
        user_id_to_unblock = int(data.split("_")[2])
        
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
            
        response = await blacklist.unblock_user(user_id_to_unblock)
        await query.answer(response, show_alert=True)

        current_page = 1
        message_text = query.message.text or ""
        reply_markup_str = str(query.message.reply_markup) if query.message.reply_markup else ""
        
        is_panel = "panel_blacklist" in reply_markup_str or "panel_stats_blacklist" in reply_markup_str
        is_stats_page = "黑名单用户列表" in message_text or "stats_list_blacklist" in reply_markup_str
        
        if "第" in message_text and "/" in message_text:
            try:
                match = re.search(r'第\s*(\d+)/', message_text)
                if match:
                    current_page = int(match.group(1))
            except:
                pass
        
        if is_panel:
            message, keyboard = await blacklist.get_blacklist_keyboard(page=current_page)
            if keyboard:
                keyboard_buttons = [list(row) for row in keyboard.inline_keyboard]
                keyboard_buttons.append([InlineKeyboardButton("返回主面板", callback_data="panel_back")])
                keyboard = InlineKeyboardMarkup(keyboard_buttons)
            else:
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        elif is_stats_page:
            message, keyboard = await blacklist.get_blacklist_keyboard_detailed(page=current_page)
            if keyboard:
                keyboard_buttons = [list(row) for row in keyboard.inline_keyboard]
                for i, row in enumerate(keyboard_buttons):
                    for j, button in enumerate(row):
                        if button.callback_data == "stats_back_to_menu":
                            keyboard_buttons[i][j] = InlineKeyboardButton("返回主面板", callback_data="panel_back")
                            break
                keyboard = InlineKeyboardMarkup(keyboard_buttons)
            else:
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            message, keyboard = await blacklist.get_blacklist_keyboard(page=current_page)
            if keyboard:
                await query.edit_message_text(
                    text=message,
                    reply_markup=keyboard,
                    parse_mode='Markdown'
                )
            else:
                await query.edit_message_text(text=message)
    
    elif data.startswith("blacklist_page_"):
        from services import blacklist
        
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[2])
        except (ValueError, IndexError):
            await query.answer("无效的页码。", show_alert=True)
            return
        
        message, keyboard = await blacklist.get_blacklist_keyboard(page=page)
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(text=message)
    
    elif data.startswith("filtered_page_"):
        from .admin_handler import _format_filtered_messages, _get_filtered_messages_keyboard
        
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[2])
        except (ValueError, IndexError):
            await query.answer("无效的页码。", show_alert=True)
            return
        
        MESSAGES_PER_PAGE = 5

        total_count = await db.get_filtered_messages_count()
        
        if total_count == 0:
            await query.edit_message_text("没有找到被过滤的消息。")
            return
        
        total_pages = (total_count + MESSAGES_PER_PAGE - 1) // MESSAGES_PER_PAGE

        if page < 1:
            page = 1
        elif page > total_pages:
            page = total_pages

        offset = (page - 1) * MESSAGES_PER_PAGE

        messages = await db.get_filtered_messages(MESSAGES_PER_PAGE, offset)
        
        if not messages:
            await query.edit_message_text("没有找到被过滤的消息。")
            return

        response = await _format_filtered_messages(messages, page, total_pages)

        keyboard = await _get_filtered_messages_keyboard(page, total_pages)

        if keyboard:
            await query.edit_message_text(response, reply_markup=keyboard)
        else:
            await query.edit_message_text(response)
    
    elif data.startswith("panel_exemptions_page_"):
        from services import blacklist
        
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[3])
        except (ValueError, IndexError):
            await query.answer("无效的页码。", show_alert=True)
            return
        
        message, keyboard = await blacklist.get_exemptions_keyboard(page=page)
        
        if keyboard:
            keyboard_buttons = [list(row) for row in keyboard.inline_keyboard]
            keyboard_buttons.append([InlineKeyboardButton("返回主面板", callback_data="panel_back")])
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
        else:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])
        
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(text=message)
    
    elif data.startswith("admin_remove_exemption_"):
        from services import blacklist
        
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            user_id_to_remove = int(data.split("_")[3])
        except (ValueError, IndexError):
            await query.answer("无效的用户ID。", show_alert=True)
            return
        
        await db.remove_exemption(user_id_to_remove)
        await query.answer(f"已移除用户 {user_id_to_remove} 的豁免", show_alert=True)
        
        current_page = 1
        message_text = query.message.text or ""
        if "第" in message_text and "/" in message_text:
            try:
                match = re.search(r'第\s*(\d+)/', message_text)
                if match:
                    current_page = int(match.group(1))
            except:
                pass
        
        message, keyboard = await blacklist.get_exemptions_keyboard(page=current_page)
        
        if keyboard:
            keyboard_buttons = [list(row) for row in keyboard.inline_keyboard]
            keyboard_buttons.append([InlineKeyboardButton("返回主面板", callback_data="panel_back")])
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
        else:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])
        
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(text=message)
    
    elif data.startswith("stats_list_all_users_page_"):
        from services.blacklist import get_all_users_keyboard
        
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[5])
        except (ValueError, IndexError):
            await query.answer("无效的页码。", show_alert=True)
            return
        
        message, keyboard = await get_all_users_keyboard(page=page)
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(text=message, parse_mode='Markdown')
    
    elif data.startswith("stats_list_blacklist_page_"):
        from services.blacklist import get_blacklist_keyboard_detailed
        
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[4])
        except (ValueError, IndexError):
            await query.answer("无效的页码。", show_alert=True)
            return
        
        message, keyboard = await get_blacklist_keyboard_detailed(page=page)
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(text=message, parse_mode='Markdown')
    
    elif data == "stats_back_to_menu":
        from .command_handler import stats
        
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        total_users = await db.get_total_users_count()
        blocked_users = await db.get_blocked_users_count()
        
        stats_message = (
            f"机器人统计数据\n"
            f"---------------------\n"
            f"总用户数: {total_users}\n"
            f"黑名单用户数: {blocked_users}\n\n"
            f"请选择要查看的列表："
        )
        
        keyboard = [
            [InlineKeyboardButton("所有用户列表", callback_data="stats_list_all_users_page_1")],
            [InlineKeyboardButton("黑名单用户列表", callback_data="stats_list_blacklist_page_1")]
        ]
        
        await query.edit_message_text(
            text=stats_message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data.startswith("autoreply_"):
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        if data == "autoreply_toggle":
            is_enabled = await db.get_autoreply_enabled()
            await db.set_autoreply_enabled(not is_enabled)
            new_status = "已启用" if not is_enabled else "已禁用"
            await query.answer(f"自动回复已{new_status}", show_alert=True)
            
            is_enabled = await db.get_autoreply_enabled()
            status_text = "已启用" if is_enabled else "已禁用"
            
            message = (
                f"自动回复管理\n\n"
                f"当前状态: {status_text}\n\n"
                f"请选择操作："
            )
            
            keyboard = [
                [
                    InlineKeyboardButton(
                        "关闭自动回复" if is_enabled else "开启自动回复",
                        callback_data="autoreply_toggle"
                    )
                ],
                [InlineKeyboardButton("管理知识库", callback_data="autoreply_kb_list_page_1")],
                [InlineKeyboardButton("添加知识条目", callback_data="autoreply_kb_add")],
            ]
            
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        
        elif data.startswith("autoreply_kb_list_page_"):
            try:
                page = int(data.split("_")[4])
            except (ValueError, IndexError):
                page = 1
            
            entries = await db.get_all_knowledge_entries()
            if not entries:
                await query.edit_message_text("知识库为空")
                return
            
            MESSAGES_PER_PAGE = 5
            total_pages = (len(entries) + MESSAGES_PER_PAGE - 1) // MESSAGES_PER_PAGE
            if page < 1:
                page = 1
            elif page > total_pages:
                page = total_pages
            
            start_idx = (page - 1) * MESSAGES_PER_PAGE
            end_idx = start_idx + MESSAGES_PER_PAGE
            page_entries = entries[start_idx:end_idx]
            
            message = f"知识库条目 (第 {page}/{total_pages} 页)\n\n"
            keyboard = []
            
            for entry in page_entries:
                title = entry['title'][:30] + "..." if len(entry['title']) > 30 else entry['title']
                keyboard.append([
                    InlineKeyboardButton(
                        f"{title}",
                        callback_data=f"autoreply_kb_view_{entry['id']}"
                    )
                ])
                keyboard.append([
                    InlineKeyboardButton(
                        "编辑",
                        callback_data=f"autoreply_kb_edit_{entry['id']}"
                    ),
                    InlineKeyboardButton(
                        "删除",
                        callback_data=f"autoreply_kb_delete_{entry['id']}"
                    )
                ])
            
            nav_buttons = []
            if page > 1:
                nav_buttons.append(InlineKeyboardButton("上一页", callback_data=f"autoreply_kb_list_page_{page-1}"))
            if page < total_pages:
                nav_buttons.append(InlineKeyboardButton("下一页", callback_data=f"autoreply_kb_list_page_{page+1}"))
            if nav_buttons:
                keyboard.append(nav_buttons)
            
            keyboard.append([InlineKeyboardButton("返回", callback_data="autoreply_back")])
            
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        
        elif data.startswith("autoreply_kb_view_"):
            try:
                entry_id = int(data.split("_")[3])
            except (ValueError, IndexError):
                await query.answer("无效的条目ID", show_alert=True)
                return
            
            entry = await db.get_knowledge_entry(entry_id)
            if not entry:
                await query.answer("条目不存在", show_alert=True)
                return
            
            message = (
                f"知识条目详情\n\n"
                f"ID: {entry['id']}\n"
                f"标题: {entry['title']}\n"
                f"内容: {entry['content']}\n\n"
                f"创建时间: {entry['created_at']}\n"
                f"更新时间: {entry['updated_at']}"
            )
            
            keyboard = [
                [
                    InlineKeyboardButton("编辑", callback_data=f"autoreply_kb_edit_{entry_id}"),
                    InlineKeyboardButton("删除", callback_data=f"autoreply_kb_delete_{entry_id}")
                ],
                [InlineKeyboardButton("返回列表", callback_data="autoreply_kb_list_page_1")]
            ]
            
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        
        elif data.startswith("autoreply_kb_edit_"):
            try:
                entry_id = int(data.split("_")[3])
            except (ValueError, IndexError):
                await query.answer("无效的条目ID", show_alert=True)
                return
            
            entry = await db.get_knowledge_entry(entry_id)
            if not entry:
                await query.answer("条目不存在", show_alert=True)
                return
            
            await query.edit_message_text(
                f"编辑知识条目\n\n"
                f"ID: {entry['id']}\n"
                f"标题: {entry['title']}\n"
                f"内容: {entry['content']}\n\n"
                f"请使用以下格式发送编辑命令：\n"
                f"`/autoreply edit {entry_id} <新标题> <新内容>`\n\n"
                f"示例：\n"
                f"`/autoreply edit {entry_id} 新标题 新内容`",
                parse_mode='Markdown'
            )
        
        elif data.startswith("autoreply_kb_delete_"):
            try:
                entry_id = int(data.split("_")[3])
            except (ValueError, IndexError):
                await query.answer("无效的条目ID", show_alert=True)
                return
            
            entry = await db.get_knowledge_entry(entry_id)
            if not entry:
                await query.answer("条目不存在", show_alert=True)
                return
            
            await db.delete_knowledge_entry(entry_id)
            await query.answer(f"已删除: {entry['title']}", show_alert=True)
            
            entries = await db.get_all_knowledge_entries()
            if not entries:
                await query.edit_message_text("知识库为空")
                return
            
            page = 1
            MESSAGES_PER_PAGE = 5
            total_pages = (len(entries) + MESSAGES_PER_PAGE - 1) // MESSAGES_PER_PAGE
            
            start_idx = (page - 1) * MESSAGES_PER_PAGE
            end_idx = start_idx + MESSAGES_PER_PAGE
            page_entries = entries[start_idx:end_idx]
            
            message = f"知识库条目 (第 {page}/{total_pages} 页)\n\n"
            keyboard = []
            
            for entry in page_entries:
                title = entry['title'][:30] + "..." if len(entry['title']) > 30 else entry['title']
                keyboard.append([
                    InlineKeyboardButton(
                        f"{title}",
                        callback_data=f"autoreply_kb_view_{entry['id']}"
                    )
                ])
                keyboard.append([
                    InlineKeyboardButton(
                        "编辑",
                        callback_data=f"autoreply_kb_edit_{entry['id']}"
                    ),
                    InlineKeyboardButton(
                        "删除",
                        callback_data=f"autoreply_kb_delete_{entry['id']}"
                    )
                ])
            
            nav_buttons = []
            if page < total_pages:
                nav_buttons.append(InlineKeyboardButton("下一页", callback_data=f"autoreply_kb_list_page_{page+1}"))
            if nav_buttons:
                keyboard.append(nav_buttons)
            
            keyboard.append([InlineKeyboardButton("返回", callback_data="autoreply_back")])
            
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        
        elif data == "autoreply_back":
            is_enabled = await db.get_autoreply_enabled()
            status_text = "已启用" if is_enabled else "已禁用"
            
            message = (
                f"自动回复管理\n\n"
                f"当前状态: {status_text}\n\n"
                f"请选择操作："
            )
            
            keyboard = [
                [
                    InlineKeyboardButton(
                        "关闭自动回复" if is_enabled else "开启自动回复",
                        callback_data="autoreply_toggle"
                    )
                ],
                [InlineKeyboardButton("管理知识库", callback_data="autoreply_kb_list_page_1")],
                [InlineKeyboardButton("添加知识条目", callback_data="autoreply_kb_add")],
            ]
            
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        
        elif data == "autoreply_kb_add":
            await query.edit_message_text(
                "添加知识条目\n\n"
                "请使用以下格式发送新条目：\n"
                "`/autoreply add <标题> <内容>`\n\n"
                "示例：\n"
                "`/autoreply add 常见问题 这是问题的答案`",
                parse_mode='Markdown'
            )
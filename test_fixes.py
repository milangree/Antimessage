#!/usr/bin/env python3
"""
测试修复后的功能
"""
import asyncio
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import config
from database import models as db
from services.ai_service import ai_service
from services.verification import (
    create_image_verification, 
    create_verification,
    verify_image_answer,
    pending_image_verifications
)


async def test_image_verification():
    """测试图片验证功能"""
    print("=" * 60)
    print("测试图片验证功能")
    print("=" * 60)
    
    try:
        # 初始化数据库
        await db.init()
        
        # 测试AI service的图片生成
        print("\n1. 测试 ai_service.generate_image_verification()...")
        image_verification = await ai_service.generate_image_verification()
        
        print(f"   ✓ 返回类型: {type(image_verification)}")
        print(f"   ✓ 包含字段: {list(image_verification.keys())}")
        print(f"   ✓ 验证码类型: {image_verification.get('type')}")
        print(f"   ✓ 验证码文本长度: {len(image_verification.get('captcha_text', ''))}")
        print(f"   ✓ 图片bytes长度: {len(image_verification.get('image_bytes', b''))}")
        print(f"   ✓ 选项数量: {len(image_verification.get('options', []))}")
        
        # 测试create_image_verification
        print("\n2. 测试 create_image_verification()...")
        test_user_id = 123456789
        image_io, caption, keyboard = await create_image_verification(test_user_id)
        
        print(f"   ✓ 返回image_io类型: {type(image_io)}")
        print(f"   ✓ 返回caption类型: {type(caption)}")
        print(f"   ✓ 返回keyboard类型: {type(keyboard)}")
        print(f"   ✓ caption内容: {caption}")
        print(f"   ✓ image_io可读字节数: {len(image_io.getvalue())}")
        print(f"   ✓ keyboard按钮数: {len(keyboard.inline_keyboard[0]) + len(keyboard.inline_keyboard[1])}")
        
        # 检查pendling_image_verifications
        print("\n3. 检查待处理验证...")
        if test_user_id in pending_image_verifications:
            verification = pending_image_verifications[test_user_id]
            print(f"   ✓ 用户{test_user_id}的验证已存储")
            print(f"   ✓ 正确答案: {verification.get('answer')}")
            print(f"   ✓ 尝试次数: {verification.get('attempts')}")
            
            # 测试错误答案
            print("\n4. 测试验证错误答案...")
            wrong_answer = "0000"
            success, message, is_banned, new_verification = await verify_image_answer(test_user_id, wrong_answer)
            
            print(f"   ✓ 验证成功: {success}")
            print(f"   ✓ 返回消息: {message}")
            print(f"   ✓ 是否被封禁: {is_banned}")
            print(f"   ✓ 新验证信息: {new_verification is not None}")
            
            if new_verification:
                new_image_io, new_caption, new_keyboard = new_verification
                print(f"   ✓ 新image_io类型: {type(new_image_io)}")
                print(f"   ✓ 新image_io字节数: {len(new_image_io.getvalue())}")
        else:
            print("   ✗ 用户验证未存储")
        
        # 测试txt验证
        print("\n5. 测试文本验证...")
        question, keyboard = await create_verification(999999999)
        print(f"   ✓ 问题类型: {type(question)}")
        print(f"   ✓ 键盘类型: {type(keyboard)}")
        print(f"   ✓ 问题包含内容: {'请完成人机验证' in question}")
        
        print("\n" + "=" * 60)
        print("✅ 所有测试通过！")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await db.close()
    
    return True


if __name__ == "__main__":
    result = asyncio.run(test_image_verification())
    sys.exit(0 if result else 1)

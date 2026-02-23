#!/usr/bin/env python3
"""
测试 JSON 消息分析功能
"""

import json
import asyncio

async def test_json_analysis():
    """测试 JSON 消息分析"""
    from services.gemini_service import gemini_service
    
    print("=" * 60)
    print("测试 JSON 消息分析功能")
    print("=" * 60)
    
    # 测试用例1: 色情广告 JSON
    test_json_spam = {
        "message": "。 4 P",
        "reply_to": {
            "quote_text": "炸裂现场\n酒店约炮欲求不满的她酒店约战4哥大汉轮流猛干 她老公还在旁边拍摄视频 现场刺激炸裂！\n\n点击下方按钮免费观看完整版🥰🥰🥰🥰"
        }
    }
    
    # 测试用例2: 正常消息 JSON
    test_json_safe = {
        "message": "你好，今天天气怎么样？",
        "reply_to": {
            "quote_text": "很好，今天天气晴朗，适合出门"
        }
    }
    
    print("\n测试 1: 色情广告 JSON")
    print("-" * 60)
    json_spam_str = json.dumps(test_json_spam, ensure_ascii=False)
    print(f"输入: {json_spam_str[:100]}...")
    
    try:
        result = await gemini_service.analyze_json_message(json_spam_str)
        print(f"结果: is_spam={result.get('is_spam')}")
        print(f"原因: {result.get('reason')}")
        
        if result.get('is_spam'):
            print("✓ 正确识别为垃圾消息")
        else:
            print("⚠️ 未能识别为垃圾消息（可能需要调整提示词）")
    except Exception as e:
        print(f"✗ 分析失败: {e}")
    
    print("\n测试 2: 正常消息 JSON")
    print("-" * 60)
    json_safe_str = json.dumps(test_json_safe, ensure_ascii=False)
    print(f"输入: {json_safe_str[:100]}...")
    
    try:
        result = await gemini_service.analyze_json_message(json_safe_str)
        print(f"结果: is_spam={result.get('is_spam')}")
        print(f"原因: {result.get('reason')}")
        
        if not result.get('is_spam'):
            print("✓ 正确识别为安全消息")
        else:
            print("⚠️ 误判为垃圾消息")
    except Exception as e:
        print(f"✗ 分析失败: {e}")
    
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(test_json_analysis())

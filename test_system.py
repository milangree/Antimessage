#!/usr/bin/env python3
"""
系统功能验证脚本
验证所有关键功能是否正确实现
"""

import sys
import asyncio

async def test_imports():
    """测试所有关键模块的导入"""
    print("=" * 50)
    print("测试模块导入...")
    print("=" * 50)
    
    modules_to_test = [
        ("database.models", "数据库模型"),
        ("database.db_manager", "数据库管理"),
        ("handlers.callback_handler", "回调处理"),
        ("handlers.command_handler", "命令处理"),
        ("services.verification", "验证服务"),
        ("services.ai_service", "AI服务"),
        ("services.gemini_service", "Gemini服务"),
    ]
    
    results = []
    for module_name, description in modules_to_test:
        try:
            __import__(module_name)
            print(f"✓ {module_name:<40} ({description})")
            results.append(True)
        except Exception as e:
            print(f"✗ {module_name:<40} 失败: {str(e)[:50]}")
            results.append(False)
    
    return all(results)

async def test_database_functions():
    """测试数据库函数是否存在"""
    print("\n" + "=" * 50)
    print("测试数据库函数...")
    print("=" * 50)
    
    from database import models as db
    
    functions_to_test = [
        ("is_blacklisted", "检查黑名单"),
        ("get_user_verification_mode", "获取验证模式"),
        ("set_user_verification_mode", "设置验证模式"),
        ("get_filtered_messages", "获取过滤消息"),
        ("get_autoreply_enabled", "获取自动回复状态"),
        ("set_autoreply_enabled", "设置自动回复状态"),
        ("get_all_exemptions", "获取豁免名单"),
        ("get_total_users_count", "获取用户总数"),
        ("get_blocked_users_count", "获取黑名单数"),
        ("is_admin", "检查管理员权限"),
    ]
    
    results = []
    for func_name, description in functions_to_test:
        if hasattr(db, func_name):
            print(f"✓ {func_name:<30} ({description})")
            results.append(True)
        else:
            print(f"✗ {func_name:<30} 缺失")
            results.append(False)
    
    return all(results)

async def test_callback_handlers():
    """验证回调处理器中的关键回调"""
    print("\n" + "=" * 50)
    print("验证回调处理器...")
    print("=" * 50)
    
    # 简单的字符串搜索验证
    with open("/workspaces/Antimessage/handlers/callback_handler.py", "r") as f:
        content = f.read()
    
    callbacks_to_verify = [
        ("menu_user", "用户菜单"),
        ("menu_admin", "管理员菜单"),
        ("cmd_getid", "获取ID命令"),
        ("cmd_verification_mode", "验证模式命令"),
        ("cmd_disable_ai_check", "AI审查设置"),
        ("cmd_blacklist", "黑名单命令"),
        ("cmd_stats", "统计信息命令"),
        ("cmd_exemptions", "豁免名单命令"),
        ("cmd_view_filtered", "查看过滤消息"),
        ("cmd_autoreply", "自动回复命令"),
        ("set_verification_image", "图片验证设置"),
        ("set_verification_text", "文本验证设置"),
        ("set_ai_check_on", "启用AI审查"),
        ("set_ai_check_off", "禁用AI审查"),
        ("panel_main", "面板主页"),
    ]
    
    results = []
    for callback, description in callbacks_to_verify:
        if f'data == "{callback}"' in content or f"data.startswith(\"{callback}" in content or f"data == '{callback}'" in content:
            print(f"✓ {callback:<25} ({description})")
            results.append(True)
        else:
            print(f"✗ {callback:<25} 未找到")
            results.append(False)
    
    return all(results)

async def test_start_command():
    """验证start命令的菜单按钮"""
    print("\n" + "=" * 50)
    print("验证start命令菜单...")
    print("=" * 50)
    
    with open("/workspaces/Antimessage/handlers/command_handler.py", "r") as f:
        content = f.read()
    
    required_strings = [
        ("menu_user", "用户菜单按钮"),
        ("menu_admin", "管理员菜单按钮"),
        ("InlineKeyboardButton", "按钮类"),
    ]
    
    results = []
    for search_str, description in required_strings:
        if search_str in content:
            print(f"✓ {search_str:<25} ({description})")
            results.append(True)
        else:
            print(f"✗ {search_str:<25} 未找到")
            results.append(False)
    
    return all(results)

async def main():
    """主测试函数"""
    print("\n")
    print("╔" + "=" * 48 + "╗")
    print("║" + " " * 12 + "Antimessage 系统功能验证" + " " * 11 + "║")
    print("╚" + "=" * 48 + "╝")
    
    test_results = []
    
    # 运行所有测试
    test_results.append(("模块导入", await test_imports()))
    test_results.append(("数据库函数", await test_database_functions()))
    test_results.append(("回调处理器", await test_callback_handlers()))
    test_results.append(("Start命令", await test_start_command()))
    
    # 总结
    print("\n" + "=" * 50)
    print("测试总结")
    print("=" * 50)
    
    passed = sum(1 for _, result in test_results if result)
    total = len(test_results)
    
    for test_name, result in test_results:
        status = "✓ 通过" if result else "✗ 失败"
        print(f"{test_name:<20} {status}")
    
    print("\n" + "-" * 50)
    print(f"总体结果: {passed}/{total} 测试通过")
    print("-" * 50)
    
    if passed == total:
        print("\n🎉 所有测试通过！系统准备就绪。")
        return 0
    else:
        print(f"\n⚠️  有 {total - passed} 个测试失败，请检查。")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

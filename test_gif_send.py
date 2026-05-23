"""
测试GIF发送效果
验证: GIF通过send_image_item发送是否显示为动画
"""

import os
import sys

# 添加路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'cowagent_src'))

def test_reply_type():
    """检查ReplyType定义"""
    print("=" * 60)
    print("测试1: 检查ReplyType定义")
    print("=" * 60)
    
    try:
        from bridge.reply import ReplyType
        
        print(f"ReplyType枚举值:")
        for rt in ReplyType:
            print(f"  {rt.name} = {rt.value}")
        
        # 检查是否有STICKER
        if hasattr(ReplyType, 'STICKER'):
            print(f"\n✅ ReplyType.STICKER已定义: {ReplyType.STICKER}")
            return True
        else:
            print(f"\n❌ ReplyType缺少STICKER")
            print(f"   当前只有: TEXT={ReplyType.TEXT}, IMAGE={ReplyType.IMAGE}, FILE={ReplyType.FILE}")
            return False
    except Exception as e:
        print(f"❌ 导入失败: {e}")
        return False


def test_weixin_api_methods():
    """检查WeixinApi支持的方法"""
    print("\n" + "=" * 60)
    print("测试2: 检查WeixinApi支持的方法")
    print("=" * 60)
    
    try:
        from channel.weixin.weixin_api import WeixinApi
        
        api = WeixinApi.__dict__
        methods = [m for m in api.keys() if not m.startswith('_') and callable(api[m])]
        
        print(f"WeixinApi公共方法 ({len(methods)}个):")
        for method in sorted(methods):
            print(f"  - {method}")
        
        # 检查是否有send_gif相关方法
        gif_methods = [m for m in methods if 'gif' in m.lower()]
        if gif_methods:
            print(f"\n✅ 发现GIF相关方法: {gif_methods}")
            return True
        else:
            print(f"\n❌ 未发现GIF相关方法")
            return False
    except Exception as e:
        print(f"❌ 检查失败: {e}")
        return False


def test_weixin_channel_send_methods():
    """检查WeixinChannel的发送方法"""
    print("\n" + "=" * 60)
    print("测试3: 检查WeixinChannel的发送方法")
    print("=" * 60)
    
    try:
        from channel.weixin.weixin_channel import WeixinChannel
        
        channel = WeixinChannel.__dict__
        methods = [m for m in channel.keys() if 'send' in m.lower() and callable(channel[m])]
        
        print(f"WeixinChannel发送相关方法:")
        for method in sorted(methods):
            print(f"  - {method}")
        
        # 检查是否有_send_sticker
        if '_send_sticker' in methods:
            print(f"\n✅ 已有_send_sticker方法")
            return True
        else:
            print(f"\n⚠️ 缺少_send_sticker方法，需要实现")
            return False
    except Exception as e:
        print(f"❌ 检查失败: {e}")
        return False


def analyze_gif_send_strategy():
    """分析GIF发送策略"""
    print("\n" + "=" * 60)
    print("测试4: 分析GIF发送策略")
    print("=" * 60)
    
    print("当前微信通道支持的消息类型:")
    print("  - TEXT (文本)")
    print("  - IMAGE (图片)")
    print("  - FILE (文件)")
    print("  - VIDEO (视频)")
    
    print("\nGIF发送的可能方案:")
    print("  方案A: 用send_image_item发送GIF")
    print("    - 优点: 复用现有代码")
    print("    - 风险: 可能只显示第一帧")
    print("    - 测试方法: 实际发送GIF到微信查看效果")
    
    print("\n  方案B: 用send_file_item发送GIF")
    print("    - 优点: 文件原样发送")
    print("    - 风险: 微信可能显示为文件而非动画")
    print("    - 测试方法: 实际发送查看效果")
    
    print("\n  方案C: 新增send_gif_item方法")
    print("    - 优点: 正确的消息类型")
    print("    - 风险: 需要确认微信协议中GIF的type值")
    print("    - 需要: 抓包或查阅文档")
    
    return True


if __name__ == "__main__":
    print("开始GIF发送能力测试...\n")
    
    results = []
    results.append(("ReplyType.STICKER", test_reply_type()))
    results.append(("WeixinApi GIF方法", test_weixin_api_methods()))
    results.append(("WeixinChannel _send_sticker", test_weixin_channel_send_methods()))
    results.append(("策略分析", analyze_gif_send_strategy()))
    
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    
    for name, result in results:
        status = "✅" if result else "❌"
        print(f"{status} {name}")
    
    print("\n结论:")
    print("当前项目不支持GIF发送，需要:")
    print("  1. 添加ReplyType.STICKER")
    print("  2. 实现WeixinChannel._send_sticker()")
    print("  3. 测试GIF发送效果（方案A或B）")
    print("  4. 根据测试结果决定是否新增API方法")

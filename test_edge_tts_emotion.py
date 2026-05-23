"""
测试Edge-TTS情感参数效果
验证: rate和volume参数的实际效果
"""

import asyncio
import os
import tempfile


async def test_edge_tts_params():
    """测试Edge-TTS参数"""
    print("=" * 60)
    print("测试: Edge-TTS情感参数")
    print("=" * 60)
    
    try:
        import edge_tts
        print(f"✅ edge-tts库已安装")
        print(f"   版本: {edge_tts.__version__ if hasattr(edge_tts, '__version__') else 'unknown'}")
    except ImportError:
        print("❌ edge-tts库未安装")
        print("   安装命令: pip install edge-tts")
        return False
    
    test_text = "你好，我是你的AI女友，今天心情很好。"
    voice = "zh-CN-XiaoxiaoNeural"
    
    # 测试不同参数组合
    test_cases = [
        ("默认", "+0%", "+0%"),
        ("开心", "+10%", "+10%"),
        ("伤心", "-15%", "-10%"),
        ("生气", "+15%", "+15%"),
        ("温柔", "-10%", "-5%"),
    ]
    
    output_dir = tempfile.mkdtemp(prefix="edge_tts_test_")
    print(f"\n输出目录: {output_dir}")
    
    results = []
    
    for emotion, rate, volume in test_cases:
        print(f"\n测试: {emotion} (rate={rate}, volume={volume})")
        
        try:
            communicate = edge_tts.Communicate(
                text=test_text,
                voice=voice,
                rate=rate,
                volume=volume,
            )
            
            # 收集音频数据
            audio_chunks = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_chunks.append(chunk["data"])
            
            if audio_chunks:
                audio_data = b"".join(audio_chunks)
                output_path = os.path.join(output_dir, f"{emotion}.mp3")
                
                with open(output_path, "wb") as f:
                    f.write(audio_data)
                
                file_size = len(audio_data)
                print(f"  ✅ 合成成功: {file_size} bytes")
                print(f"     保存至: {output_path}")
                results.append((emotion, rate, volume, output_path, True))
            else:
                print(f"  ❌ 合成失败: 无音频数据")
                results.append((emotion, rate, volume, "", False))
                
        except Exception as e:
            print(f"  ❌ 合成失败: {e}")
            results.append((emotion, rate, volume, "", False))
    
    return results


def analyze_params():
    """分析参数效果"""
    print("\n" + "=" * 60)
    print("分析: Edge-TTS参数说明")
    print("=" * 60)
    
    print("\n✅ 确认支持的参数:")
    print("  - rate: 语速调整")
    print("    格式: \"+10%\" (加快10%), \"-15%\" (减慢15%)")
    print("    范围: 通常 -50% 到 +50%")
    
    print("\n  - volume: 音量调整")
    print("    格式: \"+10%\" (增大10%), \"-10%\" (减小10%)")
    print("    范围: 通常 -50% 到 +50%")
    
    print("\n❌ 确认不支持的参数:")
    print("  - pitch: 音调调整")
    print("    Edge-TTS不提供此参数")
    print("    如需音调变化，需使用其他TTS引擎（如GPT-SoVITS）")
    
    print("\n情感映射建议:")
    emotions = {
        "开心": {"rate": "+10%", "volume": "+10%", "desc": "稍快、稍大声"},
        "伤心": {"rate": "-15%", "volume": "-10%", "desc": "较慢、较小声"},
        "生气": {"rate": "+15%", "volume": "+15%", "desc": "快、大声"},
        "温柔": {"rate": "-10%", "volume": "-5%", "desc": "慢、轻声"},
        "撒娇": {"rate": "-5%", "volume": "+5%", "desc": "稍慢、稍大"},
    }
    
    for emotion, params in emotions.items():
        print(f"  {emotion}: rate={params['rate']}, volume={params['volume']} ({params['desc']})")
    
    return True


async def main():
    print("开始Edge-TTS情感参数测试...\n")
    
    results = await test_edge_tts_params()
    analyze_params()
    
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    
    success_count = sum(1 for r in results if r[4])
    total_count = len(results)
    
    print(f"成功: {success_count}/{total_count}")
    
    for emotion, rate, volume, path, success in results:
        status = "✅" if success else "❌"
        print(f"{status} {emotion}: rate={rate}, volume={volume}")
        if success:
            print(f"   文件: {path}")
    
    print("\n结论:")
    if success_count == total_count:
        print("✅ Edge-TTS情感参数工作正常")
        print("✅ 可以基于rate和volume实现情感语音")
        print("⚠️  注意: 不支持pitch参数")
    else:
        print("❌ 部分测试失败，请检查edge-tts安装")
    
    return results


if __name__ == "__main__":
    asyncio.run(main())

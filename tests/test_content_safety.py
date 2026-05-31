"""单元测试: 内容安全模块"""
import sys

sys.path.insert(0, ".")

from security.content_safety import ContentSafetyFilter, SafetyCategory


def test_safe_text():
    checker = ContentSafetyFilter()
    result = checker.check_input("今天天气真好，我们去散步吧")
    assert result.is_safe is True
    assert result.category == SafetyCategory.NORMAL


def test_self_harm_detection():
    checker = ContentSafetyFilter()
    result = checker.check_input("我不想活了")
    assert result.is_safe is False
    assert result.category == SafetyCategory.SELF_HARM


def test_self_harm_english():
    checker = ContentSafetyFilter()
    result = checker.check_input("I want to kill myself")
    assert result.is_safe is False
    assert result.category == SafetyCategory.SELF_HARM


def test_violence_detection():
    checker = ContentSafetyFilter()
    result = checker.check_input("我要杀人")
    assert result.is_safe is False
    assert result.category == SafetyCategory.VIOLENCE


def test_porn_detection():
    checker = ContentSafetyFilter()
    result = checker.check_input("色情内容")
    assert result.is_safe is False
    assert result.category == SafetyCategory.PORNOGRAPHY


def test_normal_long_text():
    checker = ContentSafetyFilter()
    result = checker.check_input("今天工作很顺利，下午和朋友喝了咖啡，晚上看了一部电影，感觉非常放松")
    assert result.is_safe is True


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All content_safety tests passed!")

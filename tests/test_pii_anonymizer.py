"""单元测试: PII匿名化"""
import sys

sys.path.insert(0, ".")

from safety.pii_anonymizer import PIIAnonymizer


def test_no_pii():
    anon = PIIAnonymizer(enabled=True)
    text, entities = anon.anonymize("今天天气真好")
    assert text == "今天天气真好"
    assert len(entities) == 0


def test_phone_detection():
    anon = PIIAnonymizer(enabled=True)
    text, entities = anon.anonymize("我的电话是13812345678")
    assert len(entities) > 0
    assert entities[0]["type"] == "phone"
    assert "13812345678" not in text


def test_email_detection():
    anon = PIIAnonymizer(enabled=True)
    text, entities = anon.anonymize("联系邮箱test@example.com")
    assert len(entities) > 0
    assert entities[0]["type"] == "email"
    assert "test@example.com" not in text


def test_hotline_whitelist():
    anon = PIIAnonymizer(enabled=True)
    text, entities = anon.anonymize("请拨打4001619995")
    phone_entities = [e for e in entities if e["type"] == "phone"]
    assert len(phone_entities) == 0


def test_disabled():
    anon = PIIAnonymizer(enabled=False)
    text, entities = anon.anonymize("电话13812345678")
    assert text == "电话13812345678"
    assert len(entities) == 0


def test_multiple_pii():
    anon = PIIAnonymizer(enabled=True)
    text, entities = anon.anonymize("电话13812345678，邮箱test@example.com")
    assert len(entities) >= 2


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All pii_anonymizer tests passed!")

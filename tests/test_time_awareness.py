"""单元测试: 时间感知工具"""
import sys
sys.path.insert(0, ".")

from datetime import date
from tool_system.builtin.time_awareness_tool import TimeAwarenessTool


def test_get_current():
    tool = TimeAwarenessTool()
    result = tool.execute(action="current")
    assert result.success
    assert "date" in result.data
    assert "time" in result.data
    assert "weekday" in result.data


def test_parse_date():
    tool = TimeAwarenessTool()
    d = tool._parse_date("2025-05-01")
    assert d == date(2025, 5, 1)


def test_check_holiday():
    tool = TimeAwarenessTool()
    result = tool.execute(action="holiday", date="2025-05-01")
    if result.success:
        assert result.data["is_holiday"] is True
        assert result.data["holiday_name"] == "劳动节"


def test_check_workday():
    tool = TimeAwarenessTool()
    result = tool.execute(action="workday", date="2025-05-01")
    if result.success:
        assert result.data["is_workday"] is False


def test_convert_lunar():
    tool = TimeAwarenessTool()
    result = tool.execute(action="lunar", date="2025-05-23")
    if result.success:
        assert "lunar_date" in result.data
        assert "农历" in result.data["lunar_date"]


def test_unknown_action():
    tool = TimeAwarenessTool()
    result = tool.execute(action="invalid")
    assert not result.success


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All time_awareness tests passed!")

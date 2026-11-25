from typing import Any

from agent.adapters.tool_schemas import BUILTIN_SCHEMAS
from agent.domain.tool import ToolContext


class WeatherTool:
    name = "weather"
    description = "查询城市的模拟天气。可指定日期，默认今天。"
    parameters = BUILTIN_SCHEMAS[name]
    _forecasts = {"北京": "晴，18°C", "上海": "小雨，21°C", "广州": "多云，26°C", "深圳": "晴，27°C"}

    def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        city = arguments.get("city")
        date = arguments.get("date", "today")
        if not isinstance(city, str) or not city.strip():
            raise ValueError("city 需要填写城市名")
        if not isinstance(date, str) or not date.strip():
            raise ValueError("date 需要填写日期")
        city = city.strip()
        forecast = self._forecasts.get(city, "暂无该城市的模拟预报")
        return f"{city}（{date.strip()}，模拟天气）{forecast}"

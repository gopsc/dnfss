"""底部状态栏：连接状态 + 服务器地址 + 附加信息。"""
from textual.widgets import Static


class StatusBar(Static):
    def set_state(self, connected: bool, uri: str = "", extra: str = ""):
        dot = "●" if connected else "○"
        color = "green" if connected else "red"
        text = f"[{color}]{dot}[/] {'已连接' if connected else '未连接'}"
        if uri:
            text += f"  {uri}"
        if extra:
            text += f"  |  {extra}"
        self.update(text)

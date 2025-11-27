from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.message_components import Nodes
from astrbot.core.star.filter.event_message_type import EventMessageType
from .parser import BilibiliParser
import re

@register("astrbot_plugin_bilibili_bot", "drdon1234", "自动识别B站链接并转换为直链发送", "1.0")
class BilibiliBotPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.is_auto_parse = config.get("is_auto_parse", True)
        self.is_auto_pack = config.get("is_auto_pack", True)
        max_video_size_mb = config.get("max_video_size_mb", 0.0)
        self.parser = BilibiliParser(max_video_size_mb=max_video_size_mb)
        # 群组黑名单/白名单配置
        self.group_blacklist_mode = config.get("group_blacklist_mode", False)
        self.group_list = config.get("group_list", [])

    async def terminate(self):
        pass

    @filter.event_message_type(EventMessageType.ALL)
    async def auto_parse(self, event: AstrMessageEvent):
        # 获取群组ID
        group_id = event.message_obj.group_id
        
        # 群组黑名单/白名单判断
        if group_id:
            if self.group_blacklist_mode:
                # 黑名单模式：群组ID在列表中则不处理
                if group_id in self.group_list:
                    return
            else:
                # 白名单模式：群组ID不在列表中则不处理
                if self.group_list and group_id not in self.group_list:
                    return
        
        if not (self.is_auto_parse or bool(re.search(r'.?B站解析|b站解析|bilibili解析', event.message_str))):
            return
        nodes = await self.parser.build_nodes(event, self.is_auto_pack)
        if nodes is None:
            return
        if self.is_auto_pack:
            await event.send(event.chain_result([Nodes(nodes)]))
        else:
            for node in nodes:
                await event.send(event.chain_result([node]))

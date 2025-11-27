# -*- coding: utf-8 -*-
import re
import asyncio
from typing import Optional, Dict, Any, Tuple, List
from urllib.parse import urlparse, parse_qs
import aiohttp
from astrbot.api.message_components import Plain, Video, Node

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

B23_HOST = "b23.tv"
BV_RE = re.compile(r"BV[0-9A-Za-z]{10,}")
EP_PATH_RE = re.compile(r"/bangumi/play/ep(\d+)")
EP_QS_RE = re.compile(r"(?:^|[?&])ep_id=(\d+)")

class BilibiliParser:
    def __init__(self, max_video_size_mb: float = 0.0):
        self.max_video_size_mb = max_video_size_mb
        self.semaphore = asyncio.Semaphore(10)

    async def expand_b23(self, url: str, session: aiohttp.ClientSession) -> str:
        if urlparse(url).netloc.lower() == B23_HOST:
            async with session.get(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=10)) as r:
                return str(r.url)
        return url

    def extract_p(self, url: str) -> int:
        try:
            return int(parse_qs(urlparse(url).query).get("p", ["1"])[0])
        except Exception:
            return 1

    def detect_target(self, url: str) -> Tuple[Optional[str], Dict[str, str]]:
        m = EP_PATH_RE.search(url) or EP_QS_RE.search(url)
        if m:
            return "pgc", {"ep_id": m.group(1)}
        m = BV_RE.search(url)
        if m:
            return "ugc", {"bvid": m.group(0)}
        return None, {}

    # ------- 基础信息（标题/简介/作者） -------
    async def get_ugc_info(self, bvid: str, session: aiohttp.ClientSession) -> Dict[str, str]:
        api = "https://api.bilibili.com/x/web-interface/view"
        async with session.get(api, params={"bvid": bvid}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            j = await resp.json()
        if j.get("code") != 0:
            raise RuntimeError(f"view error: {j.get('code')} {j.get('message')}")
        data = j["data"]
        title = data.get("title") or ""
        desc = data.get("desc") or ""
        owner = data.get("owner") or {}
        name = owner.get("name") or ""
        mid = owner.get("mid")
        author = f"{name}(uid:{mid})" if name else ""
        return {"title": title, "desc": desc, "author": author}

    async def get_pgc_info_by_ep(self, ep_id: str, session: aiohttp.ClientSession) -> Dict[str, str]:
        api = "https://api.bilibili.com/pgc/view/web/season"
        async with session.get(api, params={"ep_id": ep_id}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            j = await resp.json()
        if j.get("code") != 0:
            raise RuntimeError(f"pgc season view error: {j.get('code')} {j.get('message')}")
        result = j.get("result") or j.get("data") or {}
        episodes = result.get("episodes") or []
        ep_obj = None
        for e in episodes:
            if str(e.get("ep_id")) == str(ep_id):
                ep_obj = e
                break
        title = ""
        if ep_obj:
            title = ep_obj.get("share_copy") or ep_obj.get("long_title") or ep_obj.get("title") or ""
        if not title:
            title = result.get("season_title") or result.get("title") or ""
        desc = result.get("evaluate") or result.get("summary") or ""
        name, mid = "", None
        up_info = result.get("up_info") or result.get("upInfo") or {}
        if isinstance(up_info, dict):
            name = up_info.get("name") or ""
            mid = up_info.get("mid") or up_info.get("uid")
        if not name:
            pub = result.get("publisher") or {}
            name = pub.get("name") or ""
            mid = pub.get("mid") or mid
        author = f"{name}({mid})" if name else (result.get("season_title") or result.get("title") or "")
        return {"title": title, "desc": desc, "author": author}

    # ------- 分P与取流 -------
    async def get_pagelist(self, bvid: str, session: aiohttp.ClientSession):
        api = "https://api.bilibili.com/x/player/pagelist"
        async with session.get(api, params={"bvid": bvid, "jsonp": "json"}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            j = await resp.json()
        if j.get("code") != 0:
            raise RuntimeError(f"pagelist error: {j.get('code')} {j.get('message')}")
        return j["data"]

    async def ugc_playurl(self, bvid: str, cid: int, qn: int, fnval: int, referer: str, session: aiohttp.ClientSession):
        api = "https://api.bilibili.com/x/player/playurl"
        params = {
            "bvid": bvid, "cid": cid, "qn": qn, "fnver": 0, "fnval": fnval,
            "fourk": 1, "otype": "json", "platform": "html5", "high_quality": 1
        }
        headers = {"User-Agent": UA, "Referer": referer, "Origin": "https://www.bilibili.com"}
        async with session.get(api, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            j = await resp.json()
        if j.get("code") != 0:
            raise RuntimeError(f"playurl error: {j.get('code')} {j.get('message')}")
        return j["data"]

    async def pgc_playurl_v2(self, ep_id: str, qn: int, fnval: int, referer: str, session: aiohttp.ClientSession):
        api = "https://api.bilibili.com/pgc/player/web/v2/playurl"
        params = {"ep_id": ep_id, "qn": qn, "fnver": 0, "fnval": fnval, "fourk": 1, "otype": "json"}
        headers = {"User-Agent": UA, "Referer": referer, "Origin": "https://www.bilibili.com"}
        async with session.get(api, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            j = await resp.json()
        if j.get("code") != 0:
            raise RuntimeError(f"pgc playurl v2 error: {j.get('code')} {j.get('message')}")
        return j.get("result") or j.get("data") or j

    def best_qn_from_data(self, data: Dict[str, Any]) -> Optional[int]:
        aq = data.get("accept_quality") or []
        if isinstance(aq, list) and aq:
            try:
                return max(int(x) for x in aq)
            except Exception:
                pass
        dash = data.get("dash") or {}
        if dash.get("video"):
            try:
                return max(int(v.get("id", 0)) for v in dash["video"])
            except Exception:
                pass
        return None

    def pick_best_video(self, dash_obj: Dict[str, Any]):
        vids = dash_obj.get("video") or []
        if not vids:
            return None
        return sorted(vids, key=lambda x: (x.get("id", 0), x.get("bandwidth", 0)), reverse=True)[0]

    async def get_video_size(self, video_url: str, session: aiohttp.ClientSession) -> Optional[float]:
        """获取视频文件大小(MB)"""
        try:
            async with session.head(video_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                content_length = resp.headers.get("Content-Length")
                if content_length:
                    size_bytes = int(content_length)
                    size_mb = size_bytes / (1024 * 1024)
                    return size_mb
        except Exception:
            pass
        return None

    async def parse_bilibili_minimal(self, url: str, p: Optional[int] = None, session: aiohttp.ClientSession = None) -> Optional[Dict[str, str]]:
        """解析B站链接，返回视频信息"""
        if session is None:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(headers={"User-Agent": UA}, timeout=timeout) as sess:
                return await self.parse_bilibili_minimal(url, p, sess)
        
        page_url = await self.expand_b23(url, session)
        p_index = max(1, int(p or self.extract_p(page_url)))
        vtype, ident = self.detect_target(page_url)
        if not vtype:
            return None

        FNVAL_MAX = 4048
        if vtype == "ugc":
            bvid = ident["bvid"]
            info = await self.get_ugc_info(bvid, session)
            pages = await self.get_pagelist(bvid, session)
            if p_index > len(pages):
                return None
            cid = pages[p_index - 1]["cid"]
            probe = await self.ugc_playurl(bvid, cid, qn=120, fnval=FNVAL_MAX, referer=page_url, session=session)
            target_qn = self.best_qn_from_data(probe) or probe.get("quality") or 80

            merged_try = await self.ugc_playurl(bvid, cid, qn=target_qn, fnval=0, referer=page_url, session=session)
            if merged_try.get("durl"):
                direct_url = merged_try["durl"][0].get("url")
            else:
                dash_try = await self.ugc_playurl(bvid, cid, qn=target_qn, fnval=FNVAL_MAX, referer=page_url, session=session)
                v = self.pick_best_video(dash_try.get("dash") or {})
                direct_url = (v.get("baseUrl") or v.get("base_url")) if v else ""
        else:
            ep_id = ident["ep_id"]
            info = await self.get_pgc_info_by_ep(ep_id, session)
            probe = await self.pgc_playurl_v2(ep_id, qn=120, fnval=FNVAL_MAX, referer=page_url, session=session)
            target_qn = self.best_qn_from_data(probe) or probe.get("quality") or 80

            merged_try = await self.pgc_playurl_v2(ep_id, qn=target_qn, fnval=0, referer=page_url, session=session)
            if merged_try.get("durl"):
                direct_url = merged_try["durl"][0].get("url")
            else:
                dash_try = await self.pgc_playurl_v2(ep_id, qn=target_qn, fnval=FNVAL_MAX, referer=page_url, session=session)
                v = self.pick_best_video(dash_try.get("dash") or {})
                direct_url = (v.get("baseUrl") or v.get("base_url")) if v else ""

        if not direct_url:
            return None

        # 检查视频大小
        if self.max_video_size_mb > 0:
            video_size = await self.get_video_size(direct_url, session)
            if video_size and video_size > self.max_video_size_mb:
                return None  # 视频过大，跳过

        return {
            "video_url": page_url,
            "author": info["author"],
            "title": info["title"],
            "desc": info["desc"],
            "direct_url": direct_url
        }

    @staticmethod
    def extract_bilibili_links(input_text: str) -> List[str]:
        """从文本中提取B站链接"""
        result_links = []
        # b23短链
        b23_pattern = r'https?://b23\.tv/[^\s]+'
        b23_links = re.findall(b23_pattern, input_text)
        result_links.extend(b23_links)
        # BV号链接
        bv_pattern = r'https?://(?:www\.)?bilibili\.com/(?:video|bangumi/play)/[^\s]*'
        bv_links = re.findall(bv_pattern, input_text)
        result_links.extend(bv_links)
        # 单独的BV号
        bv_standalone_pattern = r'BV[0-9A-Za-z]{10,}'
        bv_standalone = re.findall(bv_standalone_pattern, input_text)
        for bv in bv_standalone:
            if f"https://www.bilibili.com/video/{bv}" not in result_links:
                result_links.append(f"https://www.bilibili.com/video/{bv}")
        return result_links

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[Dict[str, str]]:
        """解析单个B站链接"""
        async with self.semaphore:
            try:
                return await self.parse_bilibili_minimal(url, session=session)
            except Exception as e:
                print(f"解析B站链接失败 {url}: {e}", flush=True)
                return None

    async def build_nodes(self, event, is_auto_pack: bool):
        """构建消息节点"""
        try:
            input_text = event.message_str
            urls = self.extract_bilibili_links(input_text)
            if not urls:
                return None
            
            nodes = []
            sender_name = "B站bot"
            platform = event.get_platform_name()
            sender_id = event.get_self_id()
            if platform != "wechatpadpro" and platform != "webchat" and platform != "gewechat":
                try:
                    sender_id = int(sender_id)
                except ValueError:
                    sender_id = 10000
            
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(headers={"User-Agent": UA}, timeout=timeout) as session:
                tasks = [self.parse(session, url) for url in urls]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in results:
                    if result and not isinstance(result, Exception):
                        # 构建文本节点（标题、作者、简介）
                        desc_text = f"标题：{result['title']}\n作者：{result['author']}"
                        if result.get('desc'):
                            desc_text += f"\n简介：{result['desc']}"
                        
                        if is_auto_pack:
                            text_node = Node(
                                name=sender_name,
                                uin=sender_id,
                                content=[
                                    Plain(desc_text)
                                ]
                            )
                        else:
                            text_node = Plain(desc_text)
                        nodes.append(text_node)
                        
                        # 构建视频节点
                        if is_auto_pack:
                            video_node = Node(
                                name=sender_name,
                                uin=sender_id,
                                content=[
                                    Video.fromURL(result['direct_url'])
                                ]
                            )
                        else:
                            video_node = Video.fromURL(result['direct_url'])
                        nodes.append(video_node)
            
            if not nodes:
                return None
            return nodes
        except Exception as e:
            print(f"构建节点时发生错误：{e}", flush=True)
            import traceback
            traceback.print_exc()
            return None

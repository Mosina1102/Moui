"""QQ 空间客户端：发说说 / 看自己动态。
技术路线：空间 web 协议（cookie p_skey + g_tk 签名），参照社区开源 qzone-mcp 实现。
cookie 来源：优先 NapCat get_cookies（domain=qzone.qq.com），兜底 data/qzone_cookie.txt 手动粘贴。
"""
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from src.common.logger_manager import get_logger

logger = get_logger("qzone")

COOKIE_FILE = os.path.join("data", "qzone_cookie.txt")
COOKIE_CACHE_TTL = 600  # cookie 缓存 10 分钟，避免每次请求都查 NapCat
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def calc_gtk(p_skey: str) -> int:
    """QQ空间 g_tk 签名算法（5381 hash 变体）"""
    h = 5381
    for c in p_skey:
        h += (h << 5) + ord(c)
    return h & 0x7FFFFFFF


def parse_cookies(cookie_str: str) -> Dict[str, str]:
    cookies = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


def safe_json_parse(text: str) -> dict:
    """解析 QZone 各种响应格式（jsonp 包裹/纯json）"""
    if not text or not text.strip():
        return {}
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for prefix in ("_Callback(", "_preloadCallback("):
        if text.startswith(prefix):
            inner = text[len(prefix) :]
            if inner.endswith(");"):
                inner = inner[:-2]
            elif inner.endswith(")"):
                inner = inner[:-1]
            try:
                return json.loads(inner.strip())
            except json.JSONDecodeError:
                pass
    match = re.search(r"frameElement\.callback\((\{.*\})\)", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return {}


def check_response_code(data: dict) -> Tuple[bool, str]:
    code = data.get("code") or data.get("ret") or data.get("err")
    if code is not None and code != 0 and code != "0":
        msg = data.get("message") or data.get("msg") or str(code)
        return False, f"code={code}, msg={msg}"
    return True, ""


def _cookie_from_napcat() -> str:
    """经适配器 action 端点调 NapCat get_cookies 拿 QZone 域 cookie（适配器内部自行处理异步）"""
    try:
        resp = requests.post(
            "http://127.0.0.1:8096/action",
            json={"action": "get_cookies", "params": {"domain": "qzone.qq.com"}},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.debug(f"NapCat 取 QZone cookie 失败: HTTP {resp.status_code}")
            return ""
        data = (resp.json().get("data") or {})
        cookies = data.get("data") if isinstance(data, dict) else None
        cookie_str = (cookies.get("cookies") if isinstance(cookies, dict) else "") or ""
        if cookie_str and "p_skey" in str(cookie_str):
            return str(cookie_str)
    except Exception as e:
        logger.debug(f"NapCat 取 QZone cookie 失败: {e}")
    return ""


def _cookie_from_file() -> str:
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
        # 支持直接粘贴 document.cookie 的输出
        if content and "p_skey" in content:
            return content
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.error(f"读取 qzone cookie 文件失败: {e}")
    return ""


class QZoneClient:
    def __init__(self, cookie_str: str, bot_qq: str = ""):
        self.cookie_str = cookie_str.strip()
        self.cookies = parse_cookies(self.cookie_str)
        raw_uin = self.cookies.get("uin", "")
        if raw_uin.startswith("o"):
            raw_uin = raw_uin[1:]
        self.uin = raw_uin or str(bot_qq)
        p_skey = self.cookies.get("p_skey", "") or self.cookies.get("skey", "")
        self._gtk = calc_gtk(p_skey)
        self._qzonetoken: Optional[str] = None
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": _UA,
                "Cookie": self.cookie_str,
            }
        )

    def _fetch_qzonetoken(self) -> str:
        try:
            resp = self.session.get(
                f"https://user.qzone.qq.com/{self.uin}",
                headers={"Referer": "https://qzone.qq.com/"},
                timeout=15,
            )
            m = re.search(r'window\.g_qzonetoken\s*=\s*"([^"]*)"', resp.text)
            token = (m.group(1) if m else "").strip()
            if token:
                self._qzonetoken = token
                return token
            return ""
        except Exception as e:
            logger.error(f"获取 qzonetoken 失败: {e}")
            return ""

    @property
    def qzonetoken(self) -> str:
        if self._qzonetoken is None:
            self._qzonetoken = self._fetch_qzonetoken()
        return self._qzonetoken or ""

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", 20)
        if "headers" not in kwargs:
            kwargs["headers"] = {}
        kwargs["headers"].setdefault("Origin", "https://user.qzone.qq.com")
        kwargs["headers"].setdefault("X-Requested-With", "XMLHttpRequest")
        return self.session.request(method, url, **kwargs)

    def _add_g_tk(self, params: dict) -> dict:
        if self._gtk:
            params.setdefault("g_tk", self._gtk)
        return params

    def validate_cookie(self) -> Tuple[bool, str]:
        if not self.uin:
            return False, "无法提取 QQ 号"
        if not self.cookies.get("p_skey"):
            return False, "Cookie 缺少 p_skey"
        try:
            resp = self.session.get(f"https://user.qzone.qq.com/{self.uin}", timeout=15, allow_redirects=False)
            return (True, "Cookie 有效") if resp.status_code == 200 else (False, f"HTTP {resp.status_code}")
        except Exception as e:
            return False, str(e)

    def upload_qzone_pic(self, image_path: str) -> dict:
        """上传图片到 QQ 空间图床，返回 {"pic_id", "url"}。

        走 up.qzone.qq.com 的 cgi_upload_image（multipart + base64，社区已知参数），
        响应格式历代不一（pic_0 数组 / data 平铺），用递归方式找 pic_id+url。
        """
        try:
            import base64

            from PIL import Image

            with Image.open(image_path) as img:
                width, height = img.size
            with open(image_path, "rb") as f:
                img_bytes = f.read()
            if len(img_bytes) > 8 * 1024 * 1024:
                return {"success": False, "error": "图片超过 8MB，QQ 空间传不了"}
            img_b64 = base64.b64encode(img_bytes).decode()

            url = "https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image"
            params = {"g_tk": self._gtk, "uin": self.uin}
            # base64=1 时 picture 是普通表单字段（base64 文本），不是 multipart 文件部分——
            # 用 files 发会被服务端报 parse form error（-21003）
            data = {
                "from": "1",
                "uploadtype": "1",
                "hd_width": str(width),
                "hd_height": str(height),
                "hd_length": str(len(img_bytes)),
                "base64": "1",
                "filepath": os.path.basename(image_path),
                "picture": img_b64,
            }
            resp = self._request(
                "POST",
                url,
                params=params,
                data=data,
                headers={"Referer": f"https://user.qzone.qq.com/{self.uin}"},
            )
            result = safe_json_parse(resp.text)
            if not result:
                logger.error(f"[图床] 响应解析失败，原文前300字: {resp.text[:300]}")
                return {"success": False, "error": "图床上传响应解析失败（可能 cookie 过期）"}
            ok, err = check_response_code(result)
            if not ok:
                logger.error(f"[图床] 上传失败: {err}，响应前300字: {resp.text[:300]}")
                return {"success": False, "error": f"图床上传失败: {err}"}

            def _find_pic(node: Any) -> Optional[Dict[str, str]]:
                """递归找第一个同时含 pic_id 和 url 的 dict"""
                if isinstance(node, dict):
                    pic_id = node.get("pic_id") or node.get("lloc")
                    pic_url = node.get("url") or node.get("pic_url")
                    if pic_id and pic_url:
                        return {"pic_id": str(pic_id), "url": str(pic_url)}
                    for v in node.values():
                        found = _find_pic(v)
                        if found:
                            return found
                elif isinstance(node, list):
                    for v in node:
                        found = _find_pic(v)
                        if found:
                            return found
                return None

            pic_info = _find_pic(result)
            if not pic_info:
                logger.error(f"[图床] 响应里找不到 pic_id，响应前300字: {resp.text[:300]}")
                return {"success": False, "error": f"图床上传响应里找不到 pic_id: {str(result)[:150]}"}
            return {"success": True, **pic_info}
        except Exception as e:
            return {"success": False, "error": f"图床上传异常: {e}"}

    def publish_mood(self, content: str, image_path: str = "") -> dict:
        """发说说；image_path 非空时先传图床再带图发。"""
        if not content or not content.strip():
            return {"success": False, "error": "内容不能为空"}
        content = content.strip()

        token = self.qzonetoken
        url = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
        params = self._add_g_tk({})
        if token:
            params["qzonetoken"] = token

        data = {
            "syn_tweet_verson": "1",
            "paramstr": "1",
            "pic_template": "",
            "richtype": "",
            "richval": "",
            "special_url": "",
            "subrichtype": "",
            "who": "1",
            "con": content,
            "feedversion": "1",
            "ver": "1",
            "ugc_right": "1",
            "to_sign": "0",
            "hostuin": self.uin,
            "code_version": "1",
            "format": "json",
            "qzreferrer": f"https://user.qzone.qq.com/{self.uin}",
        }

        if image_path:
            up = self.upload_qzone_pic(image_path)
            if not up.get("success"):
                return {"success": False, "error": f"图片上传失败: {up.get('error', '')}"}
            # 社区已知参数：pic="pic_id|url,"，pic_bo 每图"0,0"（多图逗号分隔）
            data["pic"] = f"{up['pic_id']}|{up['url']},"
            data["pic_bo"] = "0,0,"

        try:
            resp = self._request("POST", url, params=params, data=data)
            result = safe_json_parse(resp.text)
            ok, err = check_response_code(result)
            if ok:
                return {"success": True, "message": "说说发表成功", "tid": result.get("tid", ""), "content": content}
            # 未登录类错误提示更新 cookie
            if "login" in str(result).lower() or err in ("", None):
                pass
            return {"success": False, "error": f"发表失败: {err}（若提示未登录请更新空间 cookie）"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_my_moods(self, count: int = 5) -> dict:
        """拉自己最近的说说列表"""
        url = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
        params = self._add_g_tk(
            {
                "uin": self.uin,
                "hostUin": self.uin,
                "pos": 0,
                "num": max(1, min(10, count)),
                "replynum": 100,
                "code_version": 1,
                "format": "json",
                "need_comment": 0,
                "need_private_comment": 0,
            }
        )
        try:
            resp = self._request("GET", url, params=params, headers={"Referer": f"https://user.qzone.qq.com/{self.uin}"})
            data = safe_json_parse(resp.text)
            if not data:
                return {"success": False, "error": "解析响应失败（可能 cookie 过期）"}
            feed_list = data.get("msglist") or []
            if not isinstance(feed_list, list):
                feed_list = (data.get("data") or {}).get("msglist") or []
            moods = []
            for item in feed_list:
                if not isinstance(item, dict):
                    continue
                content = item.get("content", "")
                if not content:
                    conlist = item.get("conlist") or []
                    if conlist:
                        content = conlist[0].get("con", "")
                created = item.get("created_time", 0)
                time_str = time.strftime("%m-%d %H:%M", time.localtime(created)) if created else item.get("createTime", "")
                moods.append({"time": time_str, "content": str(content)[:200]})
            return {"success": True, "moods": moods}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_friend_feeds(self, friend_uins: List[str], per_friend: int = 1) -> dict:
        """刷空间：拉"认识的人"的最新说说（聚合）。

        friend_uins: 好友 QQ 号列表（由调用方从 MongoDB 私聊流提取）
        实现走已验证的 emotion_cgi_msglist_v6（同 get_my_moods，uin 换成好友号），
        对方空间受限时该人自动跳过。
        """
        feeds = []
        tried = 0
        for fuin in friend_uins:
            if tried >= 6 or len(feeds) >= per_friend * 4:
                break
            fuin = str(fuin).strip()
            if not fuin or fuin == self.uin:
                continue
            tried += 1
            url = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
            params = self._add_g_tk(
                {
                    "uin": fuin,
                    "hostUin": fuin,
                    "pos": 0,
                    "num": max(1, per_friend),
                    "replynum": 20,
                    "code_version": 1,
                    "format": "json",
                    "need_comment": 1,
                    "need_private_comment": 0,
                }
            )
            try:
                resp = self._request("GET", url, params=params, headers={"Referer": f"https://user.qzone.qq.com/{fuin}"})
                data = safe_json_parse(resp.text)
                feed_list = data.get("msglist") or []
                if not isinstance(feed_list, list):
                    feed_list = (data.get("data") or {}).get("msglist") or []
                for item in feed_list[:per_friend]:
                    if not isinstance(item, dict):
                        continue
                    content = item.get("content", "")
                    if not content:
                        conlist = item.get("conlist") or []
                        if conlist:
                            content = conlist[0].get("con", "")
                    content = str(content).strip()
                    if not content:
                        continue
                    created = item.get("created_time", 0)
                    time_str = time.strftime("%m-%d %H:%M", time.localtime(created)) if created else ""
                    # 解析评论列表（"回复其他人的回复"的原料：谁评了什么+commentid）
                    comments = []
                    clist = item.get("commentlist") or []
                    for c in clist[:5]:
                        if not isinstance(c, dict):
                            continue
                        c_content = str(c.get("content", "")).strip()
                        if not c_content:
                            continue
                        comments.append(
                            {
                                "commenter": str(c.get("nickname", "") or c.get("uin", "")),
                                "commenter_uin": str(c.get("uin", "")),
                                "content": c_content[:100],
                                "comment_id": str(c.get("commentid", "") or c.get("id", "")),
                            }
                        )
                    feeds.append(
                        {
                            "owner_uin": fuin,
                            "tid": str(item.get("tid", "")),
                            "appid": str(item.get("appid", "311")),
                            "time": time_str,
                            "content": content[:120],
                            "comments": comments,
                        }
                    )
            except Exception as e:
                logger.debug(f"拉好友({fuin})说说失败: {e}")
                continue
        return {"success": True, "feeds": feeds, "tried": tried}

    def like_feed(self, owner_uin: str, tid: str, appid: str = "311") -> dict:
        """给好友的说说点赞（接口参数为社区已知版本，需部署机实调微调）"""
        token = self.qzonetoken
        url = "https://user.qzone.qq.com/proxy/domain/w.qzone.qq.com/cgi-bin/likes/emotion_cgi_msg_like_v6"
        params = self._add_g_tk({})
        if token:
            params["qzonetoken"] = token
        data = {
            "qzreferrer": f"https://user.qzone.qq.com/{self.uin}",
            "opuin": self.uin,
            "unikey": f"http://user.qzone.qq.com/{owner_uin}/blog/{tid}",
            "curkey": f"http://user.qzone.qq.com/{owner_uin}/blog/{tid}",
            "from": "1",
            "appid": str(appid),
            "typeid": "0",
            "abstime": str(int(time.time())),
            "fid": str(tid),
            "hostuin": str(owner_uin),
            "format": "json",
        }
        try:
            resp = self._request("POST", url, params=params, data=data)
            result = safe_json_parse(resp.text)
            ok, err = check_response_code(result)
            if ok:
                return {"success": True, "message": "点赞成功"}
            return {"success": False, "error": f"点赞失败: {err}（接口可能需要实调适配）"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def comment_feed(self, owner_uin: str, tid: str, content: str, comment_id: str = "", appid: str = "311") -> dict:
        """评论好友的说说；comment_id 非空时为"回复某条评论"（楼中楼）。

        接口参数为社区已知版本（comments/add），需部署机实调微调。
        """
        if not content or not content.strip():
            return {"success": False, "error": "评论内容不能为空"}
        token = self.qzonetoken
        url = "https://user.qzone.qq.com/proxy/domain/w.qzone.qq.com/cgi-bin/comments/add"
        params = self._add_g_tk({})
        if token:
            params["qzonetoken"] = token
        data = {
            "uin": self.uin,
            "topicid": str(tid),
            "owner": str(owner_uin),
            "hostuin": str(owner_uin),
            "appid": str(appid),
            "content": content.strip()[:200],
            "parentid": str(comment_id) if comment_id else "0",
            "format": "json",
            "qzreferrer": f"https://user.qzone.qq.com/{self.uin}",
        }
        try:
            resp = self._request("POST", url, params=params, data=data)
            result = safe_json_parse(resp.text)
            ok, err = check_response_code(result)
            if ok:
                return {"success": True, "message": "评论成功", "content": content.strip()}
            return {"success": False, "error": f"评论失败: {err}（接口可能需要实调适配）"}
        except Exception as e:
            return {"success": False, "error": str(e)}


# ---- cookie 获取 + 客户端工厂（带缓存） ----
_cached_client: Optional[QZoneClient] = None
_cached_time = 0.0


def get_qzone_client(bot_qq: str = "") -> Tuple[Optional[QZoneClient], str]:
    """获取 QZoneClient（cookie 带缓存）。失败返回 (None, 错误说明)"""
    global _cached_client, _cached_time
    if _cached_client and time.time() - _cached_time < COOKIE_CACHE_TTL:
        return _cached_client, ""

    cookie_str = _cookie_from_napcat()
    source = "NapCat"
    if not cookie_str:
        cookie_str = _cookie_from_file()
        source = "手动配置文件"
    if not cookie_str:
        return (
            None,
            "拿不到 QZone cookie：NapCat 未返回 qzone.qq.com 域票据，且 data/qzone_cookie.txt 不存在。"
            "可浏览器登录 i.qq.com 后把 document.cookie 内容粘贴到 data/qzone_cookie.txt",
        )

    client = QZoneClient(cookie_str, bot_qq=bot_qq)
    ok, msg = client.validate_cookie()
    if not ok:
        logger.warning(f"QZone cookie 校验失败（来源 {source}）: {msg}")
        return None, f"QZone cookie 无效（{msg}），请更新 data/qzone_cookie.txt"

    _cached_client = client
    _cached_time = time.time()
    logger.info(f"QZone cookie 就绪（来源 {source}，uin {client.uin}）")
    return client, ""

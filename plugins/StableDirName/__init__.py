"""
MoviePilot 插件：稳定目录名

整理入库时，如果同一个 TMDB ID 已经有对应的媒体目录，就接管本次的落点，
避免因为 TMDB 中文译名变化，把同一部剧拆成两个目录。

背景：MoviePilot 生成目标路径是纯模板渲染（app/modules/filemanager/transhandler.py
的 get_rename_path），不会检查磁盘上是否已存在该剧的目录。模板里含 {{title}}，
而它取自 TMDB 的当前中文译名，译名一变就会算出新路径、建出新目录，同一部剧于是被
拆成两个目录，Jellyfin 也会当成两部剧重复刮削。

本插件挂在 TransferRename 事件上（渲染之后、落盘之前），按 TMDB ID 找到已有目录，
再按配置决定这次怎么处理：

- reuse（默认）：复用首次定下来的目录名，把渲染结果改写成那个目录。路径从此不变，
  Jellyfin 只会识别一次，观看进度不受影响。
- update：把已有目录重命名成这次算出的最新译名。目录名始终跟随 TMDB，但每次译名
  变化都会导致路径变化，Jellyfin 会把该剧当成新条目重新识别，观看进度和收藏会丢。
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.types import ChainEventType
from app.sdk.events import Event, eventmanager
from app.sdk.logging import logger
from app.sdk.plugin import _PluginBase

# 目录名结尾的 TMDB 标记，例如 "为美好的世界献上祝福！ (2016) {tmdb-65844}"
_TMDB_SUFFIX_RE = re.compile(r"\{tmdb-(?P<id>\d+)\}\s*$")
# 从目录名里剥出译名部分，用于同步文件名
_TITLE_RE = re.compile(r"^(?P<title>.*?)\s*\(\d{4}\)\s*\{tmdb-\d+\}\s*$")


class StableDirName(_PluginBase):
    """按 TMDB ID 接管整理落点，保证一部剧只对应一个媒体目录。"""

    plugin_name = "稳定目录名"
    plugin_desc = (
        "整理入库时按 TMDB ID 定位已有的媒体目录。译名变化后，"
        "可选择复用首次的目录名（不动路径），或把目录改名跟随最新译名。"
    )
    plugin_version = "1.1.0"
    plugin_author = "llanero"
    plugin_config_prefix = "stabledirname_"
    plugin_order = 21
    auth_level = 1

    # 译名变化后的处理方式
    MODE_REUSE = "reuse"
    MODE_UPDATE = "update"

    # 媒体库目录索引的存活时间（秒）。超过后重新扫描一次。
    _INDEX_TTL = 60.0

    _enabled = False
    _root_path = ""
    _mode = MODE_REUSE
    _index: Dict[str, Tuple[float, Dict[str, str]]] = {}

    def init_plugin(self, config: Optional[Dict[str, Any]] = None) -> None:
        """生效配置信息。"""
        config = config or {}
        self._enabled = bool(config.get("enabled", False))
        self._root_path = str(config.get("root_path") or "").rstrip("/")
        mode = str(config.get("name_change_mode") or self.MODE_REUSE)
        self._mode = mode if mode in (self.MODE_REUSE, self.MODE_UPDATE) else self.MODE_REUSE
        self._index = {}
        logger.info(
            f"[{self.plugin_name}] 初始化完成：enabled={self._enabled} "
            f"模式={self._mode} root_path={self._root_path or '(跟随整理目标)'}"
        )

    def get_state(self) -> bool:
        """插件当前是否可用。"""
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "enabled", "label": "启用插件"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 9},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "root_path",
                                            "label": "媒体库根目录（一般留空）",
                                            "placeholder": "/video/media/bangumi",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "name_change_mode",
                                            "label": "TMDB 译名变化时",
                                            "items": [
                                                {
                                                    "title": "复用首次译名目录（推荐，路径不变）",
                                                    "value": "reuse",
                                                },
                                                {
                                                    "title": "把目录改名跟随最新译名",
                                                    "value": "update",
                                                },
                                            ],
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "warning",
                            "variant": "tonal",
                            "text": (
                                "「复用首次译名目录」：本次落点改写为已有目录，路径从此不再变化，"
                                "Jellyfin 只识别一次。"
                                "「跟随最新译名」：插件会把已有目录重命名成最新译名，"
                                "目录名始终紧跟 TMDB；但每次译名变化都会改变路径，"
                                "Jellyfin 会把它当作新条目重新识别，该剧的观看进度和收藏会丢失。"
                            ),
                        },
                    },
                ],
            }
        ], {"enabled": False, "root_path": "", "name_change_mode": "reuse"}

    def get_page(self) -> Optional[List[dict]]:
        return None

    def stop_service(self) -> None:
        self._index = {}

    @eventmanager.register(ChainEventType.TransferRename)
    def on_transfer_rename(self, event: Event) -> None:
        """整理路径渲染完成后的接管入口。"""
        try:
            self._rewrite(event)
        except Exception as err:
            # 插件异常绝不能影响主整理流程
            logger.error(f"[{self.plugin_name}] 处理整理事件失败：{err}")

    def _rewrite(self, event: Event) -> None:
        """按配置接管本次落点：复用已有目录名，或把已有目录改名到最新译名。"""
        if not self.get_state():
            return
        data = getattr(event, "event_data", None)
        if not data:
            return

        render_str = getattr(data, "render_str", "") or ""
        parts = render_str.split("/")
        # 至少要有"剧集目录/文件名"两层才有接管余地
        if len(parts) < 2:
            return

        tmdbid = (getattr(data, "rename_dict", None) or {}).get("tmdbid")
        if not tmdbid:
            return

        root = self._resolve_root(getattr(data, "path", None))
        if not root:
            return

        existing = self._find_dir(root, tmdbid)
        if not existing:
            # 首次整理，按模板原名建目录即可
            return
        new_dir = parts[0]
        if existing == new_dir:
            return

        if self._mode == self.MODE_UPDATE:
            if self._rename_dir(root, existing, new_dir):
                # 目录已改名，本次渲染结果原样使用即可
                return
            logger.warning(
                f"[{self.plugin_name}] tmdbid={tmdbid} 改名未成功，退回复用已有目录：{existing}"
            )

        new_title = self._title_of(new_dir)
        locked_title = self._title_of(existing)
        parts[0] = existing
        # 文件名里也带着译名，一并替换，避免目录名与文件名不一致
        if new_title and locked_title and new_title != locked_title:
            parts[1:] = [seg.replace(new_title, locked_title) for seg in parts[1:]]

        data.updated = True
        data.updated_str = "/".join(parts)
        data.source = self.plugin_name
        logger.info(
            f"[{self.plugin_name}] tmdbid={tmdbid} 复用已有目录：{new_dir} -> {existing}"
        )

    def _rename_dir(self, root: Path, old_name: str, new_name: str) -> bool:
        """把已有媒体目录改名到最新译名，成功返回 True。"""
        source = root / old_name
        target = root / new_name
        if not source.is_dir():
            return False
        if target.exists():
            # 目标已存在说明库里有重名目录，交给脚本去合并，插件不掺和
            logger.warning(f"[{self.plugin_name}] 目标目录已存在，放弃改名：{target}")
            return False
        try:
            source.rename(target)
        except OSError as err:
            logger.error(f"[{self.plugin_name}] 目录改名失败 {source} -> {target}：{err}")
            return False
        self._index.pop(str(root), None)
        logger.info(f"[{self.plugin_name}] 目录改名：{old_name} -> {new_name}（路径已变化）")
        return True

    def _resolve_root(self, path: Any) -> Optional[Path]:
        """确定扫描范围：优先本次整理给出的目标库，其次插件配置的根目录。"""
        for candidate in (path, self._root_path):
            if not candidate:
                continue
            try:
                candidate_path = Path(candidate)
            except TypeError:
                continue
            if candidate_path.is_dir():
                return candidate_path
        return None

    def _find_dir(self, root: Path, tmdbid: Any) -> Optional[str]:
        """在媒体库根目录下找出以 {tmdb-<id>} 结尾的既有目录名。"""
        cache_key = str(root)
        now = time.monotonic()
        stamp, index = self._index.get(cache_key, (0.0, {}))
        if now - stamp > self._INDEX_TTL:
            index = self._build_index(root)
            self._index[cache_key] = (now, index)
        return index.get(str(tmdbid))

    def _build_index(self, root: Path) -> Dict[str, str]:
        """一次遍历建立该库下 tmdbid -> 目录名 的映射，避免逐条扫描。"""
        index: Dict[str, str] = {}
        duplicates: Dict[str, List[str]] = {}
        try:
            for entry in root.iterdir():
                if not entry.is_dir():
                    continue
                matched = _TMDB_SUFFIX_RE.search(entry.name)
                if not matched:
                    continue
                key = matched.group("id")
                if key in index:
                    duplicates.setdefault(key, [index[key]]).append(entry.name)
                else:
                    index[key] = entry.name
        except OSError as err:
            logger.error(f"[{self.plugin_name}] 扫描目录 {root} 失败：{err}")
            return {}
        if duplicates:
            # 同一 ID 对应多个目录是历史遗留，插件只能接管其中一个，需要先合并
            logger.warning(
                f"[{self.plugin_name}] {root} 下存在同一 TMDB ID 的多个目录，"
                f"插件只能接管其中一个，建议先合并：{duplicates}"
            )
        return index

    @staticmethod
    def _title_of(dir_name: str) -> Optional[str]:
        """从 "译名 (年份) {tmdb-id}" 中剥出译名，格式不符时返回 None。"""
        matched = _TITLE_RE.match(dir_name)
        return matched.group("title").strip() if matched else None

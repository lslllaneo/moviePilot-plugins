"""
MoviePilot 插件：稳定目录名

整理入库时，如果同一个 TMDB ID 已经有对应的媒体目录，就复用那个目录名。

背景：MoviePilot 生成目标路径是纯模板渲染（app/modules/filemanager/transhandler.py
的 get_rename_path），不会检查磁盘上是否已存在该剧的目录。模板里含 {{title}}，
而它取自 TMDB 的当前中文译名，译名一变就会算出新路径、建出新目录，同一部剧于是被
拆成两个目录，Jellyfin 也会当成两部剧重复刮削。

本插件挂在 TransferRename 事件上（渲染之后、落盘之前），按 TMDB ID 找到已有目录，
把本次渲染结果改写成那个目录，从而让一个 TMDB ID 永远只对应一个目录。
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
    """复用同一 TMDB ID 的既有媒体目录名。"""

    plugin_name = "稳定目录名"
    plugin_desc = (
        "整理入库时，若同一 TMDB ID 已存在媒体目录，则复用该目录名，"
        "避免因 TMDB 中文译名变化把同一部剧拆成多个目录。"
    )
    plugin_version = "1.0.0"
    plugin_author = "llanero"
    plugin_config_prefix = "stabledirname_"
    plugin_order = 21
    auth_level = 1

    # 媒体库目录索引的存活时间（秒）。超过后重新扫描一次。
    _INDEX_TTL = 60.0

    _enabled = False
    _root_path = ""
    _index: Dict[str, Tuple[float, Dict[str, str]]] = {}

    def init_plugin(self, config: Optional[Dict[str, Any]] = None) -> None:
        """生效配置信息。"""
        config = config or {}
        self._enabled = bool(config.get("enabled", False))
        self._root_path = str(config.get("root_path") or "").rstrip("/")
        self._index = {}
        logger.info(
            f"[{self.plugin_name}] 初始化完成：enabled={self._enabled} "
            f"root_path={self._root_path or '(跟随整理目标)'}"
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
                        "component": "VAlert",
                        "props": {
                            "type": "info",
                            "variant": "tonal",
                            "text": (
                                "在整理路径渲染完成后接管：按 TMDB ID 查找已存在的目录，"
                                "命中则把本次落点改写为该目录。只改写落点，不移动任何文件。"
                                "根目录留空时会自动使用本次整理的目标目录。"
                            ),
                        },
                    },
                ],
            }
        ], {"enabled": False, "root_path": ""}

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
        """命中已有目录时，把渲染结果改写成那个目录。"""
        if not self.get_state():
            return
        data = getattr(event, "event_data", None)
        if not data:
            return

        render_str = getattr(data, "render_str", "") or ""
        parts = render_str.split("/")
        # 至少要有"剧集目录/文件名"两层才有改写余地
        if len(parts) < 2:
            return

        tmdbid = (getattr(data, "rename_dict", None) or {}).get("tmdbid")
        if not tmdbid:
            return

        root = self._resolve_root(getattr(data, "path", None))
        if not root:
            return

        existing = self._find_dir(root, tmdbid)
        if not existing or existing == parts[0]:
            return

        new_title = self._title_of(parts[0])
        locked_title = self._title_of(existing)
        parts[0] = existing
        # 文件名里也带着译名，一并替换，避免目录名与文件名不一致
        if new_title and locked_title and new_title != locked_title:
            parts[1:] = [seg.replace(new_title, locked_title) for seg in parts[1:]]

        data.updated = True
        data.updated_str = "/".join(parts)
        data.source = self.plugin_name
        logger.info(
            f"[{self.plugin_name}] tmdbid={tmdbid} 复用已有目录："
            f"{render_str.split('/')[0]} -> {existing}"
        )

    def _resolve_root(self, path: Any) -> Optional[Path]:
        """确定扫描范围：优本次整理给出的目标库，其次插件配置的根目录。"""
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
        try:
            for entry in root.iterdir():
                if not entry.is_dir():
                    continue
                matched = _TMDB_SUFFIX_RE.search(entry.name)
                if matched:
                    index[matched.group("id")] = entry.name
        except OSError as err:
            logger.error(f"[{self.plugin_name}] 扫描目录 {root} 失败：{err}")
            return {}
        return index

    @staticmethod
    def _title_of(dir_name: str) -> Optional[str]:
        """从 "译名 (年份) {tmdb-id}" 中剥出译名，格式不符时返回 None。"""
        matched = _TITLE_RE.match(dir_name)
        return matched.group("title").strip() if matched else None

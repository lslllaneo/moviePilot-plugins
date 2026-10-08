"""在 MoviePilot 容器内运行的验证脚本。

用法（宿主机执行）：

    docker exec moviepilot-v3-2 python3 -c "$(cat scripts/verify.py)"

或先把本文件复制进容器再运行：

    docker cp scripts/verify.py moviepilot-v3-2:/tmp/verify.py
    docker exec moviepilot-v3-2 python3 /tmp/verify.py

它会加载插件模块，并用真实媒体库里的目录验证「按 TMDB ID 复用目录名」的逻辑。
只读取目录、只改写字符串，不会改动任何文件。
"""

import importlib.util
import sys
from pathlib import Path

PLUGIN_FILE = sys.argv[1] if len(sys.argv) > 1 else "/config/plugins/StableDirName/__init__.py"
LIBRARY = Path("/video/media/bangumi")

spec = importlib.util.spec_from_file_location("sdn_verify", PLUGIN_FILE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Plugin = module.StableDirName
print(f"模块加载 OK -> {Plugin.plugin_name} {Plugin.plugin_version}")

print("\n--- _title_of 纯函数 ---")
for src, expect in [
    ("为美好的世界献上祝福！ (2016) {tmdb-65844}", "为美好的世界献上祝福！"),
    ("正相反的你与我 (2026) {tmdb-278043}", "正相反的你与我"),
    ("没有标记的目录", None),
    ("BanG Dream! It's MyGO!!!!! (2023) {tmdb-123456}", "BanG Dream! It's MyGO!!!!!"),
]:
    got = Plugin._title_of(src)
    print(f"  {'OK  ' if got == expect else 'FAIL'} {src!r} -> {got!r}")

# 绕过 _PluginBase.__init__，避免在验证时触碰数据库
instance = Plugin.__new__(Plugin)
instance._enabled = True
instance._root_path = ""
instance._index = {}


class Data:
    def __init__(self, render_str, tmdbid):
        self.render_str = render_str
        self.rename_dict = {"tmdbid": tmdbid}
        self.path = LIBRARY
        self.updated = False
        self.updated_str = None
        self.source = None


class FakeEvent:
    def __init__(self, data):
        self.event_data = data


def run(label, render_str, tmdbid, expect_updated):
    data = Data(render_str, tmdbid)
    instance._rewrite(FakeEvent(data))
    mark = "OK  " if data.updated == expect_updated else "FAIL"
    print(f"  {mark} {label}: updated={data.updated} (期望 {expect_updated})")
    if data.updated_str:
        print(f"        {data.updated_str}")
    return data


# 从媒体库里挑一个真实存在的带 tmdb 标记的目录做基准
sample = next((d.name for d in LIBRARY.iterdir() if d.is_dir() and "{tmdb-" in d.name), None)
if not sample:
    print("\n媒体库里没有找到 {tmdb-xxx} 目录，跳过真实用例")
    sys.exit(0)

tmdbid = int(sample.split("{tmdb-")[1].rstrip("}"))

print(f"\n--- 真实媒体库用例（基准目录：{sample}）---")
run("译名漂移，应复用目录", f"完全不同的名字 (2020) {{tmdb-{tmdbid}}}/Season 1/完全不同的名字 - S01E01.mkv", tmdbid, True)
run("名字一致，不应改写", f"{sample}/Season 1/x.mkv", tmdbid, False)
run("TMDB ID 不存在于库中", "某不存在的剧 (2020) {tmdb-99999999}/Season 1/x.mkv", 99999999, False)
run("没有 tmdbid", "某剧 (2020)/Season 1/x.mkv", None, False)

instance._enabled = False
run("插件已禁用", f"完全不同的名字 (2020) {{tmdb-{tmdbid}}}/Season 1/x.mkv", tmdbid, False)
instance._enabled = True

index = instance._index.get(str(LIBRARY), (0.0, {}))[1]
print(f"\n索引规模：{len(index)} 个 TMDB ID")

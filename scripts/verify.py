"""在 MoviePilot 容器内运行的验证脚本。

用法（宿主机执行）：

    docker cp scripts/verify.py moviepilot-v3-2:/tmp/verify.py
    docker exec moviepilot-v3-2 python3 /tmp/verify.py

分两段：

* 「真实媒体库」段只读取目录、只改写字符串，不会改动任何文件。
* 「临时目录」段用来验证 update 模式的重命名行为，全程在 /tmp 下进行。
"""

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

PLUGIN_FILE = sys.argv[1] if len(sys.argv) > 1 else "/config/plugins/StableDirName/__init__.py"
LIBRARY = Path("/video/media/bangumi")

spec = importlib.util.spec_from_file_location("sdn_verify", PLUGIN_FILE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Plugin = module.StableDirName
print(f"模块加载 OK -> {Plugin.plugin_name} {Plugin.plugin_version}")

failures = 0


def check(label, actual, expected):
    global failures
    ok = actual == expected
    if not ok:
        failures += 1
    print(f"  {'OK  ' if ok else 'FAIL'} {label}: {actual!r}" + ("" if ok else f" (期望 {expected!r})"))


print("\n--- _title_of 纯函数 ---")
for src, expect in [
    ("为美好的世界献上祝福！ (2016) {tmdb-65844}", "为美好的世界献上祝福！"),
    ("正相反的你与我 (2026) {tmdb-278043}", "正相反的你与我"),
    ("没有标记的目录", None),
    ("BanG Dream! It's MyGO!!!!! (2023) {tmdb-123456}", "BanG Dream! It's MyGO!!!!!"),
]:
    check(repr(src), Plugin._title_of(src), expect)


class Data:
    def __init__(self, render_str, tmdbid, root):
        self.render_str = render_str
        self.rename_dict = {"tmdbid": tmdbid}
        self.path = root
        self.updated = False
        self.updated_str = None
        self.source = None


class FakeEvent:
    def __init__(self, data):
        self.event_data = data


def make_plugin(mode, root_path=""):
    # 绕过 _PluginBase.__init__，避免验证时触碰数据库
    instance = Plugin.__new__(Plugin)
    instance._enabled = True
    instance._root_path = root_path
    instance._mode = mode
    instance._index = {}
    return instance


def run(instance, label, render_str, tmdbid, root, expect_updated):
    data = Data(render_str, tmdbid, root)
    instance._rewrite(FakeEvent(data))
    check(label, data.updated, expect_updated)
    if data.updated_str:
        print(f"        {data.updated_str}")
    return data


sample = next((d for d in LIBRARY.iterdir() if d.is_dir() and "{tmdb-" in d.name), None)
if sample is None:
    print("\n媒体库里没有 {tmdb-xxx} 目录，跳过真实用例")
else:
    tmdbid = int(sample.name.split("{tmdb-")[1].rstrip("}"))
    print(f"\n--- 真实媒体库（reuse 模式，只读；基准目录 {sample.name}）---")
    instance = make_plugin(Plugin.MODE_REUSE)
    run(instance, "译名漂移应复用目录",
        f"完全不同的名字 (2020) {{tmdb-{tmdbid}}}/Season 1/完全不同的名字 - S01E01.mkv",
        tmdbid, LIBRARY, True)
    run(instance, "名字一致不改写", f"{sample.name}/Season 1/x.mkv", tmdbid, LIBRARY, False)
    run(instance, "库里没有该 TMDB ID",
        "某不存在的剧 (2020) {tmdb-99999999}/Season 1/x.mkv", 99999999, LIBRARY, False)
    run(instance, "没有 tmdbid", "某剧 (2020)/Season 1/x.mkv", None, LIBRARY, False)
    instance._enabled = False
    run(instance, "插件已禁用",
        f"完全不同的名字 (2020) {{tmdb-{tmdbid}}}/Season 1/x.mkv", tmdbid, LIBRARY, False)
    instance._enabled = True
    index = instance._index.get(str(LIBRARY), (0.0, {}))[1]
    print(f"  索引规模：{len(index)} 个 TMDB ID")

print("\n--- update 模式（临时目录，会真实改名）---")
workdir = Path(tempfile.mkdtemp(prefix="sdn_"))
try:
    old_dir = workdir / "正相反的你与我 (2026) {tmdb-278043}"
    (old_dir / "Season 1").mkdir(parents=True)
    (old_dir / "Season 1" / "已有的一集.mkv").write_text("placeholder")

    instance = make_plugin(Plugin.MODE_UPDATE)
    data = run(instance, "把已有目录改名到最新译名",
               "相反的你和我 (2026) {tmdb-278043}/Season 1/相反的你和我 - S01E02.mkv",
               278043, workdir, False)
    check("旧目录已消失", old_dir.exists(), False)
    check("新目录已就位", (workdir / "相反的你和我 (2026) {tmdb-278043}").is_dir(), True)
    check("目录内原有文件随之保留",
          (workdir / "相反的你和我 (2026) {tmdb-278043}" / "Season 1" / "已有的一集.mkv").exists(),
          True)
    check("render_str 未被改写", data.updated, False)

    print("\n--- update 模式：目标已存在时放弃改名 ---")
    instance = make_plugin(Plugin.MODE_UPDATE)
    (workdir / "甲 (2026) {tmdb-999001}").mkdir()
    (workdir / "乙 (2026) {tmdb-999001}").mkdir()
    check("目标已存在时 _rename_dir 返回 False",
          instance._rename_dir(workdir, "甲 (2026) {tmdb-999001}", "乙 (2026) {tmdb-999001}"), False)
    check("源目录保持原样", (workdir / "甲 (2026) {tmdb-999001}").is_dir(), True)
    check("目标目录未被破坏", (workdir / "乙 (2026) {tmdb-999001}").is_dir(), True)
finally:
    shutil.rmtree(workdir, ignore_errors=True)
    print(f"\n临时目录已清理：{workdir}")

print(f"\n{'全部通过' if failures == 0 else f'有 {failures} 项失败'}")
sys.exit(1 if failures else 0)

# MoviePilot 插件：稳定目录名

整理入库时，如果同一个 TMDB ID 已经有对应的媒体目录，就**复用那个目录名**。

用来解决一个具体问题：**TMDB 的中文译名一变，同一部剧就被拆成两个目录。**

---

## 问题是什么

MoviePilot 生成目标路径的方式是纯 Jinja2 模板渲染：

```python
# app/modules/filemanager/transhandler.py
def get_rename_path(template_string, rename_dict, path=None, ...):
    template = Template(template_string)
    render_str = template.render(rename_dict)
    ...
    return ensure_path_within_root(path, path / render_str)
```

它**完全不检查磁盘上是否已经有这部剧的目录**。而默认模板的第一层是：

```
{{title}} ({{year}}) {tmdb-{{tmdbid}}}/Season {{season}}/...
```

`{{title}}` 取自 TMDB 的**当前中文译名**。于是：

```
第 1 周：  正相反的你与我 (2026) {tmdb-278043}/   ← 建了目录
第 6 周：  相反的你和我 (2026) {tmdb-278043}/     ← TMDB 改名，又建一个
```

结果磁盘上出现两个目录，Jellyfin 当成两部剧，各自刮削一遍，观看进度也割裂在两处。

加上 `{tmdb-{{tmdbid}}}` 只能固定 ID 那一段，**标题段照漂**。

## 插件做了什么

MoviePilot 在渲染完成后会发一个 `TransferRename` 事件，并提供了改写最终结果的出口：

```python
event = eventmanager.send_event(ChainEventType.TransferRename, event_data)
if event and event.event_data:
    if event_data.updated and event_data.updated_str:
        render_str = event_data.updated_str      # ← 插件在这里接管
```

本插件挂在这个事件上，做三件事：

1. 从渲染上下文取出 `tmdbid`
2. 在目标媒体库里查找名字以 `{tmdb-<id>}` 结尾的**已有目录**
3. 命中就整个第一层目录名替换成它，同时把文件名里的译名也换成锁定名

效果：

| 场景 | 不加插件 | 加插件 |
|---|---|---|
| 首次整理 | `正相反的你与我 (2026) {tmdb-278043}/` | 一样 |
| TMDB 改名后再整理 | `相反的你和我 (2026) {tmdb-278043}/` ← **拆成两个** | 复用 `正相反的你与我 (2026) {tmdb-278043}/` |

**一个 TMDB ID 永远对应一个目录。名字在首次定名时被锁定，之后 TMDB 怎么改都不影响。**

需要注意的是：这不是"永远使用 Bangumi 的名字"，而是"**使用第一次定下来的那个名字**"。

## 安装

把插件目录复制进 MoviePilot 的插件目录：

```bash
cp -r plugins/StableDirName /volume1/docker/moviepilot/config/plugins/
```

然后在 MoviePilot 界面 **设置 → 插件** 里找到「稳定目录名」，启用即可。

如果列表里没有出现，`docker restart moviepilot-v3-2` 让 MoviePilot 重新扫描插件目录。

## 配置

| 项 | 说明 |
|---|---|
| 启用插件 | 开关 |
| 媒体库根目录 | **一般留空**。留空时插件使用本次整理的目标目录（即"设置 → 目录"里配置的媒体库路径），这是最准确的做法。只有当自动判断失效时才需要填，且要填到具体某个库，例如 `/video/media/bangumi` |

## 验证

启用后，随便整理一集新内容，看 MoviePilot 日志里有没有插件的输出：

```
[稳定目录名] tmdbid=278043 复用已有目录：相反的你和我 (2026) {tmdb-278043} -> 正相反的你与我 (2026) {tmdb-278043}
```

没有输出说明本次没有命中（例如该剧还没有历史目录，属于首次整理，属正常）。

## 已知限制

- **不重命名已有目录**。插件只改写"这次要落到哪里"，从不改动磁盘上已存在的目录名。这是有意为之：目录一改名，路径就变，Jellyfin 会当成新条目重新刮削、观看进度归零，那就失去了意义。
- **依赖 `rename_dict` 里有 `tmdbid`**。识别必须走 TMDB；如果某次识别没拿到 TMDB ID，插件会跳过，行为退回原生。
- **依赖目录名格式**。插件的索引靠目录名结尾的 `{tmdb-<id>}` 标记。如果模板不生成这个标记，插件就找不到已有目录。
- **索引有 60 秒缓存**。新建目录后最长 60 秒才会被索引到，对"译名漂移"这种以天计的变动没有影响。
- **MoviePilot 大版本升级可能失效**。插件依赖 `TransferRename` 事件契约；该契约的字段若变化，需要跟着调整。

## 卸载

```bash
rm -rf /volume1/docker/moviepilot/config/plugins/StableDirName
docker restart moviepilot-v3-2
```

插件不改动任何媒体文件，删除后 MoviePilot 立刻回到原生行为。

## License

MIT

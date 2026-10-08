# MoviePilot 插件仓库

个人自用的 MoviePilot 插件集合。仓库本身就是标准的插件市场仓库，可以被 MoviePilot 的「在线插件」直接读取安装。

## 包含的插件

| 插件 | 版本 | 说明 | 文档 |
|---|---|---|---|
| 稳定目录名 | 1.1.0 | 整理入库时按 TMDB ID 复用既有的媒体目录，避免 TMDB 中文译名变化把同一部剧拆成两个目录 | [说明](plugins.v3/stabledirname/README.md) |

## 目录结构

```
.
├── package.v3.json                    插件索引（v3 主程序读这个）
├── plugins.v3/
│   └── stabledirname/                 插件目录，名字必须全小写
│       ├── __init__.py                插件本体
│       └── README.md                  插件说明
├── scripts/
│   └── verify_stabledirname.py        容器内验证脚本
├── LICENSE
└── README.md
```

## 新增一个插件

两条约定来自 MoviePilot 的实际实现，不是风格偏好：

1. **索引 key 用驼峰，目录名用全小写**。官方仓库里索引写 `"MediaDirWatcher"`，目录是 `plugins.v3/mediadirwatcher/`——对应 `app/runtime/extensions/plugin/catalog.py` 里的 `f"app.plugins.{source_plugin_id.lower()}"`
2. **仓库必须在 main 分支**，因为下载地址是硬编码的 `https://raw.githubusercontent.com/{user}/{repo}/main/`

步骤：

1. 新建 `plugins.v3/<小写名>/__init__.py`，里面放 `_PluginBase` 的子类
2. 在 `package.v3.json` 里加一个 key（驼峰名），填 `name` / `description` / `labels` / `version` / `author` / `level` / `release` / `system_version` / `history`

索引文件按主程序大版本选：当前是 v3，读 `package.v3.json` + `plugins.v3/`；读不到会依次降级到 `package.v2.json` / `package.json`。

## 安装

### 方式一：本地安装（最直接）

```bash
docker cp plugins.v3/stabledirname moviepilot-v3-2:/config/plugins/
```

然后在 MoviePilot 界面 **设置 → 插件** 里启用。列表里没出现就 `docker restart moviepilot-v3-2` 让它重新扫描插件目录。

注意：如果同一个插件既有手动装的、又有从市场装的，会被加载两次。改用市场安装前先删掉手动装的那份：

```bash
docker exec moviepilot-v3-2 rm -rf /config/plugins/stabledirname
```

### 方式二：在线安装

**只支持 GitHub。** MoviePilot 会直接丢弃非 GitHub 的地址——`app/domain/plugin.py` 里写死的：

```python
def normalize_plugin_market_repo_url(repo_url: str) -> str | None:
    ...
    if (parsed_url.hostname or "").lower() != "github.com":
        return None          # 非 github.com 一律忽略
    return f"https://github.com/{parts[0]}/{parts[1]}"
```

**GitHub 仓库**

1. 把本仓库推到 GitHub（必须是 `main` 分支）
2. MoviePilot → 设置 → 插件 → 插件市场地址，追加你的仓库。多个地址用逗号分隔，保留原来的默认地址：

   ```
   https://github.com/jxxghp/MoviePilot-Plugins,https://github.com/<你>/<仓库>
   ```
3. 重启后到「在线插件」里就能看到本仓库的插件

私有仓库需要额外配置 `REPO_GITHUB_TOKEN`，格式 `{user}/{repo}:ghp_xxxx`。

**Gitea 仓库**

不能直接配进插件市场地址（上面那段代码会把非 github.com 的地址返回 `None` 丢掉）。可行的做法是走本地仓库：

1. 在宿主机把仓库 clone 到一个目录，例如 `/volume1/docker/mp-plugin-repo`
2. 挂载进 MoviePilot 容器

   ```yaml
   volumes:
     - /volume1/docker/mp-plugin-repo:/plugin-repo
   ```
3. 配置 `PLUGIN_LOCAL_REPO_PATHS=/plugin-repo`
4. 重启，插件市场里就会出现

代价是**不会自动更新**——需要你自己（或加个定时任务）在宿主机 `git pull`。

## License

MIT

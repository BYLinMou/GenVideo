# 角色参考图采集（可商用）

这个项目现在提供了一个“免费可商用参考图”采集脚本，用于减少角色参考图生成成本。

## 目标

- 从 **The Met Open Access（CC0）** 拉取可商用参考图
- 下载到 `assets/character_refs/free_refs/`
- 自动记录来源和许可证元数据到 `_source_index.json`

> 建议：优先使用这些现成参考图来锁定角色外观，只让图像模型负责剧情场景变化。

---

## 脚本位置

- `backend/scripts/fetch_reference_images.py`

---

## 运行方式

在项目根目录执行：

```bash
python backend/scripts/fetch_reference_images.py --query 古风女侠 黑发剑客
```

> 提示：The Met API 通常对英文关键词命中更高，建议混合使用，如：`chinese portrait`、`hanfu woman`、`wuxia hero`。

默认行为已经偏向你要的风格：

- 只保留人物/肖像类（`--person-only` 默认开启）
- 只保留中式相关线索（`--chinese-style-only` 默认开启）
- 默认排除欧式强特征（`--exclude-european-style` 默认开启）

常用参数：

- `--limit-per-query 6`：每个关键词最多下载数量（默认 6）
- `--min-width 512 --min-height 512`：最小分辨率过滤
- `--output-dir assets/character_refs/free_refs`：输出目录
- `--exclude-european-style / --no-exclude-european-style`：是否排除欧式特征
- `--dry-run`：仅预览，不下载

示例（先预览）：

```bash
python backend/scripts/fetch_reference_images.py \
  --query 古风少女 白衣剑客 国风男主 \
  --limit-per-query 4 \
  --dry-run
```

示例（正式下载）：

```bash
python backend/scripts/fetch_reference_images.py \
  --query 古风少女 白衣剑客 国风男主 \
  --limit-per-query 4

如果你想更激进地筛“国风角色设定”，可以增加：

```bash
python backend/scripts/fetch_reference_images.py \
  --preset cn-character \
  --query 国风角色设定 仙侠女主 武侠男主 \
  --limit-per-query 6
```
```

---

## 产物说明

下载后会生成：

- 图片文件：`assets/character_refs/free_refs/met_<id>_<query>.jpg`
- 元数据索引：`assets/character_refs/free_refs/_source_index.json`

每条元数据包含：

- 来源平台与作品 ID
- 源页面 URL / 源图片 URL
- 标题、作者（若有）
- 许可证信息（CC0）
- 下载时间

---

## 许可证与合规提醒

当前脚本只抓取：

- `isPublicDomain = true`
- 有可下载图片链接

并在索引里标记：

- `license = "CC0 (The Met Open Access)"`
- `license_url = "https://www.metmuseum.org/hubs/open-access"`

即使是 CC0，也建议你保留 `_source_index.json`，便于后续审计和追溯。

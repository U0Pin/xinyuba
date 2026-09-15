---
name: converting-animation-assets
description: 处理逐帧动画素材时必用。当任务涉及替换/重置/新增/删除序列帧（动画素材、逐帧 PNG、序列帧 drawable）、把 PNG 转成 WebP、或提到 cat_idle / cat_think 等帧资源、convert_png_to_webp、convert_jobs.json 时使用。新动画加载不出来、帧序号对不上时也应触发。
---

# 转换逐帧动画素材

## 核心规则

逐帧动画素材的替换/重置/新增**一律**用 `scripts/convert_png_to_webp.py`（Pillow 无损 WebP+alpha）。不要用 cwebp 有损、在线工具或其他方式——现有帧资源全部是**无损 VP8L WebP + alpha**，混入有损帧会与现有效果不一致。

## 格式硬约束（加载端依赖，错了直接播不出来）

- 帧文件输出到 `app/src/main/res/drawable/`，命名 `<baseName>_<5位补零序号>.webp`，从 0 连续编号、**不允许有洞**。
- `FrameAnimation.kt` 的 `rememberFrameIds` 默认 `digits=5`，按 `baseName_00000` 起连续探测帧数——序号位数或连续性不符会探测失败。
- **序号位数回退链：job 的 `digit` > 顶层 `default_digit` > 源文件序号位数**。源文件常是 3 位，所以**临时 job 务必显式写 `digit: 5`**（或顶层 `default_digit: 5`），位数不足只警告不阻断。
- 加载端按序号连续探测、停在第一个缺口——**洞之后的帧会被静默丢弃**，不会报错。

## 三种用法

```powershell
python scripts/convert_png_to_webp.py                    # 批量：跑 convert_jobs.json 全部 job
python scripts/convert_png_to_webp.py --only cat_idle    # 批量：按 name 过滤（逗号分隔可多个）
python scripts/convert_png_to_webp.py <in_dir> <out_dir> # 单目录：一次性转换
```

- 批量任务文件 `scripts/convert_jobs.json`（模板 `.example`）：`name` 即 baseName，`src` 为源目录，可选 `dst`（默认 drawable）、`digit`（默认 5）、`quality`（null=无损）。
- 临时任务写独立 JSON 传 `--jobs build/xxx.json`，**不要**为一次性转换改动正式任务文件（记得给 `digit: 5`，见上）。
- 有损模式仅在明确要求时用：`--quality 1-100`（另可 `--method 0-6`，默认 6）。

## 重跑语义（脚本自带，勿手删）

批量模式下每个 job 转换前会**先删除 dst 中该 name 前缀的全部旧 `*.webp`**（正则按前缀匹配，`cat_idle` 不会误删 `cat_idle_2`）——重跑即重置，帧数增减都安全，无需手工清理旧帧。

## 常见坑

- **源目录混入多余 PNG**（预览图、封面帧等）会被一并转出成帧——转换前确认 src 目录内容，转完 `git status` 核对产物数量与命名。
- 新增动画：baseName 用用户指定的名字或 src 目录名；帧序号由脚本统一重排为 0 起、补零 `digit` 位。
- 替换/重置某动画：更新对应 job 的 `src` 后 `--only <name>` 重跑即可（帧数变了由加载端自动探测，无需改代码）。

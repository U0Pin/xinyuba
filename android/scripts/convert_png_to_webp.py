#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PNG 图片 -> WebP 批量转换（Android drawable 用）。

用法:
    # 批量模式（一行命令；任务配置在 scripts/convert_jobs.json，不入库）
    python scripts/convert_png_to_webp.py
    python scripts/convert_png_to_webp.py --only cat_idle          # 只跑 name 匹配的 job
    python scripts/convert_png_to_webp.py --jobs path/to/jobs.json # 指定别的任务文件

    # 显式目录模式（转换单个目录，不改名、不删旧帧）
    python scripts/convert_png_to_webp.py <输入目录> <输出目录> [--quality 85]

任务文件（JSON，默认 scripts/convert_jobs.json，模板见 convert_jobs.json.example）:
    {
      "default_dst": "app/src/main/res/drawable",   # 可选：job 未给 dst 时的默认目标目录
      "default_digit": 5,                           # 可选：默认序号补零位数
      "default_quality": null,                      # 可选：默认有损质量（1-100；null/省略=无损）
      "jobs": [
        {
          "name":   "cat_idle",                                 # 帧序列输出前缀，也是 --only 过滤名
          "src":    "C:/designer/export/Agent/agent_idle",      # 必填：源目录（取全部 *.png）
          "dst":    "app/src/main/res/drawable",                # 可选：缺省用 default_dst
          "digit":  5,                                          # 可选：缺省用 default_digit/源序号位数
          "quality": null                                       # 可选：缺省用 default_quality（null=无损）
        }
      ]
    }

行为:
    - 帧序列自动识别（源文件名统一为 <前缀>_<序号> 且位数一致）：
      * 输出名直接用 job 的 name 作前缀并统一重编号为 0 起（源前缀/起始序号无所谓，
        如 agent_idle_00060 -> cat_idle_00000；兼容 AE 跨 clip 全局连续编号导出）
      * 源序号相邻必须连续，中间有洞直接报错拒转（洞处动画缺帧 + rememberFrameIds()
        自动探测会停在第一个缺口，洞后的帧会被静默丢弃）
      * 转换前删除 dst 中该 name 前缀的全部旧 *.webp（重置素材语义；
        正则匹配，cat_idle 不会误删 cat_idle_2）
      * 序号位数 = job digit > default_digit > 源序号位数；非 5 位给警告
    - 非帧序列的 job 保留源文件名覆盖写出，不删旧文件、不重编号（name 仅用于 --only）
    - 全部 job 的图片丢进同一个线程池并行转换
    - 默认无损编码并保留透明通道；--quality N / job quality / default_quality 切有损
      （优先级：命令行 > job 字段 > 顶层 default_quality）

依赖: Pillow (pip install Pillow)
"""
import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.stderr.write("缺少 Pillow，请先安装: pip install Pillow\n")
    sys.exit(2)

SCRIPT_DIR = Path(__file__).resolve().parent
ANDROID_DIR = SCRIPT_DIR.parent
DEFAULT_JOBS_FILE = SCRIPT_DIR / "convert_jobs.json"
FRAME_STEM_RX = re.compile(r"^(.+?)_(\d+)$")


def resolve_path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (ANDROID_DIR / p)


def save_webp(png: Path, webp: Path, quality, method: int) -> None:
    with Image.open(png) as im:
        if quality is None:
            im.save(webp, "WEBP", lossless=True, method=method)
        else:
            im.save(webp, "WEBP", quality=quality, method=method)


# ---------------- 批量模式（JSON 任务文件） ----------------

def parse_digit(value, ctx: str):
    if value is None:
        return None
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise SystemExit(f"{ctx} 不是整数: {value!r}")
    if v <= 0:
        raise SystemExit(f"{ctx} 必须为正整数: {v}")
    return v


def parse_quality(value, ctx: str):
    if value is None:
        return None
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise SystemExit(f"{ctx} 不是整数: {value!r}")
    if not 1 <= v <= 100:
        raise SystemExit(f"{ctx} 必须在 1-100 之间: {v}")
    return v


def load_jobs(jobs_file: Path):
    if not jobs_file.is_file():
        raise SystemExit(
            f"任务文件不存在: {jobs_file}\n"
            f"复制 scripts/convert_jobs.json.example 为 scripts/convert_jobs.json 并按需修改，"
            f"或改用显式目录模式（见 -h）。"
        )
    try:
        data = json.loads(jobs_file.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"任务文件读取/解析失败: {jobs_file}: {e}")
    raw = data.get("jobs") if isinstance(data, dict) else data
    if not isinstance(raw, list) or not raw:
        raise SystemExit(f"任务文件中没有 jobs 数组: {jobs_file}")

    defaults = data if isinstance(data, dict) else {}
    default_dst = defaults.get("default_dst")
    default_digit = parse_digit(defaults.get("default_digit"), "default_digit")
    default_quality = parse_quality(defaults.get("default_quality"), "default_quality")

    jobs = []
    for i, rj in enumerate(raw):
        if not isinstance(rj, dict) or not rj.get("src"):
            raise SystemExit(f"任务文件第 {i + 1} 个 job 缺少 src 字段: {jobs_file}")
        src_dir = Path(str(rj["src"]))
        dst_raw = rj.get("dst") or default_dst
        if not dst_raw:
            raise SystemExit(f"任务文件第 {i + 1} 个 job 未给 dst，且顶层没有 default_dst")
        digit = parse_digit(rj.get("digit"), f"第 {i + 1} 个 job 的 digit") or default_digit
        # job 显式给 quality（包括 null 无损）时优先；省略则回落 default_quality
        quality = rj.get("quality") if "quality" in rj else default_quality
        quality = parse_quality(quality, f"第 {i + 1} 个 job 的 quality")
        jobs.append({
            "name": str(rj.get("name") or src_dir.name),
            "src": resolve_path(str(rj["src"])),
            "dst": resolve_path(str(dst_raw)),
            "digit": digit,
            "quality": quality,
        })
    return jobs


def plan_job(job):
    """校验单个 job（无副作用），返回 (tasks, frame_prefix)。tasks = [(png, webp)]；
    frame_prefix 非 None 表示识别为帧序列。配置/命名不合规直接抛 SystemExit。"""
    name, src, dst = job["name"], job["src"], job["dst"]
    if not src.is_dir():
        raise SystemExit(f"[{name}] 源目录不存在: {src}")
    pngs = sorted(src.glob("*.png"))
    if not pngs:
        raise SystemExit(f"[{name}] 源目录下没有 .png 文件: {src}")

    stems = [p.stem for p in pngs]
    digit = job["digit"]

    # 帧序列识别（看源文件名）：全部形如 <前缀>_<序号>，且前缀、序号位数各自一致
    ms = [FRAME_STEM_RX.match(s) for s in stems]
    frame_prefix = None
    if all(ms):
        prefixes = {m.group(1) for m in ms}
        widths = {len(m.group(2)) for m in ms}
        if len(prefixes) == 1 and len(widths) == 1:
            frame_prefix = name  # 输出前缀直接用 job name（不管源前缀是什么）
            vals = sorted(int(m.group(2)) for m in ms)
            # 相邻序号必须连续；起始值任意（兼容 AE 跨 clip 全局连续编号导出）
            missing = []
            for a, b in zip(vals, vals[1:]):
                if b - a > 1:
                    missing.append("%d..%d" % (a + 1, b - 1) if b - a > 2 else str(a + 1))
            if missing:
                raise SystemExit(
                    f"[{name}] 序号不连续（缺失: {', '.join(missing)}）；洞处动画缺帧，"
                    f"rememberFrameIds() 自动探测也会停在第一个缺口，拒绝转换"
                )
            # 重编号为 0 起，前缀换成 name（文件名排序 == 序号排序，逐一映射即可）
            eff_digit = digit if digit else next(iter(widths))
            stems = ["%s_%0*d" % (name, eff_digit, i) for i in range(len(stems))]
            if eff_digit != 5:
                print(f"[{name}] 警告: 序号位数为 {eff_digit}，"
                      f"app 端约定 5 位（rememberFrameIds 默认 digits=5）")
        elif len(prefixes) == 1 and len(widths) != 1:
            print(f"[{name}] 警告: 序号位数不一致，不按帧序列处理（不校验序号/不删旧帧/不重编号）")

    tasks = [(png, dst / (stem + ".webp")) for png, stem in zip(pngs, stems)]
    return tasks, frame_prefix


def clear_old_frames(dst: Path, prefix: str) -> int:
    frame_rx = re.compile(r"^" + re.escape(prefix) + r"_\d+\.webp$")
    old = [p for p in dst.glob(prefix + "_*.webp") if frame_rx.match(p.name)]
    for p in old:
        p.unlink()
    return len(old)


def run_batch(jobs_file: Path, only, quality, method) -> int:
    jobs = load_jobs(jobs_file)
    if only:
        keys = {s.strip() for s in only.split(",") if s.strip()}
        jobs = [j for j in jobs if j["name"] in keys]
        if not jobs:
            sys.stderr.write(f"--only 未匹配到任何 job: {sorted(keys)}\n")
            return 1

    # 阶段 1：全部 job 先校验（不产生任何副作用）
    planned = []
    seen_out = {}
    for job in jobs:
        tasks, frame_prefix = plan_job(job)
        for _, webp in tasks:
            if webp in seen_out:
                raise SystemExit(
                    f"输出冲突: {webp} 同时来自 [{seen_out[webp]}] 和 [{job['name']}]"
                )
            seen_out[webp] = job["name"]
        planned.append((job, tasks, frame_prefix))
        kind = f"帧序列(前缀 {frame_prefix}, 删旧帧)" if frame_prefix else "普通图片(不删旧帧)"
        print(f"任务 [{job['name']}]: {len(tasks)} 张 <- {job['src']} ({kind})")

    # 阶段 2：建目标目录 + 帧序列删旧帧
    for job, _, _ in planned:
        job["dst"].mkdir(parents=True, exist_ok=True)
    for job, _, frame_prefix in planned:
        if frame_prefix:
            n = clear_old_frames(job["dst"], frame_prefix)
            print(f"[{job['name']}] 删除旧帧: {n} 个 webp")

    # 阶段 3：全部图片丢进同一个线程池并行转换（--quality 命令行 > job quality）
    all_tasks = [
        (png, webp, quality if quality is not None else job["quality"])
        for job, tasks, _ in planned
        for png, webp in tasks
    ]
    errors = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {
            ex.submit(save_webp, png, webp, q, method): (png, webp)
            for png, webp, q in all_tasks
        }
        for fut, (png, webp) in futures.items():
            try:
                fut.result()
            except Exception as e:  # noqa: BLE001
                errors[webp] = f"{type(e).__name__}: {e}"

    # 阶段 4：摘要
    total_in = total_out = total_ok = total_fail = 0
    for job, tasks, _ in planned:
        ok = fail = in_bytes = out_bytes = 0
        for png, webp in tasks:
            in_bytes += png.stat().st_size
            if webp in errors or not webp.exists():
                fail += 1
            else:
                ok += 1
                out_bytes += webp.stat().st_size
        total_in += in_bytes
        total_out += out_bytes
        total_ok += ok
        total_fail += fail
        mark = "OK " if fail == 0 else "ERR"
        print(f"[{mark}] [{job['name']}]: {ok}/{len(tasks)} 张, "
              f"{in_bytes // 1024}KB -> {out_bytes // 1024}KB")
        for png, webp in tasks:
            if webp in errors:
                print(f"      失败: {png.name}: {errors[webp]}")

    print(f"完成: {len(planned)} 个 job, {total_ok} 张成功 {total_fail} 张失败, "
          f"{total_in // 1024}KB -> {total_out // 1024}KB "
          f"({total_out * 100 // max(total_in, 1)}%)")
    if total_fail:
        print("结果: 失败，重跑本脚本可重试")
        return 1
    print("结果: OK")
    return 0


# ---------------- 显式目录模式（原用法） ----------------

def run_explicit(input_dir: str, output_dir: str, quality, method) -> int:
    src = Path(input_dir)
    dst = Path(output_dir)
    if not src.is_dir():
        sys.stderr.write(f"输入目录不存在: {src}\n")
        return 1
    dst.mkdir(parents=True, exist_ok=True)
    pngs = sorted(src.glob("*.png"))
    if not pngs:
        sys.stderr.write(f"输入目录下没有 .png 文件: {src}\n")
        return 1

    tasks = [(png, dst / (png.stem + ".webp")) for png in pngs]
    errors = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {
            ex.submit(save_webp, png, webp, quality, method): (png, webp)
            for png, webp in tasks
        }
        for fut, (png, webp) in futures.items():
            try:
                fut.result()
            except Exception as e:  # noqa: BLE001
                errors[webp] = f"{type(e).__name__}: {e}"

    for png, webp in tasks:
        if webp in errors:
            print(f"{png.name} -> 失败: {errors[webp]}")
        else:
            print(f"{png.name} -> {webp.name}  "
                  f"({png.stat().st_size // 1024}KB -> {webp.stat().st_size // 1024}KB)")

    ok = len(tasks) - len(errors)
    total_in = sum(p.stat().st_size for p, _ in tasks)
    total_out = sum(w.stat().st_size for _, w in tasks if w not in errors)
    print(f"完成: {ok} 帧, {total_in // 1024}KB -> {total_out // 1024}KB "
          f"({total_out * 100 // max(total_in, 1)}%)")
    return 0 if not errors else 1


def main() -> None:
    p = argparse.ArgumentParser(
        description="PNG 图片 -> WebP（Android drawable）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="不带位置参数 = 批量模式（读取 scripts/convert_jobs.json 任务文件）。",
    )
    p.add_argument("dirs", nargs="*", metavar="[输入目录 输出目录]",
                   help="显式目录模式：转换单个目录（省略则批量模式）")
    p.add_argument("--jobs", default=str(DEFAULT_JOBS_FILE),
                   help=f"批量模式任务 JSON 文件（默认 {DEFAULT_JOBS_FILE}）")
    p.add_argument("--only", help="批量模式：只跑 name 匹配的 job，逗号分隔")
    p.add_argument("--quality", type=int, default=None,
                   help="有损质量 1-100；给出即切换有损模式（默认无损，与现有 cat_*.webp 一致）")
    p.add_argument("--method", type=int, default=6,
                   help="压缩方法 0-6（默认 6 最慢最小）")
    args = p.parse_args()

    if len(args.dirs) == 2:
        raise SystemExit(run_explicit(args.dirs[0], args.dirs[1], args.quality, args.method))
    if args.dirs:
        p.error("显式目录模式需要同时给出 <输入目录> <输出目录>；批量模式不带位置参数")
    raise SystemExit(run_batch(Path(args.jobs), args.only, args.quality, args.method))


if __name__ == "__main__":
    main()

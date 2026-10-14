"""下载 Sen1Floods11 v1.1 本阶段所需数据：Sentinel-1 S1Hand + QC LabelHand + 官方 split。

来源  : GCS 公开 bucket gs://sen1floods11（匿名 HTTP/JSON API，无需 gsutil）
版本  : v1.1
模块  : v1.1/data/flood_events/HandLabeled/{S1Hand, LabelHand}
        v1.1/splits/flood_handlabeled/{flood_train,flood_valid,flood_test}_data.csv
目标  : 保持官方相对目录结构
用法  : python scripts/download_sen1floods11.py --out E:\\floodrisk\\datasets\\Sen1Floods11
"""
import argparse
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BUCKET = "sen1floods11"
API = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o"
BASE = f"https://storage.googleapis.com/{BUCKET}/"

DATA_PREFIX = "v1.1/data/flood_events/HandLabeled"
SPLIT_PREFIX = "v1.1/splits/flood_handlabeled"
SPLIT_FILES = ["flood_train_data.csv", "flood_valid_data.csv", "flood_test_data.csv"]
WORKERS = 8


def list_objects(prefix):
    """分页列出 bucket 对象，返回 [(name, size), ...]。"""
    items = []
    token = None
    while True:
        url = f"{API}?prefix={prefix}&maxResults=1000"
        if token:
            url += f"&pageToken={token}"
        with urllib.request.urlopen(url, timeout=60) as r:
            data = json.load(r)
        for item in data.get("items", []):
            items.append((item["name"], int(item.get("size", 0))))
        token = data.get("nextPageToken")
        if not token:
            break
    return items


def fetch_one(name, dest):
    if dest.exists() and dest.stat().st_size > 0:
        return name, "skip"
    url = BASE + name
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=180) as r, open(dest, "wb") as f:
        f.write(r.read())
    return name, "ok"


def main():
    parser = argparse.ArgumentParser(description="下载 Sen1Floods11 v1.1（S1Hand + LabelHand + split）")
    parser.add_argument("--out", default=r"E:\floodrisk\datasets\Sen1Floods11", help="目标目录")
    parser.add_argument("--workers", type=int, default=WORKERS)
    args = parser.parse_args()

    out = Path(args.out)
    print("=" * 60)
    print("数据来源  : Google Cloud Storage 公开 bucket gs://sen1floods11")
    print("数据版本  : v1.1（COG 规范，最新版）")
    print(f"目标目录  : {out}")
    print("下载模块  : S1Hand（Sentinel-1 GRD，2 band VV/VH）")
    print("          LabelHand（QC 手绘标签，Int16，-1/0/1）")
    print("          splits/flood_handlabeled（官方 train/val/test）")
    print("=" * 60)

    s1 = list_objects(f"{DATA_PREFIX}/S1Hand/")
    qc = list_objects(f"{DATA_PREFIX}/LabelHand/")
    n_s1 = sum(1 for n, _ in s1 if n.endswith(".tif"))
    n_qc = sum(1 for n, _ in qc if n.endswith(".tif"))
    total_bytes = sum(s for _, s in s1) + sum(s for _, s in qc)
    print(f"S1Hand 数量 : {n_s1} 张（{total_bytes / 1024**3:.2f} GB）")
    print(f"LabelHand   : {n_qc} 张（{(total_bytes - sum(s for _, s in s1)) / 1024**3:.2f} GB）")
    print(f"预计总规模  : 约 {total_bytes / 1024**3:.2f} GB（仅本阶段模块，不含 S2/weak）")
    if not n_s1 or not n_qc:
        print("警告: bucket 中未发现数据，请检查网络/前缀")
        sys.exit(1)

    tasks = [(n, out / n) for n, _ in s1 + qc]
    for name in SPLIT_FILES:
        tasks.append((f"{SPLIT_PREFIX}/{name}", out / f"{SPLIT_PREFIX}/{name}"))

    ok = skip = fail = 0
    failed = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(fetch_one, n, d): n for n, d in tasks}
        for i, fut in enumerate(as_completed(futures), 1):
            name = futures[fut]
            try:
                _, status = fut.result()
            except Exception as e:  # 单个文件失败不中断整体
                status = "fail"
                failed.append((name, str(e)))
            if status == "ok":
                ok += 1
            elif status == "skip":
                skip += 1
            else:
                fail += 1
            if i % 50 == 0 or i == len(tasks):
                print(f"进度: {i}/{len(tasks)}  新下载 {ok}  跳过 {skip}  失败 {fail}")

    print("=" * 60)
    print(f"完成: 新下载 {ok}，跳过(已存在) {skip}，失败 {fail}")
    if failed:
        print("失败文件（不重复重试，请检查网络后重跑本脚本即可续传）:")
        for name, err in failed[:10]:
            print(f"  {name}: {err}")


if __name__ == "__main__":
    main()

"""
Convert tab-separated .txt files to .csv format.

Usage:
  # Convert a single file:
  python3 txt_to_csv.py filename.txt

  # Convert all .txt files in current folder:
  python3 txt_to_csv.py
"""
import csv
from pathlib import Path

# ==========================================
# 输入文件名（直接改这里）
# ==========================================

txt_filename = "/Users/luchia/Desktop/work/coupon/All+Listings+Report_07-02-2026.txt"

# ==========================================
# Convert TXT -> CSV
# ==========================================

txt_path = Path(txt_filename)

if not txt_path.exists():
    print(f"File not found: {txt_path}")

else:

    # 自动改成同名 csv
    csv_path = txt_path.with_suffix(".csv")

    with open(txt_path, "r", encoding="utf-8") as fin, \
         open(csv_path, "w", encoding="utf-8", newline="") as fout:

        reader = csv.reader(fin, delimiter="\t")
        writer = csv.writer(fout)

        for row in reader:
            writer.writerow(row)

    print(f"✓  {txt_path.name}  →  {csv_path.name}")
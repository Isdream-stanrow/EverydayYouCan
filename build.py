"""
每日计划机 - PyInstaller 打包脚本

运行: D:/python/python.exe build.py
输出: dist/每日计划机/ 文件夹，内含 exe + 默认配置文件

分发: 把整个 dist/每日计划机/ 文件夹压缩发给朋友即可。
"""
import sys
import shutil
from pathlib import Path

import PyInstaller.__main__


def main():
    project_root = Path(__file__).resolve().parent

    dist_dir = project_root / "dist" / "每日计划机"
    if dist_dir.exists():
        shutil.rmtree(dist_dir)

    PyInstaller.__main__.run([
        str(project_root / "study-gui" / "main.py"),
        "--name=每日计划机",
        "--onedir",
        "--windowed",
        "--noconfirm",
        "--clean",
        f"--distpath={dist_dir.parent}",
        # 隐藏导入
        "--hidden-import=yaml",
        "--hidden-import=requests",
        "--hidden-import=bs4",
        "--hidden-import=PyQt5.QtCore",
        "--hidden-import=PyQt5.QtGui",
        "--hidden-import=PyQt5.QtWidgets",
        # 打包默认配置文件
        f"--add-data={project_root / 'config.yaml'};.",
    ])

    # 复制默认配置文件到 exe 同级目录
    shutil.copy(project_root / "config.yaml", dist_dir / "config.yaml")

    # 复制 plans 目录结构和已有数据（如果存在）
    plans_dir = project_root / "plans"
    if plans_dir.exists():
        target_plans = dist_dir / "plans"
        shutil.copytree(plans_dir, target_plans)

    # 创建空的 plans/ 和 图片/ 和 记忆库/ 目录（如果不存在）
    (dist_dir / "plans").mkdir(exist_ok=True)
    (dist_dir / "图片").mkdir(exist_ok=True)
    (dist_dir / "记忆库").mkdir(exist_ok=True)

    print(f"\nDone! Output: {dist_dir}")
    print("Zip this folder, send to friends. They unzip and run 每日计划机.exe.")


if __name__ == "__main__":
    main()

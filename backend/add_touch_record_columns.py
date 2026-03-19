#!/usr/bin/env python3
"""
为touch_records表添加缺失的列
"""

import asyncio
import sqlite3
import os

def add_missing_columns():
    db_path = "./media_ops.db"
    if not os.path.exists(db_path):
        print(f"❌ 数据库文件不存在: {db_path}")
        return False

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 检查touch_records表结构
    cursor.execute("PRAGMA table_info(touch_records)")
    columns = [row[1] for row in cursor.fetchall()]
    print(f"现有列: {columns}")

    # 需要添加的列
    columns_to_add = [
        ("platform", "VARCHAR(20) DEFAULT 'bilibili'"),
        ("target_note_id", "VARCHAR(64) DEFAULT ''"),
        ("target_note_title", "VARCHAR(512) DEFAULT ''"),
        ("target_aweme_id", "VARCHAR(64) DEFAULT ''"),
    ]

    added = 0
    for col_name, col_type in columns_to_add:
        if col_name not in columns:
            try:
                cursor.execute(f"ALTER TABLE touch_records ADD COLUMN {col_name} {col_type}")
                print(f"✅ 添加列: {col_name} {col_type}")
                added += 1
            except sqlite3.OperationalError as e:
                print(f"❌ 添加列 {col_name} 失败: {e}")

    if added > 0:
        conn.commit()
        print(f"✅ 成功添加 {added} 个列")
    else:
        print("✅ 所有列都已存在，无需更新")

    # 验证表结构
    cursor.execute("PRAGMA table_info(touch_records)")
    print("\n📊 最终表结构:")
    for row in cursor.fetchall():
        print(f"  {row[1]} ({row[2]})")

    conn.close()
    return True

if __name__ == "__main__":
    print("🔧 开始更新touch_records表结构...")
    if add_missing_columns():
        print("🎉 数据库更新完成！")
    else:
        print("❌ 数据库更新失败")
import sqlite3
import os

def merge_databases(path1, path2, output_path):
    # 创建输出数据库
    conn_out = sqlite3.connect(output_path)
    cur_out = conn_out.cursor()
    
    # 连接两个源数据库
    conn1 = sqlite3.connect(path1)
    conn2 = sqlite3.connect(path2)
    
    # 获取表结构（以path1为准）
    cur1 = conn1.cursor()
    cur1.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [row[0] for row in cur1.fetchall()]
    
    for table in tables:
        # 获取表结构
        cur1.execute(f"PRAGMA table_info({table})")
        columns = cur1.fetchall()
        col_names = [col[1] for col in columns]
        col_defs = ", ".join([f"{col[1]} {col[2]}" for col in columns])
        
        # 创建表
        cur_out.execute(f"CREATE TABLE IF NOT EXISTS {table} ({col_defs})")
        
        # 获取所有列名用于INSERT
        cols = ", ".join(col_names)
        placeholders = ", ".join(["?" for _ in col_names])
        
        # 从两个数据库读取数据并用set去重
        seen = set()
        unique_rows = []
        
        for conn in [conn1, conn2]:
            cur = conn.cursor()
            try:
                cur.execute(f"SELECT {cols} FROM {table}")
                for row in cur.fetchall():
                    if row not in seen:
                        seen.add(row)
                        unique_rows.append(row)
            except sqlite3.OperationalError:
                continue  # 表不存在则跳过
        
        # 插入去重后的数据
        if unique_rows:
            cur_out.executemany(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", unique_rows)
    
    conn_out.commit()
    conn1.close()
    conn2.close()
    conn_out.close()

# 使用示例
merge_databases("models.db", "models (2).db", "merged.db")
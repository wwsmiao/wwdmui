import sqlite3

def reindex_table(db_path, table_name, id_column='id'):
    """
    重新对指定表的id列进行连续编号，不影响其他数据
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # 获取除id外的所有列名
    cur.execute(f"PRAGMA table_info({table_name})")
    columns = [col[1] for col in cur.fetchall()]
    other_cols = [c for c in columns if c != id_column]
    
    # 创建临时表
    temp_table = f"{table_name}_temp"
    cur.execute(f"CREATE TABLE {temp_table} AS SELECT * FROM {table_name}")
    
    # 清空原表
    cur.execute(f"DELETE FROM {table_name}")
    
    # 重置自增计数器（如果有的话）
    try:
        cur.execute(f"DELETE FROM sqlite_sequence WHERE name='{table_name}'")
    except:
        pass
    
    # 重新插入数据，id从1开始连续编号
    if other_cols:
        col_list = ", ".join(other_cols)
        cur.execute(f"""
            INSERT INTO {table_name} ({id_column}, {col_list})
            SELECT ROW_NUMBER() OVER (ORDER BY {id_column}) as new_id, {col_list}
            FROM {temp_table} ORDER BY {id_column}
        """)
    else:
        cur.execute(f"""
            INSERT INTO {table_name} ({id_column})
            SELECT ROW_NUMBER() OVER (ORDER BY {id_column}) as new_id
            FROM {temp_table} ORDER BY {id_column}
        """)
    
    # 删除临时表
    cur.execute(f"DROP TABLE {temp_table}")
    
    conn.commit()
    conn.close()


def reindex_table_v2(db_path, table_name, id_column='id'):
    """
    兼容旧版SQLite的写法（不使用窗口函数）
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # 获取所有列名
    cur.execute(f"PRAGMA table_info({table_name})")
    columns = [col[1] for col in cur.fetchall()]
    other_cols = [c for c in columns if c != id_column]
    
    # 创建临时表保存数据
    temp_table = f"{table_name}_temp"
    cur.execute(f"CREATE TABLE {temp_table} AS SELECT * FROM {table_name}")
    
    # 清空原表
    cur.execute(f"DELETE FROM {table_name}")
    
    # 尝试重置自增
    try:
        cur.execute(f"DELETE FROM sqlite_sequence WHERE name='{table_name}'")
    except:
        pass
    
    # 重新插入数据，手动编号
    cur_temp = conn.cursor()
    cur_temp.execute(f"SELECT * FROM {temp_table} ORDER BY {id_column}")
    
    new_id = 1
    placeholders = ", ".join(["?" for _ in columns])
    col_list = ", ".join(columns)
    
    for row in cur_temp.fetchall():
        # 替换id为新编号
        row_list = list(row)
        id_idx = columns.index(id_column)
        row_list[id_idx] = new_id
        cur.execute(f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})", row_list)
        new_id += 1
    
    cur.execute(f"DROP TABLE {temp_table}")
    
    conn.commit()
    conn.close()


# 使用示例
reindex_table("models.db", "models", "id")
# reindex_table_v2("your.db", "your_table", "id")  # 兼容旧版SQLite
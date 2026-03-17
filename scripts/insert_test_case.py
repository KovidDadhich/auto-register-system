import sqlite3
from config.settings import DB_PATH


def insertTestCase():    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
    INSERT INTO Cases (case_id, case_name, district)
    VALUES (?, ?, ?)
    """, ("2014/4585", "Sarkar - Poorti", "Jaipur"))

    conn.commit()
    conn.close()

    print("Test case inserted")
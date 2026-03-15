import sqlite3
from config.settings import DATABASE_PATH


def insertTestCase():    
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    cursor.execute("""
    INSERT INTO Cases (case_id, case_name, district)
    VALUES (?, ?, ?)
    """, ("2014/4585", "Sarkar - Poorti", "Jaipur"))

    conn.commit()
    conn.close()

    print("Test case inserted")
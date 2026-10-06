"""
Database connection factory functions.

All scripts should use these functions to get database connections
"""

import os
from dotenv import load_dotenv
import mysql.connector
from sqlalchemy import create_engine

load_dotenv()


def get_db_params():
    """Return database connection parameters from environment variables."""
    return {
        'host': os.getenv('HOST'),
        'database': os.getenv('DATABASE'),
        'user': os.getenv('USER'),
        'password': os.getenv('PASSWORD'),
        'port': os.getenv('PORT', '3306'),
    }


def get_sqlalchemy_engine(echo=False):
    """
    Create and return a SQLAlchemy engine.
    Use this when working with pandas read_sql / to_sql.

    Returns:
        sqlalchemy.Engine
    """
    p = get_db_params()
    url = f"mysql+pymysql://{p['user']}:{p['password']}@{p['host']}:{p['port']}/{p['database']}"
    return create_engine(url, echo=echo)


def get_mysql_connection():
    """
    Create and return a raw mysql.connector connection and cursor.
    Use this for INSERT/UPDATE statements with parameterized queries.

    Returns:
        (connection, cursor) tuple
    """
    p = get_db_params()
    conn = mysql.connector.connect(
        host=p['host'],
        database=p['database'],
        user=p['user'],
        password=p['password'],
    )
    cursor = conn.cursor()
    return conn, cursor

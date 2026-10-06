"""
Employee organizational directory ETL functions.

Fetches the USD department directory from the web service and
syncs employee organizational memberships into emp_orgs.

Update: 9/29/2026
No sync to website yet, using static CSV file
"""

import pandas as pd
from sqlalchemy import text
from config.settings import get_sqlalchemy_engine
from dotenv import load_dotenv

load_dotenv()

USD_DIRECTORY_URL = "http://lws.sandiego.edu/department-report/get-dirs.php"


def fetch_directory_data(url: str = USD_DIRECTORY_URL) -> pd.DataFrame:
    """
    Download the USD organizational directory CSV.

    Args:
        url: URL to the pipe-delimited directory CSV

    Returns:
        pd.DataFrame with columns: BannerID, Name, E-mail,
            Department, SubDepartment, SubUnit
    """
    return pd.read_csv(url, delimiter="|")


def expand_org_units(df: pd.DataFrame) -> pd.DataFrame:
    """
    Expand semicolon-separated Department, SubDepartment, and SubUnit
    values into one row per unit per employee.

    Args:
        df: Raw directory DataFrame from fetch_directory_data()

    Returns:
        pd.DataFrame with columns: BannerID, Name, Email, Unit
    """
    rows = []

    for _, emp in df.iterrows():
        base = {
            'BannerID': emp['BannerID'],
            'Name': emp['Name'],
            'Email': emp['E-mail'],
        }
        for field in ('Department', 'SubDepartment', 'SubUnit'):
            if pd.isna(emp[field]):
                continue
            for unit in str(emp[field]).split(';'):
                unit = unit.strip()
                if unit:
                    rows.append({**base, 'Unit': unit})

    return pd.DataFrame(rows)


def sync_emp_orgs(df_units: pd.DataFrame, connection) -> list:
    """
    Insert employee organizational memberships into emp_orgs.

    Skips rows where the banner_id is not found in emp_tbl,
    and skips duplicates that already exist in emp_orgs.

    Args:
        df_units: DataFrame from expand_org_units()
        connection: SQLAlchemy connection

    Returns:
        List of banner IDs that were not found in emp_tbl
    """
    missing = []

    for _, emp in df_units.iterrows():
        banner_id = emp['BannerID']
        name = emp['Name']
        email = emp['Email']
        unit = emp['Unit']

        lookup = pd.read_sql(
            "SELECT res_id FROM emp_tbl WHERE banner_id = '{}'".format(banner_id),
            connection
        )

        if lookup.empty:
            print(f"Banner ID {banner_id} not found for {name}.")
            missing.append(banner_id)
            continue

        res_id = lookup['res_id'].values[0]

        check = pd.read_sql(
            '''SELECT 1 FROM emp_orgs WHERE res_id = {} AND banner_id = {}
               AND full_name = "{}" AND email = "{}" AND unit = "{}"'''.format(
                res_id, banner_id, name, email, unit),
            connection
        )

        if check.empty:
            connection.execute(text(
                'INSERT INTO emp_orgs (res_id, banner_id, full_name, email, unit) '
                'VALUES ({}, "{}", "{}", "{}", "{}")'.format(
                    res_id, banner_id, name, email, unit)
            ))
            connection.commit()

    return missing

test_org_file = "new_directory2.csv"
orgmap = pd.read_csv(f"Data/{test_org_file}")

units_df = pd.DataFrame(
    {'unit_name': sorted(orgmap['Unit'].dropna().unique())}
)

engine = get_sqlalchemy_engine()
with engine.connect() as conn:
    existing_units = pd.read_sql("SELECT unit_name FROM units_tbl", conn)
    new_units = units_df[~units_df['unit_name'].isin(existing_units['unit_name'])]

    for _, row in new_units.iterrows():
        conn.execute(
            text("INSERT INTO units_tbl (unit_name) VALUES (:unit_name)"),
            {'unit_name': row['unit_name']}
        )
    conn.commit()
    print(f"Inserted {len(new_units)} unit(s) into units_tbl")

with engine.connect() as conn:
    emp_tbl = pd.read_sql("SELECT res_id, banner_id FROM emp_tbl", conn)
    units_tbl_db = pd.read_sql("SELECT unit_id, unit_name FROM units_tbl", conn)

    # orgmap BannerID may be float — cast to int string to match emp_tbl banner_id
    orgmap['BannerID'] = orgmap['BannerID'].dropna().astype(int).astype(str)
    emp_tbl['banner_id'] = emp_tbl['banner_id'].astype(str)

    emp_units = (
        orgmap[['BannerID', 'Unit']]
        .merge(emp_tbl, left_on='BannerID', right_on='banner_id', how='inner')
        .merge(units_tbl_db, left_on='Unit', right_on='unit_name', how='inner')
        [['res_id', 'unit_id']]
        .drop_duplicates()
    )
    print(f"emp_units: {len(emp_units)} relationship(s) to sync")

with engine.connect() as conn:
    existing_emp_units = pd.read_sql("SELECT res_id, unit_id FROM emp_units", conn)

    new_emp_units = emp_units.merge(
        existing_emp_units, on=['res_id', 'unit_id'], how='left', indicator=True
    ).query("_merge == 'left_only'").drop(columns='_merge')
    print(f"emp_units: {len(new_emp_units)} new relationship(s) to insert")

    for _, row in new_emp_units.iterrows():
        conn.execute(
            text("INSERT INTO emp_units (res_id, unit_id) VALUES (:res_id, :unit_id)"),
            {'res_id': int(row['res_id']), 'unit_id': int(row['unit_id'])}
        )
    conn.commit()
    print(f"Inserted {len(new_emp_units)} row(s) into emp_units")



############################################################
### Build pos_df for pos_tbl

import re

EMP_REPORT_FILE = "Data/USD_Active_Employees_Publications_Research_Report.csv"
emp_report = pd.read_csv(EMP_REPORT_FILE)

def _clean_position(val):
    if pd.isna(val):
        return ''
    p = re.sub(r'^P\d+\s*', '', str(val))   # strip leading code e.g. "P28 "
    p = re.sub(r'\s*\([^)]*\)', '', p)        # strip parentheticals
    return p.strip()

def _clean_unit(val):
    if pd.isna(val):
        return ''
    return re.sub(r'\s*\([^)]*\)', '', str(val)).strip()

def _supervisory_dept(val):
    # "School of Law - Tenured/Tenure-Track Faculty (Robert Muth)" -> "Tenured/Tenure-Track Faculty"
    cleaned = _clean_unit(val)
    if ' - ' in cleaned:
        return cleaned.split(' - ', 1)[1].strip()
    return cleaned.strip()

emp_report['position_clean'] = emp_report['Position'].apply(_clean_position)
emp_report['dept_clean']     = emp_report['Department'].apply(_clean_unit)
emp_report['sup_dept_clean'] = emp_report['Supervisory_Organization'].apply(_supervisory_dept)
emp_report['college_clean']  = emp_report['College'].apply(_clean_unit)

with engine.connect() as conn:
    emp_tbl_db    = pd.read_sql("SELECT res_id, banner_id FROM emp_tbl", conn)
    units_tbl_db  = pd.read_sql("SELECT unit_id, unit_name FROM units_tbl", conn)
    emp_units_db  = pd.read_sql("SELECT res_id, unit_id FROM emp_units", conn)

unit_lookup = units_tbl_db.set_index('unit_name')['unit_id'].to_dict()
emp_units_db['unit_id'] = emp_units_db['unit_id'].astype(int)
first_unit = emp_units_db.groupby('res_id')['unit_id'].first().to_dict()

emp_report['Banner_ID'] = emp_report['Banner_ID'].astype(int).astype(str)
emp_tbl_db['banner_id'] = emp_tbl_db['banner_id'].astype(str)
merged = emp_report.merge(emp_tbl_db, left_on='Banner_ID', right_on='banner_id', how='inner')

pos_rows = []
for _, row in merged.iterrows():
    res_id = int(row['res_id'])

    unit_id = (
        unit_lookup.get(row['dept_clean'])
        or unit_lookup.get(row['sup_dept_clean'])
        or unit_lookup.get(row['college_clean'])
        or first_unit.get(res_id)
    )

    if unit_id is None:
        continue

    pos_rows.append({
        'res_id':     res_id,
        'position':   row['position_clean'],
        'unit_id':    int(unit_id),
        'has_tenure': int(row['Has_Tenure']),
    })

pos_df = pd.DataFrame(pos_rows).drop_duplicates(subset=['res_id', 'unit_id'])
print(f"pos_df: {len(pos_df)} row(s) ready for pos_tbl")

with engine.connect() as conn:
    existing_pos = pd.read_sql("SELECT res_id, unit_id FROM pos_tbl", conn)

new_pos = pos_df.merge(
    existing_pos, on=['res_id', 'unit_id'], how='left', indicator=True
).query("_merge == 'left_only'").drop(columns='_merge')
print(f"pos_tbl: {len(new_pos)} new record(s) to insert")

with engine.connect() as conn:
    for _, row in new_pos.iterrows():
        conn.execute(
            text("INSERT INTO pos_tbl (res_id, position, unit_id, has_tenure) "
                 "VALUES (:res_id, :position, :unit_id, :has_tenure)"),
            {
                'res_id':     int(row['res_id']),
                'position':   row['position'],
                'unit_id':    int(row['unit_id']),
                'has_tenure': int(row['has_tenure']),
            }
        )
    conn.commit()
    print(f"Inserted {len(new_pos)} row(s) into pos_tbl")

############################################################
### Unit roster query

UNIT_ID = 252

with engine.connect() as conn:
    unit_roster = pd.read_sql("""
        SELECT DISTINCT
            e.res_id,
            e.first_name,
            e.last_name,
            p.position,
            p.has_tenure
        FROM emp_tbl e
        JOIN emp_units eu ON e.res_id = eu.res_id
        LEFT JOIN pos_tbl p ON e.res_id = p.res_id
        WHERE eu.unit_id = %(unit_id)s
        ORDER BY e.last_name, e.first_name
    """, conn, params={'unit_id': UNIT_ID})

print(f"Unit {UNIT_ID} — {len(unit_roster)} employee(s)")
print(unit_roster.to_string(index=False))

############################################################
### NOT READY YET
### Build org_map_df (hierarchy of units)

def _clean_name(val):
    if pd.isna(val): return ''
    t = str(val)
    t = re.sub(r'\s*\([^)]*\)', '', t)   # remove complete (...) groups
    t = re.sub(r'\s*\([^)]*$', '', t)    # remove trailing unclosed (
    return t.strip()

# Load fresh units_tbl and emp_units
with engine.connect() as conn:
    units_tbl_db  = pd.read_sql("SELECT unit_id, unit_name FROM units_tbl", conn)
    emp_units_db  = pd.read_sql("SELECT res_id, unit_id FROM emp_units", conn)
    emp_tbl_db    = pd.read_sql("SELECT res_id, banner_id FROM emp_tbl", conn)

# Clean College column — this gives us the top-level (super_org) name
emp_report['college_clean'] = emp_report['College'].apply(_clean_name)

# Add any College-level names missing from units_tbl
existing_unit_names = set(units_tbl_db['unit_name'])
missing_colleges = (
    emp_report['college_clean']
    .replace('', pd.NA).dropna()
    .unique()
)
missing_colleges = [c for c in missing_colleges if c not in existing_unit_names]

if missing_colleges:
    with engine.connect() as conn:
        for name in missing_colleges:
            conn.execute(text("INSERT INTO units_tbl (unit_name) VALUES (:n)"), {'n': name})
        conn.commit()
    print(f"Added {len(missing_colleges)} college-level unit(s) to units_tbl")
    with engine.connect() as conn:
        units_tbl_db = pd.read_sql("SELECT unit_id, unit_name FROM units_tbl", conn)

unit_lookup = units_tbl_db.set_index('unit_name')['unit_id'].to_dict()

# Join emp_report → emp_tbl to get res_id
emp_report['Banner_ID_str'] = emp_report['Banner_ID'].astype(int).astype(str)
emp_tbl_db['banner_id_str'] = emp_tbl_db['banner_id'].astype(str)
report_with_res = emp_report[['Banner_ID_str', 'college_clean']].merge(
    emp_tbl_db[['res_id', 'banner_id_str']],
    left_on='Banner_ID_str', right_on='banner_id_str',
    how='inner'
)

# Join with emp_units to get each person's child unit_ids
report_with_units = report_with_res.merge(emp_units_db, on='res_id', how='inner')

# Map College name → unit_id
report_with_units['super_org'] = report_with_units['college_clean'].map(unit_lookup)

# Drop rows where College didn't resolve or College == child unit (no real hierarchy)
org_map_df = (
    report_with_units
    .dropna(subset=['super_org'])
    .assign(super_org=lambda d: d['super_org'].astype(int))
    .rename(columns={'unit_id': 'sub1_org'})
    [['super_org', 'sub1_org']]
    .drop_duplicates()
    .query("super_org != sub1_org")
    .assign(sub2_org=lambda d: d['sub1_org'],
            sub3_org=lambda d: d['sub1_org'])
    .reset_index(drop=True)
)

print(f"\norg_map_df: {len(org_map_df)} hierarchy row(s)")

id_to_name = units_tbl_db.set_index('unit_id')['unit_name'].to_dict()
display = org_map_df.copy()
display['college'] = display['super_org'].map(id_to_name)
display['unit']    = display['sub1_org'].map(id_to_name)
print(display[['college', 'unit']].to_string(index=False))



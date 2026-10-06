# %%
# import sys
# sys.path.insert(0, r"c:\Users\jaedkim\OneDrive - University of San Diego\PyProjects\granted_res_live")

import pandas as pd
from sqlalchemy import text
from config.settings import get_sqlalchemy_engine
from dotenv import load_dotenv

load_dotenv()

engine = get_sqlalchemy_engine()

with engine.connect() as con:
	emp_units_df = pd.read_sql(
		text("""
			SELECT eu.res_id, eu.unit_id, u.unit_name
			FROM emp_units eu
			LEFT JOIN units_tbl u ON eu.unit_id = u.unit_id
			ORDER BY u.unit_name
		"""),
		con,
	)
	units_tbl = pd.read_sql(text("SELECT unit_id, unit_name FROM units_tbl"), con)

print(f"{len(emp_units_df)} row(s) in emp_units joined with units_tbl:")
print(emp_units_df.head(20).to_string(index=False))

# hierarchy_file = "USD_Active_Employees_Publications_Research_Report.csv"
hierarchy_file = r"c:\Users\jaedkim\OneDrive - University of San Diego\PyProjects\granted_res_live\Data\USD_Active_Employees_Publications_Research_Report.csv"
hierarchy = pd.read_csv(hierarchy_file)
# hierarchy = pd.read_csv(f"Data/{hierarchy_file}")
hierarchy = hierarchy.iloc[:, -3:]
hierarchy.columns = ['Supervisory_Organization', 'Department', 'College']
hierarchy = hierarchy.apply(lambda col: col.str.replace(r'\s*\(.*?\)\s*$', '', regex=True).str.strip())
hierarchy = hierarchy.drop_duplicates()
print(f"\n{len(hierarchy)} row(s) in hierarchy file:")
print(hierarchy.head(20).to_string(index=False))

unit_names = set(emp_units_df['unit_name'].dropna().unique())

hierarchy_filtered = hierarchy[
	hierarchy['Supervisory_Organization'].isin(unit_names) |
	hierarchy['Department'].isin(unit_names) |
	hierarchy['College'].isin(unit_names)
].copy()

for col in ['Supervisory_Organization', 'Department', 'College']:
	hierarchy_filtered.loc[~hierarchy_filtered[col].isin(unit_names), col] = None

print(f"\n{len(hierarchy_filtered)} hierarchy row(s) where at least one column matches a unit_name in emp_units_df:")
print(hierarchy_filtered.to_string(index=False))

schools = ['School of Engineering', 'School of Business', 'School of Law', 'School of Arts and Sciences',
		   'School of Leadership and Education', 'School of Nursing and Health Science',
			'School of Peace Studies','Professional and Continuing Education']

all_unit_names = set(units_tbl['unit_name'].dropna().unique())
missing_schools = [s for s in schools if s not in all_unit_names]
print(f"\n{len(missing_schools)} school(s) NOT found in units_tbl:")
print(missing_schools)

### Insert missing school to units_tbl table
if missing_schools:
	with engine.begin() as con:
		for school in missing_schools:
			con.execute(text("INSERT INTO units_tbl (unit_name) VALUES (:name)"), {"name": school})
	print(f"\nInserted {len(missing_schools)} school(s) into units_tbl:")
	for s in missing_schools:
		print(f"  {s}")
else:
	print("\nNo missing schools to insert.")

# Refresh units_tbl so newly inserted schools are included
with engine.connect() as con:
	units_tbl = pd.read_sql(text("SELECT unit_id, unit_name FROM units_tbl"), con)
all_unit_names = set(units_tbl['unit_name'].dropna().unique())

# Build parent-child pairs from the hierarchy CSV
# Column order: Supervisory_Organization < Department < College
pairs = []
for child_col, parent_col in [('Supervisory_Organization', 'Department'), ('Department', 'College')]:
	sub = hierarchy[[child_col, parent_col]].dropna()
	sub = sub[sub[child_col].isin(all_unit_names) & sub[parent_col].isin(all_unit_names)]
	pairs.append(sub.rename(columns={child_col: 'child_name', parent_col: 'parent_name'}))

unit_map_df = pd.concat(pairs).drop_duplicates().reset_index(drop=True)

unit_map_df = (
	unit_map_df
	.merge(units_tbl.rename(columns={'unit_id': 'child_unit_id', 'unit_name': 'child_name'}), on='child_name')
	.merge(units_tbl.rename(columns={'unit_id': 'parent_unit_id', 'unit_name': 'parent_name'}), on='parent_name')
)[['child_unit_id', 'child_name', 'parent_unit_id', 'parent_name']]

print(f"\n{len(unit_map_df)} unit hierarchy mapping(s):")
print(unit_map_df.to_string(index=False))
unit_map_df.to_csv("mapping.csv")


CAS = ['Anthropology','Communication','Ethnic Studies','Political Science and International Relations',
'Psychological Sciences','Sociology','Biology','Chemistry and Biochemistry',
'Environmental and Ocean Sciences','Mathematics','Neuroscience, Cognition and Behavior',
'Physics and Biophysics','English','History','Languages, Cultures and Literatures',
'Philosophy','Theology and Religious Studies','Art, Architecture + Art History',
'Music','Theatre']

cas_units_df = units_tbl[units_tbl['unit_name'].isin(CAS)][['unit_id', 'unit_name']].reset_index(drop=True)
missing_cas = [d for d in CAS if d not in all_unit_names]
print(f"\n{len(cas_units_df)} CAS department(s) found in units_tbl:")
print(cas_units_df.to_string(index=False))
if missing_cas:
	print(f"\n{len(missing_cas)} CAS department(s) NOT in units_tbl:")
	print(missing_cas)

SOB = ['Accountancy', 'Economics', 'Finance', 'Management, Law and Ethics',
	   'Marketing', 'Real Estate', 'International Business',
	   'Supply Chain, Operations and Information Systems']

sob_units_df = units_tbl[units_tbl['unit_name'].isin(SOB)][['unit_id', 'unit_name']].reset_index(drop=True)
missing_sob = [d for d in SOB if d not in all_unit_names]
print(f"\n{len(sob_units_df)} School of Business department(s) found in units_tbl:")
print(sob_units_df.to_string(index=False))
if missing_sob:
	print(f"\n{len(missing_sob)} School of Business department(s) NOT in units_tbl:")
	print(missing_sob)

mapping_updated = pd.read_csv(r"c:\Users\jaedkim\OneDrive - University of San Diego\PyProjects\granted_res_live\Data\mapping_updated.csv")
print(f"\n{len(mapping_updated)} row(s) in mapping_updated.csv:")
print(mapping_updated.head(20).to_string(index=False))

with engine.begin() as con:
	con.execute(text("""
		CREATE TABLE IF NOT EXISTS hierarchy_tbl (
			hierarchy_id   INT AUTO_INCREMENT PRIMARY KEY,
			child_unit_id  INT NOT NULL,
			parent_unit_id INT NOT NULL,
			FOREIGN KEY (child_unit_id)  REFERENCES units_tbl(unit_id),
			FOREIGN KEY (parent_unit_id) REFERENCES units_tbl(unit_id)
		)
	"""))
	rows = mapping_updated[['child_unit_id', 'parent_unit_id']].dropna().astype(int).to_dict('records')
	con.execute(
		text("INSERT INTO hierarchy_tbl (child_unit_id, parent_unit_id) VALUES (:child_unit_id, :parent_unit_id)"),
		rows
	)
print(f"\nInserted {len(rows)} row(s) into hierarchy_tbl.")


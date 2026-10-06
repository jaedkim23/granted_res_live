
import requests

from api.crossref_api import (
	CROSSREF_BASE,
	disambiguate_orcid_by_affiliation_and_doi,
	get_works_by_orcid,
	flatten_crossref_works,
)

import pandas as pd
from sqlalchemy import text, bindparam
from time import sleep

from config.settings import get_sqlalchemy_engine, get_db_params


# --- Config ---
LIMIT = 0                          # set to 0 for all rows
AFFILIATION = "University of San Diego"
ROWS = 1000                          # Crossref works to scan per author during ORCID search
WORKS_ROWS = 20                     # works to fetch per ORCID
THROTTLE = 0.1                      # seconds between API calls


def get_author_works_by_orcid(orcid: str, rows: int = WORKS_ROWS) -> pd.DataFrame:
	works_data = get_works_by_orcid(orcid=orcid, rows=rows)
	return flatten_crossref_works(orcid=orcid, works_data=works_data)

def insert_crossref_works_tbl(works_df: pd.DataFrame) -> int:
	engine = get_sqlalchemy_engine()
	count = 0
	with engine.begin() as con:
		for _, row in works_df.iterrows():
			res_id       = row["res_id"]
			orcid        = None     if pd.isna(row.get("orcid"))        else row.get("orcid")
			work_title   = None 	if pd.isna(row.get("title"))        else str(row.get("title"))[:1023]
			work_doi     = None 	if pd.isna(row.get("doi"))          else row.get("doi")
			publisher    = None 	if pd.isna(row.get("publisher"))    else row.get("publisher")
			work_type    = None 	if pd.isna(row.get("type"))         else row.get("type")
			publish_year = None     if pd.isna(row.get("publish_year")) else int(row.get("publish_year"))
			issn         = None     if pd.isna(row.get("issn"))         else row.get("issn")
			eissn        = None     if pd.isna(row.get("eissn"))        else row.get("eissn")

			existing = con.execute(
				text("SELECT crossref_work_id FROM crossref_works_tbl WHERE res_id = :res_id AND work_doi = :work_doi LIMIT 1"),
				{"res_id": res_id, "work_doi": work_doi},
			).fetchone()

			if existing:
				con.execute(
					text("""
						UPDATE crossref_works_tbl
						SET work_title   = :work_title,
						    publisher    = :publisher,
						    `type`       = :type,
						    publish_year = :publish_year,
						    issn         = :issn,
						    eissn        = :eissn
						WHERE crossref_work_id = :crossref_work_id
					"""),
					{
						"work_title":        work_title,
						"publisher":         publisher,
						"type":              work_type,
						"publish_year":      publish_year,
						"issn":              issn,
						"eissn":             eissn,
						"crossref_work_id":  existing[0],
					},
				)
			else:
				con.execute(
					text("""
						INSERT INTO crossref_works_tbl
							(res_id, orcid, work_title, work_doi, publisher, `type`, publish_year, issn, eissn)
						VALUES
							(:res_id, :orcid, :work_title, :work_doi, :publisher, :type, :publish_year, :issn, :eissn)
					"""),
					{
						"res_id":       res_id,
						"orcid":        orcid,
						"work_title":   work_title,
						"work_doi":     work_doi,
						"publisher":    publisher,
						"type":         work_type,
						"publish_year": publish_year,
						"issn":         issn,
						"eissn":        eissn,
					},
				)
				count += 1
	return count

### Retreive ORCIDs from emp_id_tbl
engine = get_sqlalchemy_engine()
with engine.connect() as con:
    query = text("""
        SELECT e.res_id,
               CONCAT(emp.first_name, ' ', emp.last_name) AS author_name,
               e.source_id AS best_orcid
        FROM emp_id_tbl e
        JOIN emp_tbl emp ON e.res_id = emp.res_id
        WHERE e.source_id IS NOT NULL
        {limit_clause}
    """.format(limit_clause="LIMIT :limit" if LIMIT > 0 else ""))
    params = {"limit": LIMIT} if LIMIT > 0 else {}
    orcid_df = pd.read_sql(query, con, params=params)

orcid_rows = orcid_df[orcid_df["best_orcid"].notna()].copy()

### Fetch all works for each ORCID and store in crossref_works_tbl
all_works: list[pd.DataFrame] = []

for _, row in orcid_rows.iterrows():
	orcid = str(row["best_orcid"]).strip()
	works_df = get_author_works_by_orcid(orcid=orcid)
	if not works_df.empty:
		works_df.insert(0, "res_id", row["res_id"])
		works_df.insert(1, "author_name", row["author_name"])
		all_works.append(works_df)
	print(f"  {row['author_name']} ({orcid}) -> {len(works_df)} work(s)")
	if THROTTLE > 0:
		sleep(THROTTLE)

works_all_df = pd.concat(all_works, ignore_index=True) if all_works else pd.DataFrame()


### Optional save to CSV
# WORKS_OUT = "Data/works_results.csv"
# WORKS_OUT = r"c:\Users\jaedkim\OneDrive - University of San Diego\PyProjects\granted_res_live\Data\works_results.csv"
# works_all_df = pd.read_csv(WORKS_OUT)

# if WORKS_OUT and not works_all_df.empty:
# 	works_all_df.to_csv(WORKS_OUT, index=False)
# 	print(f"Saved works to: {WORKS_OUT}")

if not works_all_df.empty:

	inserted_works = insert_crossref_works_tbl(works_all_df)
	print(f"Inserted {inserted_works} row(s) into crossref_works_tbl.")

### Check for duplicates in crossref_works_tbl
with get_sqlalchemy_engine().connect() as con:
	dupes_df = pd.read_sql(
		text("""
			SELECT res_id, work_doi, COUNT(*) AS cnt
			FROM crossref_works_tbl
			GROUP BY res_id, work_doi
			HAVING COUNT(*) > 1
		"""),
		con,
	)

if dupes_df.empty:
	print("No duplicates found in crossref_works_tbl.")
else:
	print(f"{len(dupes_df)} duplicate group(s) found in crossref_works_tbl:")
	print(dupes_df.to_string(index=False))

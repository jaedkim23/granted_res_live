"""
Research pipeline for OpenAlex and WOS
"""

import sys
# sys.path.insert(0, r"c:\Users\jaedkim\OneDrive - University of San Diego\PyProjects\granted_res_live")

import os
import pandas as pd
from sqlalchemy import text, bindparam
from config.settings import get_sqlalchemy_engine, get_db_params
import requests
from api.crossref_api import (
	CROSSREF_BASE,
	disambiguate_orcid_by_affiliation_and_doi,
	get_works_by_orcid,
	flatten_crossref_works,
)

from api.wos_api import create_wos_api_instance, get_wos_data_by_author_name
from time import sleep


engine = get_sqlalchemy_engine()
emp_tbl = pd.read_sql("SELECT res_id, first_name, last_name FROM emp_tbl", engine)

emp_tbl['first_name_lower'] = emp_tbl['first_name'].str.strip().str.lower()
emp_tbl['last_name_lower'] = emp_tbl['last_name'].str.strip().str.lower()

orcid_lookup = pd.read_sql(
    "SELECT res_id, source_id AS orcid FROM emp_id_tbl WHERE source_name = 'ORCID'",
    engine,
)
emp_tbl = emp_tbl.merge(orcid_lookup, on="res_id", how="left")


################################################
### OpenAlex

# --- Config ---
LIMIT = 0                          # set to 0 for all rows
AFFILIATION = "University of San Diego"
WORKS_ROWS = 1000                     # works to fetch per ORCID
THROTTLE = 0.1                      # seconds between API calls

from api.openalex_api import (
	get_works_by_orcid as openalex_works_by_orcid,
	get_author_works as openalex_author_works,
)

correct_orcids = emp_tbl['orcid'].dropna().unique()

openalex_works = []
for orcid in correct_orcids:
    orcid = str(orcid).strip()
    df = openalex_works_by_orcid(orcid) if orcid else pd.DataFrame()
    if not df.empty:
        df = df[df['institution_name'].str.contains(AFFILIATION, case=False, na=False)]
    if not df.empty:
        res_id_match = emp_tbl.loc[emp_tbl['orcid'] == orcid, 'res_id']
        df.insert(0, 'res_id', res_id_match.iloc[0] if not res_id_match.empty else None)
        openalex_works.append(df)
    print(f"{orcid} -> {len(df)} work(s) at '{AFFILIATION}'")
    sleep(THROTTLE)

openalex_works_df = pd.concat(openalex_works, ignore_index=True) if openalex_works else pd.DataFrame()

### Optional save to CSV
# WORKS_OUT = "Data/pipeline_v2_OA.csv"
# WORKS_OUT = r"c:\Users\jaedkim\OneDrive - University of San Diego\PyProjects\granted_res_live\Data\pipeline_v2_OA.csv"
# openalex_works_df = pd.read_csv(WORKS_OUT)

# openalex_works_df = pd.read_csv(WORKS_OUT)

# if WORKS_OUT and not openalex_works_df.empty:
# 	openalex_works_df.to_csv(WORKS_OUT, index=False)
# 	print(f"Saved works to: {WORKS_OUT}")

def _safe(val, max_len: int | None = None):
	if val is None or (isinstance(val, float) and pd.isna(val)):
		return None
	s = str(val)
	return s[:max_len] if max_len else s


def insert_open_alex_tbl(df: pd.DataFrame) -> int:
	engine = get_sqlalchemy_engine()
	count = 0
	with engine.begin() as con:
		for _, row in df.iterrows():
			res_id       = None if pd.isna(row.get("res_id"))           else int(row["res_id"])
			oa_author_id = _safe(row.get("openalex_author_id"),         1023)
			work_id      = _safe(row.get("work_id"),                    1023)
			title        = _safe(row.get("title"),                      1023)
			source       = _safe(row.get("source"),                     1023)
			publish_year = None if pd.isna(row.get("publication_year")) else int(row["publication_year"])

			exists = con.execute(
				text("SELECT 1 FROM open_alex_tbl WHERE res_id = :res_id AND work_id = :work_id LIMIT 1"),
				{"res_id": res_id, "work_id": work_id},
			).fetchone()
			if exists:
				continue

			con.execute(
				text("""
					INSERT INTO open_alex_tbl (res_id, oa_author_id, work_id, title, source, publish_year)
					VALUES (:res_id, :oa_author_id, :work_id, :title, :source, :publish_year)
				"""),
				{
					"res_id":       res_id,
					"oa_author_id": oa_author_id,
					"work_id":      work_id,
					"title":        title,
					"source":       source,
					"publish_year": publish_year,
				},
			)
			count += 1
	return count

inserted_oa = insert_open_alex_tbl(openalex_works_df)
print(f"Inserted {inserted_oa} row(s) into open_alex_tbl.")


############################################################
### Web of Science
from api.wos_api import create_wos_api_instance, get_wos_data_by_author_name

THROTTLE_WOS = 0.5

_emp = emp_tbl.dropna(subset=['orcid']).copy()
_emp['author_name'] = _emp['first_name'].str.strip() + ' ' + _emp['last_name'].str.strip()
_orcid_to_author = _emp.set_index(_emp['orcid'].str.strip())['author_name'].to_dict()

wos_api = create_wos_api_instance()
wos_rows = []

for orcid in correct_orcids:
    author_name = _orcid_to_author.get(str(orcid).strip(), '')
    if not author_name:
        print(f"{orcid} -> no author name, skipping WoS")
        continue
    name_parts = str(author_name).strip().split()
    wos_author = f"{name_parts[-1]}, {name_parts[0]}" if len(name_parts) >= 2 else author_name

    response = get_wos_data_by_author_name(wos_api, 1, wos_author)
    if response is None:
        print(f"{orcid} ({wos_author}) -> WoS error")
        sleep(THROTTLE_WOS)
        continue

    data = response.to_dict()
    hits = data.get('hits', [])
    for hit in hits:
        ids = hit.get('identifiers', {})
        uid = hit.get('uid', '')
        hit_authors = [
            a.get('display_name') or a.get('displayName', '')
            for a in (hit.get('names') or {}).get('authors', [])
        ]
        src = hit.get('source') or {}
        wos_rows.append({
            'orcid': orcid,
            'doi': ids.get('doi'),
            'title': hit.get('title'),
            'year': src.get('publish_year') or src.get('publishYear'),
            'author_name': author_name,
            'source': 'WoS',
            'url': f"https://www.webofscience.com/wos/woscc/full-record/{uid}" if uid else None,
            'wos_authors': '|'.join(hit_authors),
        })

    print(f"{orcid} ({wos_author}) -> {len(hits)} work(s)")
    sleep(THROTTLE_WOS)

wos_df = pd.DataFrame(wos_rows) if wos_rows else pd.DataFrame(
    columns=['orcid', 'doi', 'title', 'year', 'author_name', 'source', 'url', 'wos_authors']
)

### Optional save to CSV
# WORKS_OUT = "Data/pipeline_v2_wos.csv"
# WORKS_OUT = r"c:\Users\jaedkim\OneDrive - University of San Diego\PyProjects\granted_res_live\Data\pipeline_v2_wos.csv"
# # wos_works_df = pd.read_csv(WORKS_OUT)

# if WORKS_OUT and not wos_df.empty:
# 	wos_df.to_csv(WORKS_OUT, index=False)
# 	print(f"Saved works to: {WORKS_OUT}")


# Remove WoS records where the affiliated author's last name isn't in the author list.
# WoS is queried by name (not ORCID), so false positives are possible.
if not wos_df.empty:
    def _wos_author_verified(row):
        expected = _orcid_to_author.get(str(row['orcid']).strip(), '')
        if not expected:
            return False
        last_name = expected.strip().split()[-1].lower()
        return last_name in str(row.get('wos_authors', '')).lower()

    n_before = len(wos_df)
    wos_df = wos_df[wos_df.apply(_wos_author_verified, axis=1)].reset_index(drop=True)
    print(f"WoS: removed {n_before - len(wos_df)} unaffiliated work(s), {len(wos_df)} remaining")

print(f"\nWoS: {len(wos_df)} total work(s) across {len(correct_orcids)} ORCID(s)")


def insert_wos_work_tbl(df: pd.DataFrame) -> int:
	wos_with_res = df.merge(emp_tbl[['res_id', 'orcid']], on='orcid', how='left')
	engine = get_sqlalchemy_engine()
	count = 0
	with engine.begin() as con:
		for _, row in wos_with_res.iterrows():
			res_id      = None if pd.isna(row.get("res_id"))    else int(row["res_id"])
			wos_rec_url = _safe(row.get("url"),                 1023)
			wos_title   = _safe(row.get("title"),               1023)
			year        = None if pd.isna(row.get("year"))      else int(row["year"])
			doi         = _safe(row.get("doi"),                 1023)
			author      = _safe(row.get("author_name"),         1023)
			wos_authors = _safe(row.get("wos_authors"),         4095)

			exists = con.execute(
				text("""
					SELECT 1 FROM wos_work_tbl
					WHERE res_id = :res_id
					  AND (
					    (doi IS NOT NULL AND LOWER(doi) = LOWER(:doi))
					    OR (doi IS NULL AND LOWER(wos_title) = LOWER(:wos_title))
					  )
					LIMIT 1
				"""),
				{"res_id": res_id, "doi": doi, "wos_title": wos_title},
			).fetchone()
			if exists:
				continue

			con.execute(
				text("""
					INSERT INTO wos_work_tbl (res_id, wos_rec_url, wos_title, year, doi, author, wos_authors)
					VALUES (:res_id, :wos_rec_url, :wos_title, :year, :doi, :author, :wos_authors)
				"""),
				{
					"res_id":      res_id,
					"wos_rec_url": wos_rec_url,
					"wos_title":   wos_title,
					"year":        year,
					"doi":         doi,
					"author":      author,
					"wos_authors": wos_authors,
				},
			)
			count += 1
	return count


if not wos_df.empty:
	inserted_wos = insert_wos_work_tbl(wos_df)
	print(f"Inserted {inserted_wos} row(s) into wos_work_tbl.")


###############################################################
### Sync to pub_works_tbl

def _norm_doi(val):
	if not val or str(val).strip().lower() in ('unknown', 'none', ''):
		return None
	return str(val).replace("https://doi.org/", "").replace("http://doi.org/", "").strip().lower()


def sync_crossref_pub_works() -> tuple[int, int]:
	engine = get_sqlalchemy_engine()
	updated = 0
	inserted = 0
	with engine.begin() as con:
		rows = con.execute(text("""
			SELECT crossref_work_id, res_id, work_title, work_doi, publish_year
			FROM crossref_works_tbl
		""")).fetchall()
		for row in rows:
			crossref_work_id = row[0]
			res_id           = row[1]
			work_title       = row[2]
			work_doi         = _norm_doi(row[3])
			publish_year     = row[4]

			match = None
			if res_id and work_doi:
				match = con.execute(
					text("SELECT pub_works_tbl_id FROM pub_works_tbl WHERE res_id = :res_id AND LOWER(work_doi) = :doi LIMIT 1"),
					{"res_id": res_id, "doi": work_doi},
				).fetchone()
			if not match and res_id and work_title:
				match = con.execute(
					text("SELECT pub_works_tbl_id FROM pub_works_tbl WHERE res_id = :res_id AND LOWER(work_title) = LOWER(:title) LIMIT 1"),
					{"res_id": res_id, "title": work_title},
				).fetchone()

			if match:
				con.execute(
					text("""
						UPDATE pub_works_tbl
						SET crossref     = :cid,
						    publish_year = COALESCE(publish_year, :publish_year)
						WHERE pub_works_tbl_id = :pk
					"""),
					{"cid": crossref_work_id, "publish_year": publish_year, "pk": match[0]},
				)
				updated += 1
			else:
				con.execute(
					text("""
						INSERT INTO pub_works_tbl (res_id, work_title, work_doi, publish_year, crossref, openalex, wos)
						VALUES (:res_id, :work_title, :work_doi, :publish_year, :cid, NULL, NULL)
					"""),
					{
						"res_id":       res_id,
						"work_title":   work_title,
						"work_doi":     work_doi,
						"publish_year": publish_year,
						"cid":          crossref_work_id,
					},
				)
				inserted += 1
	return updated, inserted


def sync_openalex_pub_works() -> tuple[int, int]:
	engine = get_sqlalchemy_engine()
	updated = 0
	inserted = 0
	with engine.begin() as con:
		rows = con.execute(text("""
			SELECT open_alex_rec_id, res_id, title, publish_year
			FROM open_alex_tbl
		""")).fetchall()
		for row in rows:
			open_alex_rec_id = row[0]
			res_id           = row[1]
			title            = row[2]
			publish_year     = row[3]

			match = None
			if res_id and title:
				match = con.execute(
					text("SELECT pub_works_tbl_id FROM pub_works_tbl WHERE res_id = :res_id AND LOWER(work_title) = LOWER(:title) LIMIT 1"),
					{"res_id": res_id, "title": title},
				).fetchone()

			if match:
				con.execute(
					text("""
						UPDATE pub_works_tbl
						SET openalex     = :oid,
						    work_title   = :title,
						    publish_year = COALESCE(:publish_year, publish_year)
						WHERE pub_works_tbl_id = :pk
					"""),
					{"oid": open_alex_rec_id, "title": title, "publish_year": publish_year, "pk": match[0]},
				)
				updated += 1
			else:
				con.execute(
					text("""
						INSERT INTO pub_works_tbl (res_id, work_title, publish_year, crossref, openalex, wos)
						VALUES (:res_id, :title, :publish_year, NULL, :oid, NULL)
					"""),
					{
						"res_id":       res_id,
						"title":        title,
						"publish_year": publish_year,
						"oid":          open_alex_rec_id,
					},
				)
				inserted += 1
	return updated, inserted


def sync_wos_pub_works() -> tuple[int, int]:
	engine = get_sqlalchemy_engine()
	updated = 0
	inserted = 0
	with engine.begin() as con:
		rows = con.execute(text("""
			SELECT wos_work_id, res_id, wos_title, doi, year
			FROM wos_work_tbl
		""")).fetchall()
		for row in rows:
			wos_work_id  = row[0]
			res_id       = row[1]
			wos_title    = row[2]
			doi          = _norm_doi(row[3])
			publish_year = row[4]

			match = None
			if res_id and doi:
				match = con.execute(
					text("SELECT pub_works_tbl_id FROM pub_works_tbl WHERE res_id = :res_id AND LOWER(work_doi) = :doi LIMIT 1"),
					{"res_id": res_id, "doi": doi},
				).fetchone()
			if not match and res_id and wos_title:
				match = con.execute(
					text("SELECT pub_works_tbl_id FROM pub_works_tbl WHERE res_id = :res_id AND LOWER(work_title) = LOWER(:title) LIMIT 1"),
					{"res_id": res_id, "title": wos_title},
				).fetchone()

			if match:
				con.execute(
					text("""
						UPDATE pub_works_tbl
						SET wos          = :wid,
						    work_title   = :wos_title,
						    work_doi     = COALESCE(:doi, work_doi),
						    publish_year = COALESCE(:publish_year, publish_year)
						WHERE pub_works_tbl_id = :pk
					"""),
					{"wid": wos_work_id, "wos_title": wos_title, "doi": doi, "publish_year": publish_year, "pk": match[0]},
				)
				updated += 1
			else:
				con.execute(
					text("""
						INSERT INTO pub_works_tbl (res_id, work_title, work_doi, publish_year, crossref, openalex, wos)
						VALUES (:res_id, :wos_title, :doi, :publish_year, NULL, NULL, :wid)
					"""),
					{
						"res_id":       res_id,
						"wos_title":    wos_title,
						"doi":          doi,
						"publish_year": publish_year,
						"wid":          wos_work_id,
					},
				)
				inserted += 1
	return updated, inserted


crossref_updated, crossref_inserted = sync_crossref_pub_works()
print(f"pub_works_tbl crossref — updated: {crossref_updated}, inserted: {crossref_inserted}")

oa_updated, oa_inserted = sync_openalex_pub_works()
print(f"pub_works_tbl openalex — updated: {oa_updated}, inserted: {oa_inserted}")

wos_updated, wos_inserted = sync_wos_pub_works()
print(f"pub_works_tbl wos      — updated: {wos_updated}, inserted: {wos_inserted}")

### Check for duplicates in pub_works_tbl
with get_sqlalchemy_engine().connect() as con:
	dupes_df = pd.read_sql(
		text("""
			SELECT res_id, work_doi, COUNT(*) AS cnt
			FROM pub_works_tbl
			GROUP BY res_id, work_doi
			HAVING COUNT(*) > 1
		"""),
		con,
	)

if dupes_df.empty:
	print("No duplicates found in pub_works_tbl.")
else:
	print(f"{len(dupes_df)} duplicate group(s) found in pub_works_tbl:")
	print(dupes_df.to_string(index=False))
import requests

from api.crossref_api import (
	CROSSREF_BASE,
	disambiguate_orcid_by_affiliation_and_doi,
	get_works_by_orcid,
)

import pandas as pd
from sqlalchemy import text, bindparam
from time import sleep
from config.settings import get_sqlalchemy_engine, get_db_params


# --- Config ---
LIMIT = 0                        # set to 0 for all rows
AFFILIATION = "University of San Diego"
AFFILIATION_ALIASES = [             # tried in order for the ORCID API method; stops at the first alias with a match
	"USD",
	"University of San Diego",
	"Univ. of San Diego",
	"U. San Diego",
]
ROWS = 200                          # Crossref works to scan per author during ORCID search
THROTTLE = 0.1                      # seconds between API calls

ORCID_SEARCH_URL = "https://pub.orcid.org/v3.0/search/"


def fetch_emp_names(limit: int | None = None) -> pd.DataFrame:
	engine = get_sqlalchemy_engine()
	query = """
		SELECT res_id, first_name, last_name
		FROM emp_tbl
		WHERE first_name IS NOT NULL
		  AND last_name IS NOT NULL
		ORDER BY last_name, first_name
	"""
	params: dict[str, int] = {}
	if limit is not None:
		query += " LIMIT :limit"
		params["limit"] = limit

	with engine.connect() as connection:
		return pd.read_sql(text(query), connection, params=params)


def search_orcid_api(
	first_name: str,
	last_name: str,
	affiliations: list[str] = AFFILIATION_ALIASES,
	throttle_seconds: float = THROTTLE,
) -> dict | None:
	"""Query ORCID's public API by given name, family name, and affiliation. Tries each affiliation alias in order."""
	for i, affiliation in enumerate(affiliations):
		query = f'given-names:{first_name} AND family-name:"{last_name}" AND affiliation-org-name:"{affiliation}"'
		response = requests.get(
			ORCID_SEARCH_URL,
			headers={"Accept": "application/json"},
			params={"q": query},
		)
		if response.status_code != 200:
			print(f"ORCID API error {response.status_code} for: {first_name} {last_name} (affiliation={affiliation!r})")
			continue

		data = response.json()
		if data.get("num-found", 0) > 0:
			return data

		if i < len(affiliations) - 1 and throttle_seconds > 0:
			sleep(throttle_seconds)

	return None


def find_orcids_merged(
	emp_df: pd.DataFrame,
	affiliation: str = AFFILIATION,
	affiliations: list[str] = AFFILIATION_ALIASES,
	rows: int = ROWS,
	throttle_seconds: float = THROTTLE,
) -> pd.DataFrame:
	"""
	For each name, try the ORCID API method first (with affiliation-alias fallback).
	If it finds nothing, fall back to the Crossref-based method. If neither finds anything, leave blank.
	"""
	results: list[dict[str, object]] = []

	for idx, row in emp_df.iterrows():
		first_name = str(row["first_name"]).strip()
		last_name = str(row["last_name"]).strip()
		author_name = f"{first_name} {last_name}"

		best_orcid = None
		source = None

		data = search_orcid_api(first_name, last_name, affiliations, throttle_seconds)
		if data:
			matches = data.get("result") or []
			if matches:
				best_orcid = matches[0].get("orcid-identifier", {}).get("path")
				source = "orcid"

		if not best_orcid:
			result = disambiguate_orcid_by_affiliation_and_doi(
				author_name=author_name,
				affiliation=affiliation,
				known_dois=None,
				rows=rows,
			)
			if result and result.get("best_orcid"):
				best_orcid = result.get("best_orcid")
				source = "crossref"

		results.append(
			{
				"res_id": row["res_id"],
				"first_name": first_name,
				"last_name": last_name,
				"author_name": author_name,
				"best_orcid": best_orcid,
				"source": source,
			}
		)

		print(
			f"[{idx + 1}/{len(emp_df)}] {author_name} -> "
			f"{best_orcid if best_orcid else 'No ORCID found'}"
			f"{f' ({source})' if source else ''}"
		)

		if throttle_seconds > 0:
			sleep(throttle_seconds)

	return pd.DataFrame(results)


def insert_emp_id_tbl(orcid_rows: pd.DataFrame) -> int:
	insert_df = (
		orcid_rows[["res_id", "best_orcid"]]
		.rename(columns={"best_orcid": "source_id"})
		.copy()
	)
	insert_df["source_name"] = "ORCID"
	engine = get_sqlalchemy_engine()
	res_ids = insert_df["res_id"].tolist()
	with engine.begin() as con:
		if res_ids:
			con.execute(
				text("DELETE FROM emp_id_tbl WHERE res_id IN :res_ids AND source_name = 'ORCID'")
				.bindparams(bindparam("res_ids", expanding=True)),
				{"res_ids": res_ids},
			)
		insert_df.to_sql("emp_id_tbl", con=con, if_exists="append", index=False)
	return len(insert_df)

# --- Script ---

effective_limit = None if LIMIT == 0 else LIMIT
emp_df = fetch_emp_names(limit=effective_limit)

##### Testing a specific list of res_id
# select_res_id = df_sub['res_id'].tolist()
# emp_df = emp_df[emp_df['res_id'].isin(select_res_id)]

orcid_df = find_orcids_merged(emp_df=emp_df)


# #### Optional save to CSV
# ORCID_OUT = "Data/ORCID_results.csv"  # Example output path; change as needed
# orcid_df = pd.read_csv(ORCID_OUT) if ORCID_OUT else orcid_df

# if ORCID_OUT:
# 	orcid_df.to_csv(ORCID_OUT, index=False)
# 	print(f"Saved ORCID results to: {ORCID_OUT}")


orcid_rows = orcid_df[orcid_df["best_orcid"].notna()].copy()
print(f"\n{len(orcid_rows)} researcher(s) with an ORCID.")

inserted = insert_emp_id_tbl(orcid_rows)
print(f"Inserted {inserted} row(s) into emp_id_tbl.")
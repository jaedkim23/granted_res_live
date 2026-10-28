# %%
import unicodedata

import requests

import pandas as pd
from sqlalchemy import text, bindparam
from time import sleep
from config.settings import get_sqlalchemy_engine, get_db_params

import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd().parent))  # adjust levels as needed


# --- Config ---
LIMIT = 0                         # set to 0 for all rows
AFFILIATION_ALIASES = [             # tried in order; stops at the first alias with a match
	"USD",
	"University of San Diego",
	"Univ. of San Diego",
	"U. San Diego",
]
THROTTLE = 0.1                      # seconds between API calls
OPENALEX_MAX_CANDIDATES = 20         # candidate cap for the OpenAlex-affiliation fallback (only runs when orcid + other-names both miss)

ORCID_SEARCH_URL = "https://pub.orcid.org/v3.0/search/"
OPENALEX_AUTHOR_URL = "https://api.openalex.org/authors/https://orcid.org/{orcid}"


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


def search_orcid_name_only(
	first_name: str,
	last_name: str,
	throttle_seconds: float = THROTTLE,
) -> dict | None:
	"""Query ORCID's public API by given name + family name only, no affiliation clause."""
	query = f'given-names:{first_name} AND family-name:"{last_name}"'
	response = requests.get(
		ORCID_SEARCH_URL,
		headers={"Accept": "application/json"},
		params={"q": query},
	)
	if response.status_code != 200:
		print(f"ORCID API error {response.status_code} for name-only search: {first_name} {last_name}")
		return None
	return response.json()


def _strip_accents(s: str) -> str:
	"""Transliterate accented characters to their plain-ASCII equivalent (e.g. 'González' -> 'Gonzalez')."""
	return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')


def search_orcid_other_names(
	first_name: str,
	last_name: str,
	affiliations: list[str] = AFFILIATION_ALIASES,
	throttle_seconds: float = THROTTLE,
) -> dict | None:
	"""
	Query ORCID's public API by the 'other-names' field (aliases/former/maiden names on file)
	+ affiliation. Catches cases where a person's ORCID family-name is only part of their full
	last name (e.g. family-name="Recart" but other-names has "Wilnelia Recart Gonzalez"). Tries
	each affiliation alias in order, and also tries an accent-stripped version of the name --
	ORCID's search does literal matching on quoted phrases, so accent differences between our
	records and what's on ORCID can otherwise break an exact match.
	"""
	full_name = f"{first_name} {last_name}"
	stripped_name = _strip_accents(full_name)
	name_variants = [full_name] if stripped_name == full_name else [full_name, stripped_name]

	for name in name_variants:
		for i, affiliation in enumerate(affiliations):
			query = f'other-names:"{name}" AND affiliation-org-name:"{affiliation}"'
			response = requests.get(
				ORCID_SEARCH_URL,
				headers={"Accept": "application/json"},
				params={"q": query},
			)
			if response.status_code != 200:
				print(f"ORCID API error {response.status_code} for other-names search: {name} (affiliation={affiliation!r})")
				continue

			data = response.json()
			if data.get("num-found", 0) > 0:
				return data

			if i < len(affiliations) - 1 and throttle_seconds > 0:
				sleep(throttle_seconds)

	return None


def _affiliation_matches(org_name: str, affiliation_terms: list[str]) -> bool:
	"""Case-insensitive substring match against a list of acceptable affiliation phrasings."""
	name_lower = org_name.lower()
	return any(term.lower() in name_lower for term in affiliation_terms)


def get_openalex_affiliation_latest_year(orcid_id: str, affiliation_terms: list[str]) -> int | None:
	"""
	Return the most recent year a person's OpenAlex author record (looked up directly by ORCID)
	shows them affiliated with an institution matching affiliation_terms, or None if no match.
	OpenAlex infers this from actual paper metadata, so it can find an affiliation even when
	ORCID's own employment section is empty.
	"""
	response = requests.get(OPENALEX_AUTHOR_URL.format(orcid=orcid_id))
	if response.status_code != 200:
		return None

	data = response.json()
	latest_year = None
	for aff in data.get("affiliations") or []:
		name = aff.get("institution", {}).get("display_name")
		if not name or not _affiliation_matches(name, affiliation_terms):
			continue
		years = aff.get("years") or []
		if years:
			candidate_year = max(years)
			if latest_year is None or candidate_year > latest_year:
				latest_year = candidate_year
	return latest_year


def find_orcids_via_orcid_openalex(
	emp_df: pd.DataFrame,
	affiliations: list[str] = AFFILIATION_ALIASES,
	max_candidates: int = OPENALEX_MAX_CANDIDATES,
	throttle_seconds: float = THROTTLE,
) -> pd.DataFrame:
	"""
	orcid + openalex: for each name --
	  1) Search ORCID (given name + family name + affiliation, alias fallback). If found, done.
	  2) If (1) misses, search ORCID's 'other-names' field + affiliation (catches a truncated
	     ORCID family-name; also tries an accent-stripped name variant). If found, done.
	  3) If (1) and (2) both miss, search ORCID by name only to get a small candidate list, then
	     for each candidate check their OpenAlex affiliation history for a matching institution.
	     When multiple candidates match, keep whichever has the most recent matching year (two
	     different real people can share a name and both have worked at the same institution).
	If nothing is found by any step, leave blank.
	"""
	results: list[dict[str, object]] = []

	for idx, row in emp_df.iterrows():
		first_name = str(row["first_name"]).strip()
		last_name = str(row["last_name"]).strip()
		author_name = f"{first_name} {last_name}"

		best_orcid = None

		# Step 1: affiliation-search
		data = search_orcid_api(first_name, last_name, affiliations, throttle_seconds)
		if data:
			matches = data.get("result") or []
			if matches:
				best_orcid = matches[0].get("orcid-identifier", {}).get("path")

		# Step 2: other-names + affiliation, only if step 1 missed
		if not best_orcid:
			if throttle_seconds > 0:
				sleep(throttle_seconds)

			data_other = search_orcid_other_names(first_name, last_name, affiliations, throttle_seconds)
			if data_other:
				matches = data_other.get("result") or []
				if matches:
					best_orcid = matches[0].get("orcid-identifier", {}).get("path")

		# Step 3: name-only search -> OpenAlex affiliation check, only if steps 1 and 2 missed
		if not best_orcid:
			if throttle_seconds > 0:
				sleep(throttle_seconds)

			data_name_only = search_orcid_name_only(first_name, last_name, throttle_seconds)
			candidates = (data_name_only.get("result") or []) if data_name_only else []

			best_candidate = None
			best_year = None
			for candidate in candidates[:max_candidates]:
				candidate_orcid = candidate.get("orcid-identifier", {}).get("path")
				if not candidate_orcid:
					continue

				latest_year = get_openalex_affiliation_latest_year(candidate_orcid, affiliations)
				if latest_year is not None and (best_year is None or latest_year > best_year):
					best_year = latest_year
					best_candidate = candidate_orcid

				if throttle_seconds > 0:
					sleep(throttle_seconds)

			best_orcid = best_candidate

		results.append(
			{
				"res_id": row["res_id"],
				"first_name": first_name,
				"last_name": last_name,
				"author_name": author_name,
				"best_orcid": best_orcid,
			}
		)

		print(
			f"[{idx + 1}/{len(emp_df)}] {author_name} -> "
			f"{best_orcid if best_orcid else 'No ORCID found'}"
		)

		if throttle_seconds > 0:
			sleep(throttle_seconds)

	return pd.DataFrame(results)


def sync_emp_id_tbl(orcid_df: pd.DataFrame) -> tuple[int, int]:
	"""
	For every res_id processed this run: delete any existing emp_id_tbl row first
	(clearing stale/wrong values from older runs or older methods), then insert a
	fresh row only for the ones where an ORCID was actually found this run. A miss
	this run means that person ends up with NO row at all -- it does not preserve
	an old value, so emp_id_tbl only ever reflects the latest finding.
	Returns (cleared_count, inserted_count).
	"""
	engine = get_sqlalchemy_engine()
	res_ids = orcid_df["res_id"].tolist()
	insert_df = (
		orcid_df[orcid_df["best_orcid"].notna()][["res_id", "best_orcid"]]
		.rename(columns={"best_orcid": "orcid"})
		.copy()
	)
	with engine.begin() as con:
		if res_ids:
			con.execute(
				text("DELETE FROM emp_id_tbl WHERE res_id IN :res_ids")
				.bindparams(bindparam("res_ids", expanding=True)),
				{"res_ids": res_ids},
			)
		if not insert_df.empty:
			insert_df.to_sql("emp_id_tbl", con=con, if_exists="append", index=False)
	return len(res_ids), len(insert_df)

# --- Script ---

effective_limit = None if LIMIT == 0 else LIMIT
emp_df = fetch_emp_names(limit=effective_limit)

orcid_df = find_orcids_via_orcid_openalex(emp_df=emp_df.iloc[:1000])
orcid_df_all = orcid_df.copy()

orcid_df = find_orcids_via_orcid_openalex(emp_df=emp_df.iloc[4400:])
orcid_df_all = pd.concat([orcid_df_all, orcid_df], ignore_index=True)

ORCID_OUT = "ORCID_results.csv"  # Example output path; change as needed
orcid_df_all = pd.read_csv(ORCID_OUT) if ORCID_OUT else orcid_df
if ORCID_OUT:
	orcid_df_all.to_csv(ORCID_OUT, index=False)
	print(f"Saved ORCID results to: {ORCID_OUT}")


##### Optional save to CSV
# ORCID_OUT = "ORCID_results.csv"  # Example output path; change as needed
# if ORCID_OUT:
# 	orcid_df.to_csv(ORCID_OUT, index=False)
# 	print(f"Saved ORCID results to: {ORCID_OUT}")
# orcid_df = pd.read_csv(ORCID_OUT) if ORCID_OUT else orcid_df

found_count = orcid_df["best_orcid"].notna().sum()
print(f"\n{found_count} researcher(s) with an ORCID.")

cleared, inserted = sync_emp_id_tbl(orcid_df)
print(f"Cleared {cleared} existing row(s), inserted {inserted} fresh row(s) into emp_id_tbl.")
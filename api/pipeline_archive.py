"""
Research pipeline for a single employee record.

Steps:
  1. Find ORCID from emp_id_tbl
  2. Find OpenAlex ID
  3. Find works in OpenAlex using the ORCID; fill missing institutions;
     use the OpenAlex author ID to retrieve all works for that author
Optional:
  4. Find works in WOS using the best-matching WOS researcher ID

"""

import difflib
from time import sleep

import pandas as pd
from sqlalchemy import text

from api.crossref_api import get_works_by_orcid as _crossref_works_by_orcid, get_by_doi
from api.openalex_api import (
	get_works_by_orcid as openalex_works_by_orcid,
	get_author_works as openalex_author_works,
)
from api.wos_api import (
	create_wos_api_instance,
	get_wos_known_names,
	get_wos_data_by_author_id,
)
from etl.id_matching import validate_wos_id
from config.settings import get_sqlalchemy_engine


# Step 1 helper functions: Lookup ORCID from DB

def get_orcid_for_res_id(res_id: int) -> str | None:
	"""Return the ORCID stored in emp_id_tbl for a given res_id."""
	engine = get_sqlalchemy_engine()
	with engine.connect() as con:
		result = pd.read_sql(
			text("SELECT orcid FROM emp_id_tbl WHERE res_id = :res_id LIMIT 1"),
			con, params={"res_id": res_id},
		)
	return result.iloc[0]["orcid"] if not result.empty else None

# Step 2 helper functions: Find OpenAlex IDs


def fill_missing_institutions(openalex_df: pd.DataFrame) -> pd.DataFrame:
	"""Fill null institution_name values using Crossref author affiliations."""
	df = openalex_df.copy()
	missing_mask = df["institution_name"].isna()
	if not missing_mask.any():
		return df

	doi_cache: dict[str, dict] = {}
	for doi in df.loc[missing_mask, "doi"].dropna().unique():
		data = get_by_doi(doi)
		if data:
			doi_cache[doi] = {
				f"{a.get('given', '')} {a.get('family', '')}".strip(): "; ".join(
					aff.get("name", "") for aff in a.get("affiliation", []) if aff.get("name")
				) or None
				for a in data.get("message", {}).get("author", [])
			}

	filled = 0
	for idx, row in df[missing_mask].iterrows():
		doi = row.get("doi")
		author_name = str(row.get("author_name") or "").strip()
		if not doi or doi not in doi_cache:
			continue
		matches = difflib.get_close_matches(author_name, doi_cache[doi].keys(), n=1, cutoff=0.6)
		if matches:
			institution = doi_cache[doi][matches[0]]
			if institution:
				df.at[idx, "institution_name"] = institution
				filled += 1

	print(f"  Filled {filled} missing institution(s) from Crossref.")
	return df


# ── Step 4 helpers ────────────────────────────────────────────────────────────

def get_best_wos_id(res_id: int, api_instance) -> str | None:
	"""Return the wos_id from id_lookup with the highest name similarity."""
	engine = get_sqlalchemy_engine()
	with engine.connect() as con:
		emp = pd.read_sql(
			text("SELECT first_name, last_name FROM emp_tbl WHERE res_id = :res_id"),
			con, params={"res_id": res_id},
		)
		if emp.empty:
			return None
		first_name = emp.iloc[0]["first_name"]
		last_name = emp.iloc[0]["last_name"]

		id_rows = pd.read_sql(
			text("SELECT wos_id FROM id_lookup WHERE res_id = :res_id AND wos_id IS NOT NULL"),
			con, params={"res_id": res_id},
		)

	if id_rows.empty:
		return None
	if len(id_rows) == 1:
		return id_rows.iloc[0]["wos_id"]

	target = f"{first_name} {last_name}".lower()
	best_id, best_score = None, -1.0
	for wos_id in id_rows["wos_id"]:
		known = get_wos_known_names(api_instance, wos_id)
		if not known:
			continue
		score = max(
			difflib.SequenceMatcher(None, target, n.lower()).ratio()
			for n in known
		)
		if score > best_score:
			best_score, best_id = score, wos_id
	return best_id


def get_wos_works_by_wos_id(wos_id: str, api_instance) -> pd.DataFrame:
	"""Fetch all WOS publications for a WOS researcher ID."""
	response = get_wos_data_by_author_id(api_instance, 1, wos_id)
	if response is None:
		return pd.DataFrame()

	data = response.to_dict()
	total = data.get("metadata", {}).get("total", 0)
	if total == 0:
		return pd.DataFrame()

	limit = data.get("metadata", {}).get("limit", 50)
	pages = range(1, total // limit + 2)
	rows = []
	for page_n in pages:
		resp = get_wos_data_by_author_id(api_instance, page_n, wos_id)
		if resp is None:
			continue
		for hit in resp.to_dict().get("hits", []):
			ids = hit.get("identifiers", {})
			rows.append({
				"wos_id": wos_id,
				"work_id": hit.get("uid", "").split(":")[-1],
				"title": hit.get("title"),
				"source": hit.get("source", {}).get("sourceTitle"),
				"publish_year": hit.get("source", {}).get("publishYear"),
				"doi": ids.get("doi"),
				"issn": ids.get("issn"),
			})
	return pd.DataFrame(rows)


# ── Merge helpers ─────────────────────────────────────────────────────────────

_MERGE_COLS = ["doi", "title", "year", "journal"]

def merge_source_dfs(
    sources: list[tuple[pd.DataFrame, str, dict[str, str]]],
) -> pd.DataFrame:
    """
    Normalize and concatenate DataFrames from multiple sources into a common schema.

    Args:
        sources: List of (df, source_db, column_mapping) tuples.
                 column_mapping maps each common column name to its
                 source-specific column name.
                 Common columns: doi, title, year, journal.

    Returns:
        pd.DataFrame with columns: source_db, doi, title, year, journal
    """
    frames = []
    for df, source_db, col_map in sources:
        if df.empty:
            continue
        normalized = df.rename(columns={v: k for k, v in col_map.items()})
        normalized = normalized.reindex(columns=_MERGE_COLS)
        normalized["doi"] = normalized["doi"].str.replace("https://doi.org/", "", regex=False)
        normalized.insert(0, "source_db", source_db)
        frames.append(normalized)

    if not frames:
        return pd.DataFrame(columns=["source_db"] + _MERGE_COLS)
    return pd.concat(frames, ignore_index=True)


# ── Step 5 helpers ────────────────────────────────────────────────────────────

def collect_unique_dois(*dataframes: pd.DataFrame) -> list[str]:
	"""Collect unique lowercased DOIs from any number of DataFrames."""
	unique: set[str] = set()
	for df in dataframes:
		if not df.empty and "doi" in df.columns:
			unique.update(df["doi"].dropna().str.lower().str.strip())
	return sorted(unique)


def get_crossref_records_by_doi(dois: list[str]) -> pd.DataFrame:
	"""
	Fetch Crossref records for a list of DOIs.
	Returns one row per (work, author) with affiliation info included.
	"""
	records = []
	for doi in dois:
		data = get_by_doi(doi)
		if not data:
			continue
		msg = data.get("message", {})
		title_list = msg.get("title") or []
		journal_list = msg.get("container-title") or []
		for author in msg.get("author", []):
			affiliations = author.get("affiliation") or []
			affiliation = "; ".join(
				a.get("name", "") for a in affiliations if a.get("name")
			) or None
			records.append({
				"doi": msg.get("DOI", doi),
				"title": title_list[0] if title_list else None,
				"journal": journal_list[0] if journal_list else None,
				"type": msg.get("type"),
				"year": msg.get("published-print", msg.get("published-online", {}))
					.get("date-parts", [[None]])[0][0],
				"author_given": author.get("given"),
				"author_family": author.get("family"),
				"affiliation": affiliation,
			})
	return pd.DataFrame(records)


# ── Step 6 helpers ────────────────────────────────────────────────────────────

def filter_to_usd(crossref_df: pd.DataFrame) -> pd.DataFrame:
	"""Keep only works where at least one author lists USD as their affiliation."""
	if crossref_df.empty:
		return crossref_df

	usd_mask = (
		crossref_df["affiliation"]
		.fillna("")
		.str.lower()
		.str.contains(USD_NAME, regex=False)
	)
	usd_dois = set(crossref_df.loc[usd_mask, "doi"])
	return crossref_df[crossref_df["doi"].isin(usd_dois)].copy()


# ── Pipeline ──────────────────────────────────────────────────────────────────

### Baseline Sources
# Step 1. Find ORCID from emp_id_tbl

# --- Config ---
LIMIT = 0                          # set to 0 for all rows
AFFILIATION = "University of San Diego"
ROWS = 200                          # Crossref works to scan per author during ORCID search
WORKS_ROWS = 20                     # works to fetch per ORCID
THROTTLE = 0.1                      # seconds between API calls

### Retreive ORCIDs from emp_id_tbl
engine = get_sqlalchemy_engine()
with engine.connect() as con:
    query = text("""
        SELECT e.res_id,
               CONCAT(emp.first_name, ' ', emp.last_name) AS author_name,
               e.orcid AS best_orcid
        FROM emp_id_tbl e
        JOIN emp_tbl emp ON e.res_id = emp.res_id
        WHERE e.orcid IS NOT NULL
        {limit_clause}
    """.format(limit_clause="LIMIT :limit" if LIMIT > 0 else ""))
    params = {"limit": LIMIT} if LIMIT > 0 else {}
    orcid_df = pd.read_sql(query, con, params=params)

orcid_rows = orcid_df[orcid_df["best_orcid"].notna()].copy()

# Step 2 — Resolve each ORCID to its OpenAlex author ID
print("\nStep 2: Finding OpenAlex IDs...")

openalex_id_rows: list[dict] = []
for _, row in orcid_rows.iterrows():
	orcid = str(row["best_orcid"]).strip()
	works_df = openalex_works_by_orcid(orcid)

	# get_works_by_orcid already filters authorships down to this ORCID,
	# so any openalex_author_id present belongs to this researcher.
	if works_df.empty:
		alex_ids: list[str] = []
	else:
		alex_ids = [
			aid.split("/")[-1]
			for aid in works_df["openalex_author_id"].dropna().unique()
		]

	for alex_id in alex_ids or [None]:
		openalex_id_rows.append({
			"res_id": row["res_id"],
			"author_name": row["author_name"],
			"orcid": orcid,
			"alex_id": alex_id,
		})

	print(f"  {row['author_name']} ({orcid}) -> {alex_ids or 'no OpenAlex ID'}")
	if THROTTLE > 0:
		sleep(THROTTLE)

openalex_id_df = pd.DataFrame(openalex_id_rows)
matched = openalex_id_df["alex_id"].notna().sum()
print(
	f"\n{matched} of {len(orcid_rows)} ORCID(s) resolved to an OpenAlex author ID "
	f"({len(openalex_id_df)} row(s), including multi-ID authors)."
)

### Public records:
### crossref_orcid_df
### openalex_all_df
print("\nNumber of Crossref works by ORCID:", len(crossref_orcid_df))
print("\nNumber of OpenAlex works by ORCID:", len(openalex_orcid_df))

### Private Sources
# Step 4 — WOS by researcher ID
print("\nStep 4: WOS works...")
api_instance = create_wos_api_instance()
wos_id = get_best_wos_id(RES_ID, api_instance)
print(f"  Best WOS ID: {wos_id or 'not found'}")
wos_df = get_wos_works_by_wos_id(wos_id, api_instance) if wos_id else pd.DataFrame()

### Private records:
### wos_df
print("\nNumber of WOS works by researcher ID:", len(wos_df))

## Merge baseline sources into a common schema
print("\nMerging baseline sources...")
merged_df = merge_source_dfs([
    (crossref_orcid_df, "crossref",  {"doi": "doi",  "title": "title",      "year": "year",                   "journal": "journal"}),
    (openalex_all_df,   "openalex",  {"doi": "doi",  "title": "work_title", "year": "work_publication_year",  "journal": "work_source"}),
    (wos_df,            "wos",       {"doi": "doi",  "title": "title",      "year": "publish_year",           "journal": "source"}),
])
print(f"  {len(merged_df)} total row(s) across {merged_df['source_db'].nunique()} source(s)")
print(merged_df.to_string(index=False) if not merged_df.empty else "  (empty)")

# Fully identical rows (same source_db + all columns)
exact_dupes = merged_df[merged_df.duplicated(keep=False)]
print(f"\n  Exact duplicate rows: {len(exact_dupes)}")

# Same DOI found in more than one source
doi_sources = (
    merged_df.dropna(subset=["doi"])
    .groupby("doi")["source_db"]
    .apply(lambda s: sorted(s.unique().tolist()))
)
cross_source = doi_sources[doi_sources.map(len) > 1]
print(f"  DOIs found in multiple sources: {len(cross_source)}")
if not cross_source.empty:
    print(cross_source.to_string())

# Records with no DOI — cannot be deduplicated or looked up by DOI
no_doi = merged_df[merged_df["doi"].isna()]
print(f"\n  Records with no DOI: {len(no_doi)}")
if not no_doi.empty:
    print(no_doi.to_string(index=False))

# Deduplicate: one row per DOI (keep first source), preserve all null-DOI rows
merged_deduped_df = pd.concat([
    merged_df[merged_df["doi"].notna()].drop_duplicates(subset=["doi"], keep="first"),
    no_doi,
], ignore_index=True)

# Deduplicate by title/year/journal (case-insensitive).
# If two rows share the same content but have conflicting DOIs, keep both.
content_cols = ["title", "year", "journal"]
has_content = merged_deduped_df[content_cols].notna().all(axis=1)
to_dedup = merged_deduped_df[has_content].copy()
no_content_rows = merged_deduped_df[~has_content]

norm = to_dedup[content_cols].apply(lambda c: c.str.lower().str.strip() if c.dtype == object else c)
to_dedup["_key"] = list(zip(norm["title"], norm["year"], norm["journal"]))

keep_idx = []
for _, group in to_dedup.groupby("_key", sort=False):
    distinct_dois = group["doi"].dropna().str.lower().str.strip().unique()
    if len(distinct_dois) <= 1:
        keep_idx.append(group.index[0])   # same or no DOI — keep one
    else:
        keep_idx.extend(group.index)      # conflicting DOIs — keep all

to_dedup = to_dedup.loc[keep_idx].drop(columns=["_key"])
merged_final_df = pd.concat([to_dedup, no_content_rows], ignore_index=True)

merged_final_df.to_csv(f"{RES_ID}_merged.csv", index=False)






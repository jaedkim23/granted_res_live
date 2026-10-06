import sys
import os
import pandas as pd
from sqlalchemy import text, bindparam
from config.settings import get_sqlalchemy_engine

testing_file = "ground_truth - 200.csv"

df = pd.read_csv(testing_file)

df = df[['res_id', 'first_name', 'last_name', 'author_name', 'crossref', 'orcid',
       'orcid + crossref', 'orcid_no_affiliation', 'orcid_no_last_name',
       'Correct ORCID ID', 'Comment']]

engine = get_sqlalchemy_engine()
emp_tbl = pd.read_sql("SELECT res_id, first_name, last_name FROM emp_tbl", engine)

emp_tbl['first_name_lower'] = emp_tbl['first_name'].str.strip().str.lower()
emp_tbl['last_name_lower'] = emp_tbl['last_name'].str.strip().str.lower()

df['first_name_lower'] = df['first_name'].str.strip().str.lower()
df['last_name_lower'] = df['last_name'].str.strip().str.lower()

df = df.drop(columns='res_id').merge(
    emp_tbl[['res_id', 'first_name_lower', 'last_name_lower']],
    on=['first_name_lower', 'last_name_lower'],
    how='left'
).drop(columns=['first_name_lower', 'last_name_lower'])

df = df[df['Correct ORCID ID'].notna()]

df_sub1 = df[df['Correct ORCID ID'].notna() & (df['Correct ORCID ID'].str.strip() != '')]
df_sub = df_sub1[df_sub1['res_id'].notna() & (df_sub1['res_id'] != 0)]

correct_orcids = df_sub['Correct ORCID ID'].str.strip().unique().tolist()

########################################
## Rest of the script assumes the correct ORCID

######################################
### From pre_pipeline_xx.py

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
ROWS = 200                          # Crossref works to scan per author during ORCID search
WORKS_ROWS = 20                     # works to fetch per ORCID
THROTTLE = 0.1                      # seconds between API calls


def get_author_works_by_orcid(orcid: str, rows: int = WORKS_ROWS) -> pd.DataFrame:
	works_data = get_works_by_orcid(orcid=orcid, rows=rows)
	return flatten_crossref_works(orcid=orcid, works_data=works_data)

from time import sleep

all_works = []
for orcid in correct_orcids:
    df = get_author_works_by_orcid(orcid)
    if not df.empty:
        # df.insert(0, "orcid", orcid)
        all_works.append(df)
    print(f"{orcid} -> {len(df)} work(s)")
    sleep(THROTTLE)

works_df = pd.concat(all_works, ignore_index=True) if all_works else pd.DataFrame()
works_df['Source'] = 'Crossref'

############################################################
### OpenAlex

# --- Config ---
LIMIT = 0                          # set to 0 for all rows
AFFILIATION = "University of San Diego"
WORKS_ROWS = 500                     # works to fetch per ORCID
THROTTLE = 0.1                      # seconds between API calls

from api.openalex_api import (
	get_works_by_orcid as openalex_works_by_orcid,
	get_author_works as openalex_author_works,
)

openalex_works = []
for orcid in correct_orcids:
    orcid = str(orcid).strip()
    df = openalex_works_by_orcid(orcid) if orcid else pd.DataFrame()
    if not df.empty:
        df = df[df['institution_name'].str.contains(AFFILIATION, case=False, na=False)]
    if not df.empty:
        openalex_works.append(df)
    print(f"{orcid} -> {len(df)} work(s) at '{AFFILIATION}'")
    sleep(THROTTLE)

openalex_works_df = pd.concat(openalex_works, ignore_index=True) if openalex_works else pd.DataFrame()
openalex_works_df['Source'] = 'OpenAlex'
print(f"\nOpenAlex: {len(openalex_works_df)} total work(s) across {len(correct_orcids)} ORCID(s)")
openalex_works_df.to_csv('openalex_works.csv', encoding='utf-8-sig', index=False)



############################################################
### Web of Science

from api.wos_api import create_wos_api_instance, get_wos_data_by_author_name

THROTTLE_WOS = 0.5

_orcid_to_author = df_sub.set_index(df_sub['Correct ORCID ID'].str.strip())['author_name'].to_dict()

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
            a.get('displayName', '')
            for a in (hit.get('names') or {}).get('authors', [])
        ]
        wos_rows.append({
            'orcid': orcid,
            'doi': ids.get('doi'),
            'title': hit.get('title'),
            'year': (hit.get('source') or {}).get('publishYear'),
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
wos_df.to_csv('wos_works.csv', encoding='utf-8-sig', index=False)

############################################################
### Combined / Deduplicated View

import re

def _normalize_title(title):
    if pd.isna(title):
        return ''
    t = str(title)
    # Normalize Unicode hyphens/dashes to ASCII hyphen
    for ch in '‐‑‒–—―−':
        t = t.replace(ch, '-')
    # Remove periods from abbreviations: U.S. -> US, U.S -> US
    t = re.sub(r'(?<=[A-Za-z])\.(?=[A-Za-z\-])', '', t)  # U.S.C or U.S.-China -> USC / US-China
    t = re.sub(r'(?<=[A-Za-z])\.(?=\s|$)', '', t)         # trailing dot in abbrev
    return re.sub(r'\s+', ' ', t.lower().strip())

def _normalize_doi(doi):
    if pd.isna(doi):
        return pd.NA
    d = str(doi).strip().lower()
    # OpenAlex returns full URLs; strip to bare DOI for cross-source matching
    d = re.sub(r'^https?://doi\.org/', '', d)
    return d if d else pd.NA

crossref_slim = works_df[['orcid', 'doi', 'title']].copy()
crossref_slim['doi_key'] = crossref_slim['doi'].apply(_normalize_doi)
crossref_slim['year'] = pd.NA
crossref_slim['author_name'] = pd.NA
crossref_slim['url'] = crossref_slim['doi'].apply(lambda d: f"https://doi.org/{d}" if pd.notna(d) else None)
crossref_slim['source_tag'] = 'Crossref'

openalex_slim = openalex_works_df[['orcid', 'doi', 'title', 'publication_year', 'author_name', 'work_id']].copy()
openalex_slim = openalex_slim.rename(columns={'publication_year': 'year'})
openalex_slim['doi_key'] = openalex_slim['doi'].apply(_normalize_doi)
openalex_slim['url'] = openalex_slim['work_id']
openalex_slim['source_tag'] = 'OpenAlex'

wos_slim = wos_df[['orcid', 'doi', 'title', 'year', 'author_name', 'url']].copy()
wos_slim['doi_key'] = wos_slim['doi'].apply(_normalize_doi)
wos_slim['source_tag'] = 'WoS'

COLS = ['orcid', 'doi_key', 'title', 'year', 'author_name', 'url', 'source_tag']
all_works = pd.concat([
    openalex_slim[COLS],
    crossref_slim[COLS],
    wos_slim[COLS],
], ignore_index=True)

all_works['title_key'] = all_works['title'].apply(_normalize_title)
all_works['work_key'] = all_works['doi_key'].where(all_works['doi_key'].notna(), all_works['title_key'])
all_works = all_works[all_works['work_key'].notna()]

def _first(s):
    return s.dropna().iloc[0] if s.notna().any() else pd.NA

combined_df = all_works.groupby(['orcid', 'work_key'], sort=False).agg(
    author_name=('author_name', _first),
    title=('title', _first),
    year=('year', _first),
    url=('url', _first),
    in_crossref=('source_tag', lambda s: 'Crossref' in s.values),
    in_openalex=('source_tag', lambda s: 'OpenAlex' in s.values),
    in_wos=('source_tag', lambda s: 'WoS' in s.values),
).reset_index().drop(columns='work_key')

orcid_to_name = df_sub.set_index(
    df_sub['Correct ORCID ID'].str.strip()
)['author_name'].to_dict()
combined_df['author_name'] = combined_df['author_name'].fillna(
    combined_df['orcid'].map(orcid_to_name)
)

n_crossref = combined_df['in_crossref'].sum()
n_openalex = combined_df['in_openalex'].sum()
n_wos = combined_df['in_wos'].sum()
print(f"\nCombined: {len(combined_df)} unique work(s) — Crossref: {n_crossref}, OpenAlex: {n_openalex}, WoS: {n_wos}")


## Remove duplicates based on middle name

def _normalize_author_name(name):
    if pd.isna(name):
        return ''
    # Remove parenthetical middle names: "Jeeyun (Sophia) Baik" -> "Jeeyun Baik"
    name = re.sub(r'\s*\([^)]*\)', '', str(name)).strip()
    # Strip middle names: keep only first and last word
    parts = name.split()
    if len(parts) > 2:
        name = f"{parts[0]} {parts[-1]}"
    return name.lower().strip()

combined_df['_author_key'] = combined_df['author_name'].apply(_normalize_author_name)
combined_df['_title_key'] = combined_df['title'].apply(_normalize_title)

dedup_key = combined_df['_author_key'] + '||' + combined_df['_title_key']
combined_df = combined_df[~dedup_key.duplicated(keep='first')].drop(
    columns=['_author_key', '_title_key']
).reset_index(drop=True)

combined_df = combined_df.sort_values('orcid').reset_index(drop=True)
combined_df.to_csv('pipeline_testing_WOS.csv', encoding='utf-8-sig')



### 

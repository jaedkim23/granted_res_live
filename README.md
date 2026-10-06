# USD Research Publications Tracker

Tracks faculty publications at the University of San Diego by integrating
employee HR data with the Web of Science (WOS) and OpenAlex research databases.

Slack workspace: https://usdnsfgranted.slack.com

## Stats

1. **ORCID identification:** 1404 employees have ORCIDs identified
   - *1.1* ORCID evaluation set results:
   - Valid test set of 52 IDs
   - 48 ORCID values, 4 missing res_id (newer faculty)
   - 45 correct ORCID, 3 incorrect ORCID
   - Last update: 37 matches
2. **ORCID/Crossref work identification:** 8993 works
   - *2.1* Evaluation set results:
3. **OpenAlex coverage:**
   - *3.1* OpenAlex identification
   - 27 ORCIDs resuls in testing set - 22 OpenAlex IDs found, 4 NAs
   - 
   - *3.2* OpenAlex work identification
4. **WOS coverage:**


## Project Structure

### Ignore for now (not updated, refer to Google Drive folder 9/10/2026)
```
granted_res/
├── config/
│   └── settings.py             # DB connection factories (shared by all modules)
├── api/
│   ├── wos_api.py              # Clarivate WOS API calls
│   ├── openalex_api.py         # OpenAlex API calls
│   └── crossref_api.py         # Crossref API calls
├── etl/
│   ├── employees.py            # Load employee CSV → emp_tbl, fac_tbl, nonfac_tbl
│   ├── organizations.py        # Sync USD directory → emp_orgs
│   ├── id_matching.py          # Find/validate WOS & OpenAlex researcher IDs
│   ├── wos_publications.py     # Fetch WOS publications → wos_tbl, wos_work_tbl
│   └── openalex_publications.py # Fetch OpenAlex pubs → open_alex_tbl
├── scripts/                    # Runnable entry points (run in order below)
│   ├── import_smse.py          # 1. Import SMSE FT faculty reference table
│   ├── import_employees.py     # 2. Import all employees from HR CSV
│   ├── import_organizations.py # 3. Sync org directory memberships
│   ├── find_researcher_ids.py  # 4. Find WOS + OpenAlex IDs for all faculty
│   ├── update_wos_publications.py      # 5a. Pull WOS publications
│   ├── update_openalex_publications.py # 5b. Pull OpenAlex publications
│   └── cleanup_id_lookup.py   # (maintenance) Remove duplicate ID rows
└── sql/
    ├── CreateTables_v4.sql     # Full database schema
    └── test_queries.sql        # Ad-hoc analysis queries
```

## Setup

1. Copy `.env` and fill in your credentials:
   ```
   HOST=...
   DATABASE=...
   PASSWORD=...
   PORT=3306
   OUR_API=<Clarivate WOS API key>
   ```

2. Create database tables (do not need to rerun once DB has been created):
   ```sql
   source sql/CreateTables_v4.sql
   ```

3. Install dependencies:
   ```
   pip install pandas sqlalchemy mysql-connector-python python-dotenv requests clarivate-wos-starter
   ```

## Running the Pipeline

Run scripts in order from the project root:

```bash
python -m scripts.import_smse
python -m scripts.import_employees
python -m scripts.import_organizations
python -m scripts.find_researcher_ids
python -m scripts.update_wos_publications
python -m scripts.update_openalex_publications
```

Most scripts accept `--start` / `--end` arguments for batch processing:
```bash
python -m scripts.update_wos_publications --start 0 --end 100
```

## Maintenance

Remove duplicate ID lookup entries:
```bash
python -m scripts.cleanup_id_lookup
```

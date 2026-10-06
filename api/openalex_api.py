"""
OpenAlex API functions.

All OpenAlex API interactions go through this module.
Uses the public OpenAlex REST API (no key required).
"""

import requests
import pandas as pd
import os
from dotenv import load_dotenv

load_dotenv()

OPENALEX_BASE = "https://api.openalex.org"
USD_INSTITUTION_ID = "https://openalex.org/I160856358"
USD_API = os.getenv('OPEN_ALEX_API')


WORK_SELECT_FIELDS = ",".join((
    'id', 'ids', 'title', 'display_name', 'publication_year',
    'publication_date', 'primary_location', 'open_access',
    'authorships', 'cited_by_count', 'is_retracted',
    'is_paratext', 'updated_date', 'created_date',
))


def get_author_works(author_id: str, api_key=USD_API) -> pd.DataFrame:
    """
    Retrieve all works for a given OpenAlex author ID using cursor pagination.

    Returns a DataFrame with one row per (work, authorship, institution) combination,
    filtered to only include rows where the author matches author_id.

    Args:
        author_id: OpenAlex author ID (e.g., 'A1234567890')

    Returns:
        pd.DataFrame with columns:
            work_id, work_title, work_display_name, work_publication_year,
            work_publication_date, work_source, author_id, author_name,
            author_position, institution_id, institution_name,
            institution_country_code
    """
    base_url = f"{OPENALEX_BASE}/works?filter=author.id:{author_id}&api_key={api_key}"
    cursor = '*'
    works = []
    loop_index = 0

    while cursor:
        url = f"{base_url}&select={WORK_SELECT_FIELDS}&cursor={cursor}"
        page = requests.get(url).json()
        works.extend(page['results'])
        cursor = page['meta']['next_cursor']
        loop_index += 1
        if loop_index in [5, 10, 20, 50, 100] or loop_index % 500 == 0:
            print(f'{loop_index} API requests made so far')

    print(f'Done. Made {loop_index} requests, collected {len(works)} works.')

    rows = []
    for work in works:
        source = None
        if work['primary_location'] and work['primary_location']['source']:
            source = work['primary_location']['source']['display_name']

        for authorship in work.get('authorships', []):
            if not authorship:
                continue
            author = authorship.get('author')
            a_id = author['id'] if author else None
            a_name = author['display_name'] if author else None
            a_pos = authorship.get('author_position')

            for institution in authorship.get('institutions', []):
                if not institution:
                    continue
                rows.append({
                    'work_id': work['id'],
                    'doi': (work.get('ids') or {}).get('doi'),
                    'work_title': work['title'],
                    'work_display_name': work['display_name'],
                    'work_publication_year': work['publication_year'],
                    'work_publication_date': work['publication_date'],
                    'work_source': source,
                    'author_id': a_id,
                    'author_name': a_name,
                    'author_position': a_pos,
                    'institution_id': institution['id'],
                    'institution_name': institution['display_name'],
                    'institution_country_code': institution['country_code'],
                })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df[df['author_id'] == f"https://openalex.org/{author_id}"]


def get_author_works_at_usd(author_id: str, api_key=USD_API) -> pd.DataFrame:
    """
    Retrieve works for an author filtered to USD affiliation only.

    Args:
        author_id: OpenAlex author ID

    Returns:
        pd.DataFrame with columns: work_id, work_title,
            work_publication_year, work_source
    """
    df = get_author_works(author_id, api_key)
    if df.empty:
        return df
    df = df[df['institution_id'] == USD_INSTITUTION_ID]
    return df[['work_id', 'work_title', 'work_publication_year', 'work_source']]


def search_authors_by_name(name: str, api_key=USD_API) -> list[dict]:
    """
    Search OpenAlex authors by display name using cursor pagination.

    Args:
        name: Author name to search

    Returns:
        List of author result dicts from OpenAlex
    """
    results = []
    cursor = '*'
    loop_index = 0

    while cursor:
        url = f"{OPENALEX_BASE}/authors?search={name}&cursor={cursor}&api_key={api_key}"
        page = requests.get(url).json()
        if page['meta']['count'] == 0:
            break
        results.extend(page['results'])
        cursor = page['meta']['next_cursor']
        loop_index += 1
        if loop_index in [5, 10, 20, 50, 100] or loop_index % 500 == 0:
            print(f'{loop_index} API requests made so far')

    return results


def get_works_by_orcid(orcid: str, api_key=USD_API) -> pd.DataFrame:
    """
    Retrieve all OpenAlex works where an author matches the given ORCID.

    Args:
        orcid: ORCID in 0000-0000-0000-0000 format (with or without URL prefix)

    Returns:
        pd.DataFrame with columns:
            orcid, work_id, title, publication_year, source,
            doi, author_name, institution_name
    """
    orcid = orcid.replace("https://orcid.org/", "").replace("http://orcid.org/", "").strip()
    base_url = (
        f"{OPENALEX_BASE}/works"
        f"?filter=authorships.author.orcid:{orcid}"
        f"&select={WORK_SELECT_FIELDS}"
    )
    if api_key:
        base_url += f"&api_key={api_key}"

    cursor = '*'
    rows: list[dict] = []

    while cursor:
        url = f"{base_url}&cursor={cursor}"
        page = requests.get(url).json()
        results = page.get('results', [])

        for work in results:
            doi = (work.get('ids') or {}).get('doi')
            source = None
            if work.get('primary_location') and work['primary_location'].get('source'):
                source = work['primary_location']['source'].get('display_name')

            for authorship in work.get('authorships', []):
                author = authorship.get('author') or {}
                author_orcid = (author.get('orcid') or '').replace("https://orcid.org/", "")
                if author_orcid != orcid:
                    continue

                institutions = authorship.get('institutions') or [{}]
                for institution in institutions:
                    rows.append({
                        'orcid': orcid,
                        'openalex_author_id': author.get('id'),
                        'work_id': work.get('id'),
                        'title': work.get('title'),
                        'publication_year': work.get('publication_year'),
                        'source': source,
                        'doi': doi,
                        'author_name': author.get('display_name'),
                        'institution_name': institution.get('display_name'),
                    })

        cursor = page.get('meta', {}).get('next_cursor')

    return pd.DataFrame(rows)


def get_author_details(author_id: str, api_key=USD_API) -> dict:
    """
    Retrieve full author details from OpenAlex by author ID.

    Args:
        author_id: OpenAlex author ID (e.g., 'A1234567890')

    Returns:
        Author details dict from OpenAlex
    """
    url = f"{OPENALEX_BASE}/authors/{author_id}&api_key={api_key}"
    return requests.get(url).json()

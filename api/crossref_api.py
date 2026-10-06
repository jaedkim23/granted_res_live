"""
Crossref API functions.

Provides wrappers for searching publications via the Crossref REST API.
No API key required, but set a descriptive User-Agent to avoid rate limiting.
"""

import requests
import pandas as pd

CROSSREF_BASE = "https://api.crossref.org"
HEADERS = {
    'User-Agent': 'USD-Research-Tracker/1.0 (mailto:research@sandiego.edu)'
}


def _normalize_orcid(orcid: str) -> str:
    """Normalize ORCID values to canonical 0000-0000-0000-0000 format."""
    if not orcid:
        return ""

    value = orcid.strip()
    value = value.replace("https://orcid.org/", "")
    value = value.replace("http://orcid.org/", "")
    return value


def _normalize_doi(doi: str) -> str:
    """Normalize DOI values for case-insensitive matching."""
    if not doi:
        return ""
    return doi.strip().lower()


def _extract_member_id(member_value: str) -> str | None:
    """Extract numeric Crossref member ID from a member URI or raw ID value."""
    if not member_value:
        return None

    candidate = member_value.strip().rstrip('/')
    if '/' in candidate:
        candidate = candidate.split('/')[-1]

    return candidate or None


def _author_name_matches(author: dict, author_name: str) -> bool:
    """Return True when the author record appears to match the target name."""
    target_tokens = {token for token in author_name.lower().split() if token}
    if not target_tokens:
        return True

    given = (author.get('given') or '').lower()
    family = (author.get('family') or '').lower()
    literal = (author.get('literal') or '').lower()
    author_text = f"{given} {family} {literal}".strip()

    return all(token in author_text for token in target_tokens)


def _affiliation_matches(author: dict, affiliation_text: str) -> bool:
    """Return True if any affiliation string contains the target affiliation text."""
    if not affiliation_text:
        return False

    target = affiliation_text.lower().strip()
    affiliations = author.get('affiliation', [])
    for aff in affiliations:
        aff_name = (aff.get('name') or '').lower()
        if target in aff_name:
            return True
    return False


def search_by_title(title: str, rows: int = 10) -> dict | None:
    """
    Search Crossref works by title.

    Args:
        title: Publication title to search
        rows: Number of results to return (default 10)

    Returns:
        Crossref API response dict, or None on error
    """
    response = requests.get(
        f"{CROSSREF_BASE}/works",
        headers=HEADERS,
        params={'query.title': title, 'rows': rows}
    )
    if response.status_code == 200:
        return response.json()
    print(f"Crossref error {response.status_code} for title: {title}")
    return None


def get_by_doi(doi: str) -> dict | None:
    """
    Retrieve a Crossref work record by DOI.

    Args:
        doi: DOI string (e.g., '10.1000/xyz123')

    Returns:
        Crossref API response dict, or None on error
    """
    response = requests.get(f"{CROSSREF_BASE}/works/{doi}", headers=HEADERS)
    if response.status_code == 200:
        return response.json()
    print(f"Crossref error {response.status_code} for DOI: {doi}")
    return None


def get_member_by_doi(doi: str) -> dict | None:
    """
    Retrieve Crossref member information for a DOI.

    Args:
        doi: DOI string (e.g., '10.1000/xyz123')

    Returns:
        {
            'doi': str,
            'member': str | None,
            'member_id': str | None,
        }
        or None when the DOI lookup fails.
    """
    data = get_by_doi(doi)
    if not data:
        return None

    message = data.get('message', {})
    member = message.get('member')
    member_id = _extract_member_id(member) if member else None

    return {
        'doi': message.get('DOI', doi),
        'member': member,
        'member_id': member_id,
    }


def search_by_author(author_name: str, rows: int = 20) -> dict | None:
    """
    Search Crossref works by author name.

    Args:
        author_name: Author name to search
        rows: Number of results to return (default 20)

    Returns:
        Crossref API response dict, or None on error
    """
    response = requests.get(
        f"{CROSSREF_BASE}/works",
        headers=HEADERS,
        params={'query.author': author_name, 'rows': rows}
    )
    if response.status_code == 200:
        return response.json()
    print(f"Crossref error {response.status_code} for author: {author_name}")
    return None


def find_orcids_by_author(author_name: str, rows: int = 100) -> list[str]:
    """
    Find candidate ORCIDs for an author by scanning Crossref work authors.

    Args:
        author_name: Author name to search
        rows: Number of works to inspect for ORCID extraction

    Returns:
        Sorted unique list of normalized ORCID values
    """
    data = search_by_author(author_name=author_name, rows=rows)
    if not data:
        return []

    orcids: set[str] = set()
    items = data.get('message', {}).get('items', [])

    for item in items:
        for author in item.get('author', []):
            orcid = _normalize_orcid(author.get('ORCID', ''))
            if orcid:
                orcids.add(orcid)

    return sorted(orcids)


def get_works_by_orcid(orcid: str, rows: int = 20) -> dict | None:
    """
    Retrieve works in Crossref associated with an ORCID.

    Args:
        orcid: ORCID value (with or without URL prefix)
        rows: Number of results to return

    Returns:
        Crossref API response dict, or None on error
    """
    normalized_orcid = _normalize_orcid(orcid)
    if not normalized_orcid:
        return None
    response = requests.get(
        f"{CROSSREF_BASE}/works",
        headers=HEADERS,
        params={'filter': f'orcid:{normalized_orcid}', 'rows': rows}
    )
    if response.status_code == 200:
        return response.json()
    print(f"Crossref error {response.status_code} for ORCID: {normalized_orcid}")
    return None


def flatten_crossref_works(orcid: str, works_data: dict | None) -> pd.DataFrame:
	if not works_data:
		return pd.DataFrame()

	items = works_data.get("message", {}).get("items", [])
	records: list[dict[str, object]] = []

	for item in items:
		title_list = item.get("title", [])
		title = title_list[0] if title_list else None

		publish_year = None
		for date_field in ("issued", "published-print", "published-online", "created"):
			date_parts = item.get(date_field, {}).get("date-parts", [[None]])
			if date_parts and date_parts[0] and date_parts[0][0]:
				publish_year = date_parts[0][0]
				break

		issn_types = item.get("issn-type", [])
		issn = next((x["value"] for x in issn_types if x.get("type") == "print"), None)
		eissn = next((x["value"] for x in issn_types if x.get("type") == "electronic"), None)

		records.append(
			{
				"orcid": orcid,
				"doi": item.get("DOI"),
				"title": title,
				"publisher": item.get("publisher"),
				"type": item.get("type"),
				"publish_year": publish_year,
				"issn": issn,
				"eissn": eissn,
			}
		)

	return pd.DataFrame(records)


def disambiguate_orcid_by_affiliation_and_doi(
    author_name: str,
    affiliation: str = "University of San Diego",
    known_dois: list[str] | None = None,
    rows: int = 200
) -> dict | None:
    """
    Pick the best ORCID candidate for an author using DOI and affiliation evidence.

    Ranking priority:
    1) Known DOI matches
    2) Affiliation matches
    3) Name/evidence frequency

    Returns:
        {
            'best_orcid': str | None,
            'candidates': [
                {
                    'orcid': str,
                    'score': int,
                    'doi_matches': int,
                    'affiliation_hits': int,
                    'name_hits': int,
                    'evidence_count': int,
                    'matched_dois': list[str],
                }
            ]
        }
        or None when Crossref call fails.
    """
    data = search_by_author(author_name=author_name, rows=rows)
    if data is None:
        return None

    known_doi_set = {
        _normalize_doi(doi)
        for doi in (known_dois or [])
        if _normalize_doi(doi)
    }

    candidates: dict[str, dict] = {}
    items = data.get('message', {}).get('items', [])

    for item in items:
        item_doi = _normalize_doi(item.get('DOI', ''))

        for author in item.get('author', []):
            if not _author_name_matches(author, author_name):
                continue

            orcid = _normalize_orcid(author.get('ORCID', ''))
            if not orcid:
                continue

            candidate = candidates.setdefault(
                orcid,
                {
                    'orcid': orcid,
                    'evidence_count': 0,
                    'name_hits': 0,
                    'affiliation_hits': 0,
                    'matched_dois': set(),
                }
            )

            candidate['evidence_count'] += 1
            candidate['name_hits'] += 1

            if _affiliation_matches(author, affiliation):
                candidate['affiliation_hits'] += 1

            if item_doi and item_doi in known_doi_set:
                candidate['matched_dois'].add(item_doi)

    scored_candidates = []
    for candidate in candidates.values():
        doi_matches = len(candidate['matched_dois'])
        affiliation_hits = candidate['affiliation_hits']
        name_hits = candidate['name_hits']
        evidence_count = candidate['evidence_count']

        # DOI matches dominate, then affiliation, then frequency-based tie breakers.
        score = (doi_matches * 100) + (affiliation_hits * 10) + (name_hits * 2) + evidence_count

        scored_candidates.append(
            {
                'orcid': candidate['orcid'],
                'score': score,
                'doi_matches': doi_matches,
                'affiliation_hits': affiliation_hits,
                'name_hits': name_hits,
                'evidence_count': evidence_count,
                'matched_dois': sorted(candidate['matched_dois']),
            }
        )

    scored_candidates.sort(
        key=lambda x: (
            x['doi_matches'],
            x['affiliation_hits'],
            x['name_hits'],
            x['evidence_count'],
            x['score'],
        ),
        reverse=True,
    )

    best_orcid = scored_candidates[0]['orcid'] if scored_candidates else None

    return {
        'best_orcid': best_orcid,
        'candidates': scored_candidates,
    }


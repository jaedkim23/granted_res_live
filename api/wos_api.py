"""
Web of Science (Clarivate) API functions.

All WOS API interactions go through this module.
Requires the clarivate-wos-starter Python package and an API key in .env.
"""

import os
import datetime

import clarivate.wos_starter.client
from clarivate.wos_starter.client.rest import ApiException
from dotenv import load_dotenv

load_dotenv()

CURRENT_YEAR = datetime.datetime.now().year
WOS_HOST = "https://api.clarivate.com/apis/wos-starter/v1"


def create_wos_api_instance(api_key: str = None) -> clarivate.wos_starter.client.DocumentsApi:
    """
    Create and return a WOS DocumentsApi instance.

    Args:
        api_key: Clarivate API key. Defaults to OUR_API env var.

    Returns:
        clarivate.wos_starter.client.DocumentsApi
    """
    api_key = api_key or os.getenv('OUR_API')
    configuration = clarivate.wos_starter.client.Configuration(host=WOS_HOST)
    configuration.api_key['ClarivateApiKeyAuth'] = api_key
    return clarivate.wos_starter.client.DocumentsApi(
        clarivate.wos_starter.client.ApiClient(configuration)
    )


def _documents_get(api_instance, query: str, page_n: int):
    """
    Internal helper: call the WOS documents endpoint with standard parameters.

    Args:
        api_instance: WOS DocumentsApi instance
        query: WOS advanced search query string
        page_n: Page number (1-based)

    Returns:
        DocumentsList API response, or None on error
    """
    try:
        return api_instance.documents_get(
            query,
            db='WOS',
            limit=50,
            page=page_n,
            sort_field='LD+D',
            modified_time_span=None,
            tc_modified_time_span=None,
            detail=None,
        )
    except ApiException as e:
        print(f"WOS API error: {e}")
        return None


def get_wos_data_by_author_name(api_instance, page_n: int, author: str):
    """
    Search WOS publications by author name and USD affiliation.

    Query format: AU={author} AND OG=University of San Diego AND PY=(2000-{year})

    Args:
        api_instance: WOS DocumentsApi instance
        page_n: Page number
        author: Author name in "Last, First" format

    Returns:
        DocumentsList API response
    """
    query = f'AU={author} AND OG=University of San Diego AND PY=(2000-{CURRENT_YEAR})'
    return _documents_get(api_instance, query, page_n)


def get_wos_data_by_author_id(api_instance, page_n: int, author_id: str):
    """
    Search WOS publications by WOS researcher ID (AI parameter).

    Args:
        api_instance: WOS DocumentsApi instance
        page_n: Page number
        author_id: WOS researcher ID (e.g., 'AAA-1234-2020')

    Returns:
        DocumentsList API response
    """
    query = f'AI={author_id}'
    return _documents_get(api_instance, query, page_n)



def get_wos_data_by_author_id_and_org(api_instance, page_n: int, author_id: str):
    """
    Search WOS publications by researcher ID filtered to USD and year range.

    Args:
        api_instance: WOS DocumentsApi instance
        page_n: Page number
        author_id: WOS researcher ID

    Returns:
        DocumentsList API response
    """
    query = f'AI={author_id} AND OG=University of San Diego AND PY=(2000-{CURRENT_YEAR})'
    return _documents_get(api_instance, query, page_n)


def get_wos_known_names(api_instance, author_id: str) -> list[str]:
    """
    Retrieve all known display names associated with a WOS researcher ID.

    Returns names in "First Last" order (converted from WOS "Last, First" format).

    Args:
        api_instance: WOS DocumentsApi instance
        author_id: WOS researcher ID

    Returns:
        List of unique author name strings
    """
    try:
        response = get_wos_data_by_author_id(api_instance, 1, author_id)
        data = response.to_dict()
        display_names = []
        standard_names = []

        for work in data.get('hits', []):
            for author in work.get('names', {}).get('authors', []):
                if author.get('researcherId') == author_id:
                    display_names.append(author['displayName'])
                    standard_names.append(author['wosStandard'])

        all_names = list(set(display_names)) + list(set(standard_names))

        # Convert "Last, First" to "First Last"
        converted = []
        for name in all_names:
            parts = name.split(', ')
            if len(parts) >= 2:
                converted.append(f"{parts[1]} {parts[0]}")
            else:
                converted.append(name)

        return converted

    except ApiException as e:
        print(f"WOS API error in get_wos_known_names: {e}")
        return []

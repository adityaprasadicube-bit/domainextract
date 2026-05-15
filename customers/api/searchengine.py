import os
import requests

# SEARCH_ENGINE_URL = os.getenv(
#     "SEARCH_ENGINE_URL",
#     "http://host.docker.internal:5000/search/"
#     # "192.168.1.102:5000/search/"
#
# )
#
# def search_ip(ips):
#     if not ips:
#         return {"results": []}
#
#     try:
#         resp = requests.post(
#             SEARCH_ENGINE_URL,
#             json={"ips": ips},
#             timeout=10
#         )
#         resp.raise_for_status()
#         return resp.json()
#
#     except Exception as e:
#         print("Search engine call failed:", e)
#         return {"results": []}
import requests


def search_ip(ips):
    """
    Search for IP information via API.
    Returns empty results if the request fails instead of raising an error.
    """
    # Handle edge case: empty input
    if not ips:
        return {"results": []}

    try:
        payload = {
            "ips": ips
        }
        resp = requests.post(
            # 'http://122.175.12.225:5000/search/',
            
            'http://192.168.1.163:5000/search/',
             #'http://localhost:5000/search/',
            # 'http://host.docker.internal:5000/search/',
            json=payload,
            timeout=10  # Add timeout to prevent hanging
        )
        resp.raise_for_status()  # Raise an exception for bad status codes
        ip_info = resp.json()

        # Ensure the response has the expected structure
        if not isinstance(ip_info, dict) or "results" not in ip_info:
            print(f"Warning: Unexpected response structure from search_ip API")
            return {"results": []}

        return ip_info

    except requests.exceptions.Timeout:
        print(f"Timeout error while fetching IP information (>10s)")
        return {"results": []}

    except requests.exceptions.ConnectionError:
        print(f"Connection error: Unable to reach IP search service at http://192.168.1.102:5050/search/")
        return {"results": []}

    except requests.exceptions.HTTPError as e:
        print(f"HTTP error fetching IP information: {e.response.status_code} - {e.response.reason}")
        return {"results": []}

    except requests.exceptions.RequestException as e:
        # Catch any other request-related errors
        print(f"Request error fetching IP information: {e}")
        return {"results": []}

    except ValueError as e:
        # JSON decode errors
        print(f"Error decoding JSON response from search_ip: {e}")
        return {"results": []}

    except Exception as e:
        # Catch any other unexpected errors
        print(f"Unexpected error in search_ip: {e}")
        return {"results": []}
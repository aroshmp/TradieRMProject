"""
tradiePrototype/services/distance_service.py

UC7 -- Road distance calculation via the OpenRouteService (ORS) API.

OpenRouteService is a free, open-source routing service built on
OpenStreetMap data. It requires no credit card to obtain an API key.

Free tier: 2,000 requests per day -- sufficient for a tradie business
allocating a small number of jobs per day.

API key registration: https://openrouteservice.org/dev/#/signup

The service performs two operations:
    1. Geocode a plain-text address to (longitude, latitude) coordinates.
    2. Calculate the road distance in kilometres between two coordinates.

Both operations use Python's built-in urllib -- no third-party HTTP
libraries are required, consistent with the project's approach for Ollama.

Configuration (add to settings.py or .env):
    ORS_API_KEY = 'your_openrouteservice_api_key'

If the API key is not configured or the API is unreachable, the service
returns None and logs a warning. The caller is responsible for deciding
how to handle a None result (e.g. allow manual entry, raise an error).
"""

import json
import logging
import urllib.request
import urllib.parse
import urllib.error

from django.conf import settings

logger = logging.getLogger(__name__)

# Base URLs for the OpenRouteService API endpoints used by this service.
ORS_GEOCODE_URL   = 'https://api.openrouteservice.org/geocode/search'
ORS_DIRECTIONS_URL = 'https://api.openrouteservice.org/v2/directions/driving-car'

# Returned when distance cannot be calculated, so the caller can detect failure.
DISTANCE_UNAVAILABLE = None


def get_road_distance_km(origin_address: str, destination_address: str) -> float | None:
    """
    Calculate the road distance in kilometres between two plain-text addresses.

    Steps:
        1. Geocode origin_address to (lng, lat).
        2. Geocode destination_address to (lng, lat).
        3. Request the driving route between the two coordinate pairs.
        4. Extract and return the distance in kilometres.

    Returns the distance as a float rounded to 2 decimal places,
    or None if geocoding or routing fails for either address.

    Args:
        origin_address:      Plain-text address of the starting point.
        destination_address: Plain-text address of the destination.
    """
    api_key = getattr(settings, 'ORS_API_KEY', None)
    if not api_key:
        logger.warning(
            "ORS_API_KEY is not configured in settings. Distance calculation skipped."
        )
        return DISTANCE_UNAVAILABLE

    origin_coords = _geocode_address(origin_address, api_key)
    if origin_coords is None:
        logger.warning("Geocoding failed for origin address: '%s'", origin_address)
        return DISTANCE_UNAVAILABLE

    destination_coords = _geocode_address(destination_address, api_key)
    if destination_coords is None:
        logger.warning(
            "Geocoding failed for destination address: '%s'", destination_address
        )
        return DISTANCE_UNAVAILABLE

    distance_m = _get_route_distance_metres(origin_coords, destination_coords, api_key)
    if distance_m is None:
        return DISTANCE_UNAVAILABLE

    # Convert metres to kilometres and round to 2 decimal places.
    return round(distance_m / 1000, 2)


def _geocode_address(address: str, api_key: str) -> tuple | None:
    """
    Convert a plain-text address to (longitude, latitude) coordinates
    using the ORS Geocode Search endpoint.

    Returns a (longitude, latitude) tuple, or None if the request fails
    or no results are returned.

    Args:
        address: Plain-text address string to geocode.
        api_key: OpenRouteService API key.
    """
    params = urllib.parse.urlencode({
        'api_key': api_key,
        'text':    address,
        'size':    1,  # Only the best match is needed.
    })
    url = f"{ORS_GEOCODE_URL}?{params}"

    try:
        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))

        features = data.get('features', [])
        if not features:
            logger.warning("No geocoding results returned for address: '%s'", address)
            return None

        # ORS returns coordinates as [longitude, latitude].
        coordinates = features[0]['geometry']['coordinates']
        return (coordinates[0], coordinates[1])

    except urllib.error.URLError as exc:
        logger.error("Geocoding request failed for '%s': %s", address, exc)
        return None
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        logger.error("Unexpected geocoding response structure for '%s': %s", address, exc)
        return None


def _get_route_distance_metres(
    origin: tuple,
    destination: tuple,
    api_key: str
) -> float | None:
    """
    Request the driving route between two coordinate pairs and return
    the total route distance in metres.

    Returns the distance in metres as a float, or None if the request fails.

    Args:
        origin:      (longitude, latitude) tuple for the starting point.
        destination: (longitude, latitude) tuple for the destination.
        api_key:     OpenRouteService API key.
    """
    payload = json.dumps({
        'coordinates': [
            list(origin),
            list(destination),
        ],
    }).encode('utf-8')

    url = f"{ORS_DIRECTIONS_URL}?api_key={api_key}"

    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'Accept':       'application/json',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode('utf-8'))

        # ORS returns distance in metres under routes[0].summary.distance.
        distance_m = data['routes'][0]['summary']['distance']
        return float(distance_m)

    except urllib.error.URLError as exc:
        logger.error("ORS directions request failed: %s", exc)
        return None
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        logger.error("Unexpected ORS directions response structure: %s", exc)
        return None
import pytest
from bot.geo import haversine, is_within_radius

# Lisboa: 38.7169, -9.1399
LISBON_LAT = 38.7169
LISBON_LON = -9.1399


def test_within_radius_close_point_is_inside():
    # ~1 km north of Lisboa
    point_lat = 38.7259
    point_lon = -9.1399
    assert is_within_radius(point_lat, point_lon, LISBON_LAT, LISBON_LON, radius_km=10)


def test_outside_radius_far_point_is_outside():
    # Setúbal, ~approximately 28 km south of Lisboa
    setubal_lat = 38.5244
    setubal_lon = -8.8882
    assert not is_within_radius(setubal_lat, setubal_lon, LISBON_LAT, LISBON_LON, radius_km=10)


def test_on_boundary_is_inside():
    # A point whose haversine distance from Lisboa is exactly 10 km should be <= radius
    # Use a point ~10 km north (approx 0.09 degrees latitude)
    point_lat = 38.8069  # ~10 km north
    point_lon = -9.1399
    distance = haversine(point_lat, point_lon, LISBON_LAT, LISBON_LON)
    # Pass the exact computed distance as the radius — boundary must be inclusive
    assert is_within_radius(point_lat, point_lon, LISBON_LAT, LISBON_LON, radius_km=distance)


def test_same_point_is_within_zero_radius():
    distance = haversine(LISBON_LAT, LISBON_LON, LISBON_LAT, LISBON_LON)
    assert distance == 0.0
    assert is_within_radius(LISBON_LAT, LISBON_LON, LISBON_LAT, LISBON_LON, radius_km=0)


def test_haversine_lisbon_to_porto_approx_274km():
    # Porto: 41.1496, -8.6109
    PORTO_LAT = 41.1496
    PORTO_LON = -8.6109
    distance = haversine(LISBON_LAT, LISBON_LON, PORTO_LAT, PORTO_LON)
    assert abs(distance - 274) <= 5, f"Expected ~274 km, got {distance:.1f} km"

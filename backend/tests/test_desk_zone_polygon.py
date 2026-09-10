"""Covers the desk-zone free-shape drawing change: desk_db.py storing/
reading an arbitrary polygon (not just a drag-rectangle) and
desk_tracker.py's point-in-polygon occupancy check, including backward
compatibility for zones drawn before the polygon column existed."""

import pytest

from app import desk_db
from app.desk_tracker import DeskTracker


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """desk_db.py owns its own DB_PATH (same real file as every other *_db
    module, but not routed through conftest's shared face_db-based temp_db
    fixture) — a real throwaway SQLite DB, same convention as the rest of
    this codebase's tests."""
    db_path = tmp_path / "test_desk.db"
    monkeypatch.setattr(desk_db, "DB_PATH", db_path)
    desk_db.init_db()
    return db_path


def test_create_zone_stores_polygon_and_derives_bounding_box(temp_db):
    triangle = [[0.1, 0.1], [0.5, 0.1], [0.3, 0.4]]
    zone_id = desk_db.create_zone(camera_id=1, polygon=triangle)

    zone = desk_db.get_zone(zone_id)
    assert zone["polygon"] == triangle
    assert zone["zone_label"] == "Desk 1"
    # bounding box derived from the polygon, not a separately-drawn rectangle
    assert zone["x1"] == 0.1 and zone["y1"] == 0.1
    assert zone["x2"] == 0.5 and zone["y2"] == 0.4


def test_create_zone_auto_labels_reuse_gaps(temp_db):
    id1 = desk_db.create_zone(1, [[0, 0], [1, 0], [1, 1]])
    id2 = desk_db.create_zone(1, [[0, 0], [1, 0], [1, 1]])
    desk_db.delete_zone(id1)
    id3 = desk_db.create_zone(1, [[0, 0], [1, 0], [1, 1]])
    labels = {z["id"]: z["zone_label"] for z in desk_db.list_zones(1)}
    assert labels[id2] == "Desk 2"
    assert labels[id3] == "Desk 1"  # reused the gap left by deleting id1


def test_list_zones_synthesizes_polygon_for_pre_migration_rectangle_rows(temp_db):
    """A zone written before the polygon column existed has polygon=NULL in
    the DB — list_zones/get_zone must still hand back a usable 4-corner
    polygon derived from x1..y2, so desk_tracker's point-in-polygon check
    works unchanged for it."""
    with desk_db.get_connection() as conn:
        conn.execute(
            "INSERT INTO desk_zones (camera_id, zone_label, x1, y1, x2, y2, created_at) "
            "VALUES (1, 'Desk 1', 0.2, 0.2, 0.6, 0.6, 0)"
        )
    zone = desk_db.list_zones(1)[0]
    assert zone["polygon"] == [[0.2, 0.2], [0.6, 0.2], [0.6, 0.6], [0.2, 0.6]]


def test_in_zone_true_for_point_inside_triangle(temp_db):
    zone = {"polygon": [[0.0, 0.0], [1.0, 0.0], [0.5, 1.0]]}
    assert DeskTracker._in_zone(0.5, 0.3, zone) is True


def test_in_zone_false_for_point_outside_triangle(temp_db):
    zone = {"polygon": [[0.0, 0.0], [1.0, 0.0], [0.5, 1.0]]}
    assert DeskTracker._in_zone(0.02, 0.9, zone) is False


def test_in_zone_works_for_plain_rectangle_polygon(temp_db):
    # the shape list_zones synthesizes for a pre-migration row
    zone = {"polygon": [[0.2, 0.2], [0.6, 0.2], [0.6, 0.6], [0.2, 0.6]]}
    assert DeskTracker._in_zone(0.4, 0.4, zone) is True
    assert DeskTracker._in_zone(0.9, 0.9, zone) is False


def test_in_zone_works_for_a_pentagon(temp_db):
    pentagon = [[0.5, 0.0], [0.9, 0.35], [0.75, 0.85], [0.25, 0.85], [0.1, 0.35]]
    zone = {"polygon": pentagon}
    assert DeskTracker._in_zone(0.5, 0.5, zone) is True  # center
    assert DeskTracker._in_zone(0.01, 0.01, zone) is False  # far outside

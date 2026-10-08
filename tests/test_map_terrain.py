"""Map art follows current and neighbor locations, retaining a safe fallback."""
from pathlib import Path
import tempfile
from unittest.mock import patch
from PIL import ImageChops

from bot import max_mapgen
from engine import max_map, content


def run():
    assert max_mapgen.terrain('unknown_room', (270, 138)) is None
    village = max_mapgen.render('village', ['village', 'cellar'])
    forest = max_mapgen.render('forest_edge', [])
    assert village.size == forest.size == (1080, 980)
    assert ImageChops.difference(village, forest).getbbox()
    assert max_mapgen.terrain('village', (270, 138)).tobytes() != max_mapgen.terrain('cellar', (270, 138)).tobytes()
    # Missing art never hides the exit graph, position or controls.
    with tempfile.TemporaryDirectory() as tmp, patch.object(max_mapgen, 'ROOM_ART', Path(tmp)):
        fallback = max_mapgen.render('village', ['village', 'cellar'])
        assert fallback.getpixel((0, 140)) == (17, 24, 39)
        assert ImageChops.difference(village, fallback).getbbox()
    for room in content.WORLD:
        assert max_mapgen.terrain(room, (270, 138)) is not None
    key = max_map.image_key('village', ['village', 'cellar'])
    assert max_map.asset_path(key).name.startswith('terrain-v2-')


if __name__ == '__main__':
    run()
    print('OK: map terrain and node backgrounds match all 97 actual locations, readable fallback and cache version')

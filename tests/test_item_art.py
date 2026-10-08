"""All catalog art, genuine alpha, rarity UI colors and seeded cache correctness."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from PIL import Image

from bot import item_images
from engine import item_art, content, rarity
from scripts import optimize_item_art


def test_optimizer():
    with tempfile.TemporaryDirectory() as tmp, patch.object(optimize_item_art, 'ROOT', Path(tmp)):
        source = Path(tmp) / 'sprite.png'
        fixture = Image.new('RGBA', (32, 32), (20, 150, 200, 255))
        fixture.putpixel((0, 0), (0, 0, 0, 0))
        fixture.save(source)
        assert optimize_item_art.optimize(source) is None
        destination = source.with_suffix('.webp')
        assert destination.exists()
        before = destination.stat().st_mtime_ns
        assert optimize_item_art.optimize(source) is None
        assert destination.stat().st_mtime_ns == before  # Verified files are reused.
        destination.write_bytes(b'interrupted conversion')
        assert optimize_item_art.optimize(source) is None
        with Image.open(destination) as image:
            assert image.format == 'WEBP' and image.getchannel('A').getextrema() == (0, 255)
        opaque = Path(tmp) / 'opaque.png'
        Image.new('RGB', (32, 32), 'white').save(opaque)
        assert optimize_item_art.optimize(opaque) == 'opaque.png'
        assert not opaque.with_suffix('.webp').exists()


def test_catalog():
    doc = json.loads(Path('docs/ITEM_ART_PROMPTS.json').read_text(encoding='utf-8'))
    assets = doc['assets']
    covered = [key for asset in assets for key in asset['items']]
    assert len(assets) == 179 and len(covered) == len(set(covered)) == 317
    assert set(covered) == item_art.BASES
    for asset in assets:
        path = Path(asset['file'])
        assert path.is_file(), path
        with Image.open(path) as image:
            assert image.format == 'WEBP' and max(image.size) <= 768
            assert 'A' in image.getbands()
            low, high = image.getchannel('A').getextrema()
            assert low == 0 and high > 200
            image.verify()
        for key in asset['items']:
            assert Path(item_images.art_file(key)).resolve() == path.resolve()
        first = asset['items'][0]
        assert asset['canonical_description'] == content.ITEMS[first].get('desc', '')


def test_cards():
    assert item_art.COLORS == item_images.RARITY_RGB
    assert item_art.COLORS['common'] == (255, 255, 255)
    assert rarity.META['blue']['name'] == 'Раритетная'
    assert rarity.META['purple']['name'] == 'Эпическая'
    assert [rarity.META[key]['mult'] for key in rarity.RARITY_ORDER] == [1., 1.3, 1.7, 2.2, 3., 4.]
    with tempfile.TemporaryDirectory() as tmp, patch.object(item_art, 'ROOT', Path(tmp)):
        paths = []
        pixels = {}
        for tier in rarity.RARITY_ORDER:
            key = rarity.encode('железный_меч', tier, 42 if tier != 'common' else None)
            asset = item_art.image_key(key)
            assert item_art.snapshot(asset) == key
            assert not item_art.asset_path(asset).exists()
            path = item_art.render_asset(asset)
            paths.append(path)
            with Image.open(path) as image:
                assert image.format == 'JPEG' and image.size == (768, 768)
                pixels[tier] = image.getpixel((30, 380))
                image.verify()
            assert Path(item_images.card_image(key)) == path
        assert len(set(paths)) == 6
        assert min(pixels['common']) > 248
        r, g, b = pixels['green']; assert g > r+25 and g > b+25
        r, g, b = pixels['blue']; assert b > r+40 and b > g+40
        r, g, b = pixels['purple']; assert b > g+50 and r > g+30
        r, g, b = pixels['gold']; assert r > g > b+40
        r, g, b = pixels['red']; assert r > g+70 and r > b+70
        first = item_art.render_asset(item_art.image_key('железный_меч#gold#1'))
        second = item_art.render_asset(item_art.image_key('железный_меч#gold#2'))
        assert first != second
        first.unlink()
        assert item_art.render_asset(item_art.image_key('железный_меч#gold#1')).exists()
    for invalid in (None, '../.env', 'file:///etc/passwd', 'железный_меч#unknown',
                    'железный_меч#gold#../x', 'железный_меч#gold#-1', 'железный_меч#gold#1#x'):
        assert not item_art.valid_item(invalid)
    assert item_images.art_file('../.env') is None


if __name__ == '__main__':
    test_optimizer()
    test_catalog()
    test_cards()
    print('OK: all 317 catalog items covered by 179 transparent sprites, six rarity backgrounds, immutable balance and per-seed JPEG cache')

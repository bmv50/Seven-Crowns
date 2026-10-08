"""Mechanical WEBP optimization only: preserve imagegen alpha, never cut out."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sys
import tempfile
from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / 'images' / 'items_v2'


def optimize(source):
    destination = source.with_suffix('.webp')
    temporary = None
    try:
        if destination.is_file() and destination.stat().st_mtime >= source.stat().st_mtime:
            try:
                with Image.open(destination) as existing:
                    if (existing.format == 'WEBP' and max(existing.size) <= 768
                            and 'A' in existing.getbands()
                            and existing.getchannel('A').getextrema()[0] == 0
                            and existing.getchannel('A').getextrema()[1] > 200):
                        existing.verify()
                        return None
            except (OSError, ValueError):
                pass  # Recover an interrupted conversion from the original PNG.
        with Image.open(source) as original:
            if ('A' not in original.getbands() or original.getchannel('A').getextrema()[0] != 0
                    or original.getchannel('A').getextrema()[1] <= 200):
                raise ValueError('Genuine alpha missing; regenerate with imagegen')
            image = original.convert('RGBA')
            image.thumbnail((768, 768), Image.Resampling.LANCZOS)
            with tempfile.NamedTemporaryFile(dir=ROOT, suffix='.webp', delete=False) as tmp:
                temporary = Path(tmp.name)
            image.save(temporary, 'WEBP', quality=95, method=4, exact=True)
        with Image.open(temporary) as verified:
            if ('A' not in verified.getbands() or verified.getchannel('A').getextrema()[0] != 0
                    or verified.getchannel('A').getextrema()[1] <= 200):
                raise ValueError('Alpha not preserved')
            verified.verify()
        temporary.replace(destination)
        return None
    except (OSError, ValueError) as exc:
        print(source.name, type(exc).__name__)
        return source.name
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    sources = sorted(ROOT.glob('*.png'))
    with ThreadPoolExecutor(max_workers=4) as workers:
        failed = [name for name in workers.map(optimize, sources) if name is not None]
    total = len(sources)-len(failed)
    print('Optimized:', total, 'Failed:', failed)
    return bool(failed)


if __name__ == '__main__':
    sys.exit(main())

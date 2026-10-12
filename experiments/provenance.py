import hashlib
from pathlib import Path


def source_hashes():
    root=Path(__file__).resolve().parent.parent
    paths=sorted(root.glob('semantic_video/*.py'))+sorted(root.glob('experiments/*.py'))
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest() for p in paths}

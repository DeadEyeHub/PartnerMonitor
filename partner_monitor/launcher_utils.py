import json
import re

def within(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()): raise ValueError('Invalid file path')
    return path


def result_json(output):
    # CLI emits a final pretty-printed JSON object after its progress lines.
    starts = [m.start() for m in re.finditer(r'^\{', output, re.M)]
    for start in reversed(starts):
        try: return json.loads(output[start:])
        except json.JSONDecodeError: continue
    return {}



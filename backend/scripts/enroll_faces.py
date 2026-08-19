"""Reads every photo in backend/data/enrollment_photos/, computes a face
embedding for each, and stores it against the person's name (derived from
the filename) in SQLite. Re-run any time the photo set changes — it
clears and rebuilds the table each time.
"""

import re
import sys
from pathlib import Path

import cv2
from insightface.app import FaceAnalysis

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import face_db  # noqa: E402

PHOTOS_DIR = Path(__file__).resolve().parent.parent / "data" / "enrollment_photos"


def name_from_filename(path: Path) -> str:
    stem = path.stem
    stem = re.sub(r"_\d+$", "", stem)  # strip "_2", "_3" duplicate suffixes
    return stem.replace("_", " ").strip()


def main():
    photos = sorted(PHOTOS_DIR.glob("*.jpg"))
    if not photos:
        print(f"No photos found in {PHOTOS_DIR}")
        return

    print(f"Loading face model, then enrolling {len(photos)} photos...")
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640), det_thresh=0.3)

    face_db.init_db()
    face_db.clear_faces()

    enrolled, skipped = 0, []
    for photo in photos:
        img = cv2.imread(str(photo))
        if img is None:
            skipped.append((photo.name, "unreadable"))
            continue
        faces = app.get(img)
        if not faces:
            skipped.append((photo.name, "no face detected"))
            continue
        # if more than one face is in the reference photo, use the largest
        face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        name = name_from_filename(photo)
        face_db.add_face(name, photo.name, face.embedding)
        enrolled += 1
        print(f"  enrolled: {name:30s} <- {photo.name}")

    print(f"\nDone. Enrolled {enrolled} face embeddings.")
    if skipped:
        print(f"Skipped {len(skipped)}:")
        for fname, reason in skipped:
            print(f"  {fname}: {reason}")


if __name__ == "__main__":
    main()

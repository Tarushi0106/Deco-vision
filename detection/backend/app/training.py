"""Promote-and-validate cycle for this service's own gallery. No trained
model file — recognition is nearest-neighbor cosine similarity (see
recognizer.py), so "training" means: validate each newly labeled sample
against the CURRENT gallery (reject a likely mislabel rather than let it
degrade someone else's matching), add the accepted ones to the in-memory
gallery immediately (recognizer.add_to_gallery — no restart needed), and
record a real, measured leave-one-out validation accuracy.
"""

import json
import logging
from collections import defaultdict

import numpy as np

from . import config, face_training_db
from .recognizer import get_recognizer

logger = logging.getLogger("detection.training")


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


def _leave_one_out_validation(gallery: list[tuple[str, np.ndarray]]):
    """Real, measured accuracy grounded in the actual matching algorithm —
    NOT a live-camera measurement (see recognizer.py's docstring for the
    same caveat this app.py surfaces to the API/frontend). Returns
    (accuracy | None, sample_count, per_person dict); None means genuinely
    insufficient data (no person with >=2 samples), reported as such."""
    by_name: dict[str, list[np.ndarray]] = defaultdict(list)
    for name, emb in gallery:
        by_name[name].append(emb)
    eligible = {n: v for n, v in by_name.items() if len(v) >= 2}
    if not eligible:
        return None, 0, {}

    per_person = {}
    total = correct = 0
    for name, embs in eligible.items():
        p_total = p_correct = 0
        for i, held_out in enumerate(embs):
            best_name, best_score = "Unknown", 0.0
            for other_name, other_embs in by_name.items():
                for j, other_emb in enumerate(other_embs):
                    if other_name == name and j == i:
                        continue
                    score = _cosine_sim(held_out, other_emb)
                    if score > best_score:
                        best_name, best_score = other_name, score
            is_correct = best_score >= config.RECOGNITION_SIMILARITY_THRESHOLD and best_name == name
            p_total += 1
            p_correct += int(is_correct)
        per_person[name] = {
            "samples": p_total, "correct": p_correct,
            "accuracy": round(p_correct / p_total, 3) if p_total else None,
        }
        total += p_total
        correct += p_correct

    overall = round(correct / total, 3) if total else None
    return overall, total, per_person


def run_promotion_cycle(min_samples: int = 0) -> dict:
    candidates = face_training_db.get_labeled_awaiting_promotion()
    if len(candidates) < min_samples:
        return {"status": "skipped_insufficient_new_data", "samples_considered": len(candidates)}
    if not candidates:
        return {"status": "skipped_no_new_data", "samples_considered": 0}

    recognizer = get_recognizer()
    gallery = recognizer.gallery_snapshot()

    promoted_ids, rejected = [], []
    for sample in candidates:
        name = sample["name"]
        embedding = sample["embedding"]
        conflict_name, conflict_score = None, 0.0
        for other_name, other_emb in gallery:
            if other_name == name:
                continue
            score = _cosine_sim(embedding, other_emb)
            if score > conflict_score:
                conflict_name, conflict_score = other_name, score
        if conflict_name is not None and conflict_score >= config.FACE_TRAINING_VALIDATION_CONFLICT_THRESHOLD:
            reason = f"embedding matches existing gallery person '{conflict_name}' at {conflict_score:.2f}"
            face_training_db.mark_rejected(sample["id"], reason)
            rejected.append({"id": sample["id"], "name": name, "reason": reason})
            logger.warning("face-training: rejected sample %s (%s): %s", sample["id"], name, reason)
            continue

        recognizer.add_to_gallery(name, embedding)
        gallery.append((name, embedding))
        promoted_ids.append(sample["id"])

    if promoted_ids:
        face_training_db.mark_promoted(promoted_ids)

    final_gallery = recognizer.gallery_snapshot()
    accuracy, val_samples, per_person = _leave_one_out_validation(final_gallery)
    total_people = len({n for n, _ in final_gallery})

    run_id = face_training_db.add_training_run(
        samples_considered=len(candidates), samples_promoted=len(promoted_ids), samples_rejected=len(rejected),
        total_gallery_people=total_people, total_gallery_samples=len(final_gallery), status="success",
        validation_accuracy=accuracy, validation_samples=val_samples, per_person_json=json.dumps(per_person),
    )
    logger.info(
        "face-training: run %s — promoted %d, rejected %d, %d people / %d samples in gallery, "
        "validation_accuracy=%s (%s samples)",
        run_id, len(promoted_ids), len(rejected), total_people, len(final_gallery), accuracy, val_samples,
    )
    return {
        "status": "success", "run_id": run_id, "samples_considered": len(candidates),
        "samples_promoted": len(promoted_ids), "samples_rejected": len(rejected), "rejected": rejected,
        "total_gallery_people": total_people, "total_gallery_samples": len(final_gallery),
        "validation_accuracy": accuracy, "validation_samples": val_samples, "per_person": per_person,
    }

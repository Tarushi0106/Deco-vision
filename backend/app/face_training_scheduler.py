"""Background retraining job for the continuous face-collection/labeling
pipeline (face_training_db.py).

"Training" here has no separate model file to fit — recognition is
nearest-neighbor cosine similarity straight against enrolled_faces (see
recognizer.py). So "promote a batch" means: validate each newly labeled
sample doesn't conflict with a DIFFERENT already-enrolled person, insert
the accepted ones into enrolled_faces (face_db.add_face — the exact same
table/function every other enrollment path already uses), then tell every
live detection worker to reload its gallery (pipeline_manager.reload_faces,
which already existed for this purpose before this feature). No new
recognition engine, no second model.

Runs on its own APScheduler BackgroundScheduler (own thread), same pattern
as scheduler.py's daily jobs — started/stopped from main.py alongside it,
just on a much shorter interval (config.FACE_TRAINING_CHECK_INTERVAL_SECONDS)
since this isn't a once-a-day job. Never runs inside a request handler or
the detection worker processes; a run here only ever touches the main
process's own DB connections and, at the very end, the trivial (queue
put_nowait) reload signal.
"""

import json
import logging
import time
from collections import defaultdict

import numpy as np
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from . import config, face_db, face_training_db

logger = logging.getLogger("dashboard.face_training")

_scheduler: BackgroundScheduler | None = None


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


def _leave_one_out_validation(enrolled: list[tuple[str, np.ndarray]]):
    """Real, measured accuracy — not invented — grounded in the actual
    matching algorithm recognizer.py uses: for each enrolled sample (with
    at least one OTHER sample of the same person also enrolled), remove it
    from the gallery and see whether the remaining gallery would still
    correctly match it. This is NOT a live-camera measurement (a real photo
    held perfectly still and well-lit is not the same as a face crop from a
    moving camera) — that distinction is carried through to the API/status
    output, never blurred into one "accuracy" number.

    Returns (overall_accuracy | None, validation_sample_count, per_person dict).
    None accuracy means genuinely insufficient data (see VALIDATION_MIN_
    below), reported as such rather than a fabricated percentage.
    """
    by_name: dict[str, list[np.ndarray]] = defaultdict(list)
    for name, emb in enrolled:
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
                        continue  # the held-out sample itself
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
    """Runs one promote-and-validate cycle right now, regardless of the
    scheduler's own interval — used by both the periodic job below and the
    manual POST /api/face-training/train endpoint, so a manual trigger
    behaves identically to an automatic one rather than being a second,
    slightly-different code path. min_samples=0 (the manual-trigger default)
    means "promote whatever is labeled and waiting, even if just one" —
    the batching/threshold policy is what decides WHEN this gets called
    automatically, not what this function itself is willing to do."""
    candidates = face_training_db.get_labeled_awaiting_promotion()
    if len(candidates) < min_samples:
        return {"status": "skipped_insufficient_new_data", "samples_considered": len(candidates)}
    if not candidates:
        return {"status": "skipped_no_new_data", "samples_considered": 0}

    enrolled = face_db.load_all_faces()  # [(name, embedding), ...]

    promoted_ids, rejected = [], []
    for sample in candidates:
        name = sample["name"]
        embedding = sample["embedding"]
        conflict_name, conflict_score = None, 0.0
        for other_name, other_emb in enrolled:
            if other_name == name:
                continue
            score = _cosine_sim(embedding, other_emb)
            if score > conflict_score:
                conflict_name, conflict_score = other_name, score
        if conflict_name is not None and conflict_score >= config.FACE_TRAINING_VALIDATION_CONFLICT_THRESHOLD:
            reason = f"embedding matches existing enrolled person '{conflict_name}' at {conflict_score:.2f}"
            face_training_db.mark_rejected(sample["id"], reason)
            rejected.append({"id": sample["id"], "name": name, "reason": reason})
            logger.warning("face-training: rejected sample %s (%s): %s", sample["id"], name, reason)
            continue

        try:
            face_db.add_face(name, source_photo=sample["image_path"], embedding=embedding)
            enrolled.append((name, embedding))  # so a later candidate this same batch sees it too
            promoted_ids.append(sample["id"])
        except Exception:
            logger.exception("face-training: failed to promote sample %s", sample["id"])

    if promoted_ids:
        face_training_db.mark_promoted(promoted_ids)
        # Hot-reloads every live detection worker's gallery — the exact
        # same signal path camera Allow List sync / manual enrollment
        # already use (see main.py). No worker restart, no live-video
        # interruption.
        from .pipeline import pipeline_manager
        pipeline_manager.reload_faces()

    final_enrolled = face_db.load_all_faces()
    accuracy, val_samples, per_person = _leave_one_out_validation(final_enrolled)
    total_people = len({n for n, _ in final_enrolled})

    run_id = face_training_db.add_training_run(
        samples_considered=len(candidates),
        samples_promoted=len(promoted_ids),
        samples_rejected=len(rejected),
        total_enrolled_people=total_people,
        total_enrolled_samples=len(final_enrolled),
        status="success",
        validation_accuracy=accuracy,
        validation_samples=val_samples,
        per_person_json=json.dumps(per_person),
    )
    logger.info(
        "face-training: run %s — promoted %d, rejected %d, %d people / %d samples enrolled, "
        "validation_accuracy=%s (%s samples)",
        run_id, len(promoted_ids), len(rejected), total_people, len(final_enrolled), accuracy, val_samples,
    )
    return {
        "status": "success", "run_id": run_id, "samples_considered": len(candidates),
        "samples_promoted": len(promoted_ids), "samples_rejected": len(rejected), "rejected": rejected,
        "total_enrolled_people": total_people, "total_enrolled_samples": len(final_enrolled),
        "validation_accuracy": accuracy, "validation_samples": val_samples, "per_person": per_person,
    }


def _scheduled_check() -> None:
    """The periodic job body — decides WHETHER to promote (batch-size-or-
    max-interval policy), then delegates the actual work to
    run_promotion_cycle so both paths share one implementation."""
    try:
        candidates = face_training_db.get_labeled_awaiting_promotion()
        if not candidates:
            return
        last_run = face_training_db.get_latest_training_run()
        time_since_last = time.time() - last_run["trained_at"] if last_run else float("inf")
        should_run = (
            len(candidates) >= config.FACE_TRAINING_MIN_NEW_SAMPLES
            or time_since_last >= config.FACE_TRAINING_MAX_INTERVAL_SECONDS
        )
        if not should_run:
            return
        run_promotion_cycle(min_samples=1)
    except Exception:
        logger.exception("face-training: scheduled promotion cycle failed")


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _scheduled_check,
        trigger=IntervalTrigger(seconds=config.FACE_TRAINING_CHECK_INTERVAL_SECONDS),
        id="face_training_promotion_check",
    )
    _scheduler.start()
    logger.info(
        "face-training: background promotion check scheduled every %.0fs "
        "(promotes at >=%d new labeled samples or %.0fs elapsed)",
        config.FACE_TRAINING_CHECK_INTERVAL_SECONDS,
        config.FACE_TRAINING_MIN_NEW_SAMPLES, config.FACE_TRAINING_MAX_INTERVAL_SECONDS,
    )


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None

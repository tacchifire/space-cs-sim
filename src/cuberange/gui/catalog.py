"""Exercise metadata for the local GUI, delegated to cuberange.exercises.

The front-matter parser used to be a second copy of the one in tools/syllabus.py; both now
delegate to the single implementation in `cuberange.exercises`.
"""
from .. import exercises as _exercises_module

EXERCISES = _exercises_module.EXERCISES_DIR
FIELDS = _exercises_module.FIELDS

#: Fields the exercise list endpoint exposes: the curriculum summary plus the hardware
#: classification (`targets`, `hw_class`). `hw_evidence` stays on the full record returned
#: by get_exercise(), where there is room for the longer phrase.
LIST_FIELDS = ("id", "title", "layer", "difficulty", "duration", "targets", "hw_class", "dir")


def exercises() -> list[dict]:
    return _exercises_module.all_exercises(EXERCISES)


def list_exercises() -> list[dict]:
    return [{key: entry[key] for key in LIST_FIELDS} for entry in exercises()]


def get_exercise(exercise_id: str) -> dict | None:
    return _exercises_module.find_exercise(exercise_id, EXERCISES)

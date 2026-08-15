"""
Race-safe reference generation — the two-phase pattern carried forward from the
prototype (TDD.md §4.5): insert the row with a throwaway placeholder, then derive
the real reference from the row's own already-unique primary key and update it in.
Two concurrent inserts can never collide, because each derives from its own PK
rather than a racy `SELECT COUNT(*)+1`.
"""
import uuid


def placeholder_ref() -> str:
    return f"TMP-{uuid.uuid4().hex}"


def finalize_ref(instance, ref_field: str, ref_value: str) -> None:
    setattr(instance, ref_field, ref_value)
    instance.save(update_fields=[ref_field])

# Error Log

---

## 2026-05-29

**Error:** Tests hang after the first OK — `DROP TABLE` in `_make_session()` blocks indefinitely with no output.

**Root cause:** Each test's assertion (`session.query(DogORM).count()`) started a new implicit transaction after the commit; the session was never closed, leaving the connection "idle in transaction" in Postgres and holding an `ACCESS SHARE` lock that blocked the next test's `DROP TABLE` (which needs `ACCESS EXCLUSIVE`).

**Fix:** Moved to a module-level engine and `_current_session` reference; `_make_session()` now calls `_current_session.close()` before `drop_all()`, rolling back the implicit transaction and releasing all locks.

**Files changed:** `tests/test_upsert_dog.py`

---

# CS Learning Notes

---

## Python: The Underscore Prefix Convention

The single underscore prefix (`_`) on a function or variable name is a **convention**, not a language rule. Python won't stop you from calling `_my_function()` from anywhere — but the underscore signals intent: *this is an internal implementation detail, not part of the public interface.*

### The mental model

When you design a module, you're implicitly defining two layers:

- **Public** — things other modules or callers are meant to use. Named normally: `run()`, `export()`, `main()`
- **Private** — helpers that exist to serve the public functions, not to be called directly. Named with a leading underscore: `_build_url()`, `_normalize_age()`, `_run_scrape()`.

### Why bother if Python doesn't enforce it?

Several real effects:

1. **Tab completion** — most editors and REPLs hide `_` names from autocomplete, so consumers of your module see a cleaner API.
2. **Linters** — tools like `pylint` and `ruff` will warn if code outside the module imports a `_` name.
3. **Wildcard imports** — `from mymodule import *` skips `_` names automatically. Public interface only.
4. **Readability** — a future reader (including you, six months later) immediately knows: this function isn't meant to be called from outside, and isn't a stable API.

### The single-responsibility benefit

The common pattern is to split a long function into a public entry point and a private helper:

```python
def process(data):          # public — called by external code
    _validate(data)
    return _transform(data)

def _validate(data):        # private — only exists to serve process()
    ...

def _transform(data):       # private — same
    ...
```

Without the split, `process()` becomes a long function mixing multiple concerns. The underscore split keeps each function focused on one thing, while making clear that `_validate` and `_transform` are not standalone utilities — they're implementation details of `process`.

### Watch out for

The double underscore (`__name`) is different — that triggers Python's **name mangling** inside classes, making the attribute harder to access from outside (though still not impossible). Single underscore is purely a social contract. Double underscore has a real (if weak) mechanical effect.

---

## Databases: Alembic and Schema Migrations

### The problem `create_all()` can't solve

SQLAlchemy's `Base.metadata.create_all(engine)` creates tables from your ORM models if
they don't exist yet. It works perfectly when starting from scratch. But it has no memory
of what the database looked like before — it can't diff "what's in the DB now" vs "what
the models say should be there."

So when you add a new column to an ORM model six months later, `create_all()` sees the
table already exists and skips it entirely. Your new column is in the Python model but
not in the actual database. Your code crashes trying to write to a column that doesn't exist.

This is the core problem: **`create_all()` is for greenfield only. It cannot evolve a schema.**

### What Alembic is

Alembic is a **versioned migration system**. Every schema change gets its own numbered
script that lives in `alembic/versions/`:

```
alembic/versions/
  0001_initial.py          ← CREATE TABLE dog_profiles, raw_scrapes, dog_profile_history
  0002_add_foster_name.py  ← ALTER TABLE dog_profiles ADD COLUMN foster_name VARCHAR(255)
  0003_add_gin_index.py    ← CREATE INDEX CONCURRENTLY ...
```

Each script has two functions:
- `upgrade()` — apply the change forward
- `downgrade()` — undo it (roll back)

Alembic keeps a special table in your database called `alembic_version` that records
which migration number is currently applied:

```sql
SELECT * FROM alembic_version;
-- Returns: 0003_add_gin_index
```

This is how Alembic knows what to do next time you run it.

### What `alembic upgrade head` means

- `head` = the latest migration script in the `versions/` folder
- `upgrade head` = "apply every migration script that hasn't been applied yet, in order,
  until you reach the latest one"

If your database is at `0001` and you've added `0002` and `0003` to the codebase,
running `alembic upgrade head` applies `0002` then `0003` in sequence. If you're
already at `head`, it does nothing.

### When to run it

| Situation | Run it? |
|---|---|
| First time setting up the project on a new machine | Yes — once, after `createdb fetchr` |
| You just added a new column and generated a new migration | Yes — to apply it to your local DB |
| You pulled new code from git that included a new migration | Yes — after `git pull` |
| Deploying to a new server in the future | Yes — as part of the deploy script, before starting the app |
| Every scrape run / on application startup | No — it's schema management, not runtime |

### The workflow for every future schema change

```
1. Edit models/dog.py                                         (add/change a column)
2. alembic revision --autogenerate -m "add foster_name"       (generate the script)
3. Review the generated file in alembic/versions/             (always read it — autogenerate is ~90% accurate)
4. alembic upgrade head                                       (apply to your local DB)
5. git commit the model change AND the migration script together
```

The migration script is part of the codebase. It gets committed to git alongside the
Python change that created it. That's how a future collaborator — or your future self on
a new machine — can recreate the exact schema with a single command.

### Why `create_tables()` must be removed from the startup path once Alembic is in place

If `main.py` still calls `create_tables()` (which calls `create_all()`) on startup,
you have two competing systems managing your schema. If someone runs the scraper on a
fresh database before running `alembic upgrade head`, `create_all()` creates the tables
directly — but Alembic's `alembic_version` table never gets populated. Now `alembic upgrade head`
tries to re-create tables that already exist and fails with a confusing error.

The rule: once Alembic is set up, `create_tables()` is only called in tests (to set up
the in-memory SQLite fixture). Production schema initialization goes through
`alembic upgrade head` exclusively.

### Interview angle

"We use Alembic to manage schema migrations as versioned, reviewable scripts checked
into git alongside the code changes that require them. This means a deploy is always
`git pull` → `alembic upgrade head` → start the app, in that order, and any schema
rollback is `alembic downgrade -1` rather than a manual ALTER TABLE. The alternative —
`create_all()` at startup — doesn't scale past the first schema change."

---

## SQLAlchemy: Shared `Base` — Interview Revision Note

All ORM models must inherit from the **same** `DeclarativeBase` instance so Alembic can
see them in one metadata graph. In this project `Base` is created in `models/dog.py` and
imported by `models/reference.py` — that's why the comment says "all three share the
same Base." If you accidentally create a second `Base` in another file, those tables
silently disappear from `alembic revision --autogenerate`.

**Q: "How do you ensure Alembic tracks all your models?"**
→ One shared `Base`, `target_metadata = Base.metadata` in `alembic/env.py`.

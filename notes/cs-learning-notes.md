# CS Learning Notes

---

## Python: The Underscore Prefix Convention

The single underscore prefix (`_`) on a function or variable name is a **convention**, not a language rule. Python won't stop you from calling `_my_function()` from anywhere — but the underscore signals intent: *this is an internal implementation detail, not part of the public interface.*

### The mental model

When you design a module, you're implicitly defining two layers:

- **Public** — things other modules or callers are meant to use. Named normally: `run()`, `export()`, `main()`.
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

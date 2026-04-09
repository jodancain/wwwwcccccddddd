"""Wrapper to run LLaMA-Factory with Python 3.14 compatibility patch.

Python 3.14 changed pickle.Pickler._batch_setitems(items) → (items, obj)
which breaks datasets.utils._dill.Pickler._batch_setitems(items).

Usage: python train_wrapper.py train <config_file>
       python train_wrapper.py api <config_file>
"""
import sys

# ====== PATCH: Fix Python 3.14 pickle._batch_setitems ======
if sys.version_info >= (3, 14):
    # Patch datasets._dill.Pickler to accept the new (items, obj) signature
    try:
        import datasets.utils._dill as _dd
        _orig_batch = _dd.Pickler._batch_setitems

        def _fixed_batch(self, items, obj=None):
            return _orig_batch(self, items)

        _dd.Pickler._batch_setitems = _fixed_batch
        print(f"[py314] Patched datasets.utils._dill.Pickler._batch_setitems")
    except Exception as e:
        print(f"[py314] Patch failed: {e}")

    # Also patch dill.Pickler in case it's called directly
    try:
        import dill._dill
        if hasattr(dill._dill.Pickler, '_batch_setitems'):
            _orig_dill = dill._dill.Pickler._batch_setitems
            def _fixed_dill(self, items, obj=None):
                try:
                    return _orig_dill(self, items, obj)
                except TypeError:
                    return _orig_dill(self, items)
            dill._dill.Pickler._batch_setitems = _fixed_dill
    except Exception:
        pass
# ====== END PATCH ======


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python train_wrapper.py <train|api> [config_file]")
        sys.exit(1)

    action = sys.argv[1]
    config_file = sys.argv[2] if len(sys.argv) > 2 else None

    if action == "train":
        sys.argv = ["llamafactory", "train"] + ([config_file] if config_file else [])
        from llamafactory.cli import main
        main()
    elif action == "api":
        sys.argv = ["llamafactory", "api"] + ([config_file] if config_file else [])
        from llamafactory.cli import main
        main()
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)

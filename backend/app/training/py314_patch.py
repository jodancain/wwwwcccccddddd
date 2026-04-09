"""Python 3.14 compatibility patch for dill/datasets.

Python 3.14 changed pickle.Pickler._batch_setitems(self, items) to
_batch_setitems(self, items, obj). The dill library (used by datasets)
overrides save_dict but calls StockPickler.save_dict which now passes
2 args to _batch_setitems, but dill's subclass only accepts 1.

This patch fixes the signature mismatch.

Usage: import this module BEFORE importing datasets or dill.
"""
import sys
import pickle

if sys.version_info >= (3, 14):
    # Get the pure-Python Pickler
    _PyPickler = pickle._Pickler

    # Save the original _batch_setitems
    _original_batch = _PyPickler._batch_setitems

    # Create a wrapper that accepts both signatures
    def _patched_batch_setitems(self, items, obj=None):
        """Accept both old (items) and new (items, obj) signatures."""
        try:
            return _original_batch(self, items, obj)
        except TypeError:
            return _original_batch(self, items)

    _PyPickler._batch_setitems = _patched_batch_setitems

    # Also patch the C Pickler if available
    try:
        import _pickle
        # C pickler doesn't have this issue typically, but just in case
    except ImportError:
        pass

    print("[py314_patch] Patched pickle._batch_setitems for Python 3.14 compat")

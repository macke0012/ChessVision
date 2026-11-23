__all__ = ["classify_raw", "load_models"]


def __getattr__(name):
    # Import lazily so data tools (e.g. chessvision.data_processing) work without TensorFlow.
    if name in __all__:
        from . import chessvision as _chessvision
        return getattr(_chessvision, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

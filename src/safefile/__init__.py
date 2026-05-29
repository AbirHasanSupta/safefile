from ._transaction import Transaction

def transaction(*filepaths: str) -> Transaction:
    """
    Create a transaction context manager that protects the given files.

    Example:
        with transaction("config.json", "data.csv"):
            # modify the files; they will be rolled back on exception.
    """
    return Transaction(*filepaths)

__version__ = "0.1.0"
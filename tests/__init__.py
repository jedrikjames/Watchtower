"""Making tests a package keeps `from tests.helpers import ...` working on a
clean checkout, rather than relying on whatever pytest happens to put on
sys.path."""

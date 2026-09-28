import os

# Checks that reach the network are off for the suite as a whole. Every test
# address is at a reserved .example domain that can never resolve, so a real
# MX lookup would mark each one undeliverable. The tests for each check turn
# it back on with a fake in place of the network.
os.environ.setdefault("MX_CHECK", "0")
os.environ.setdefault("DISTANCE", "0")

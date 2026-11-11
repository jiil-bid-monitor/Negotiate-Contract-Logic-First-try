import unittest

from webapp.bidlogpull import selfcheck


class BidLogPullTest(unittest.TestCase):
    def test_selfcheck(self):
        selfcheck()


if __name__ == "__main__":
    unittest.main()

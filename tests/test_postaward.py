import unittest

from webapp.postaward import selfcheck


class PostAwardTest(unittest.TestCase):
    def test_selfcheck(self):
        selfcheck()


if __name__ == "__main__":
    unittest.main()

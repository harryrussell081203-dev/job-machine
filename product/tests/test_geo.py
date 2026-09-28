"""Distances from postcodes.io, asked once per place.

  1. THE RIGHT QUESTION for each kind of place: a postcode, an outward code,
     a town, or a postcode buried in a longer location.
  2. EVERY ANSWER IS REMEMBERED, including "not found", but a network
     failure is not, so it is asked again next time.
  3. THE DISTANCE is right to the mile.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobseeker import geo  # noqa: E402

ABERDEEN = {"latitude": 57.1497, "longitude": -2.0943, "country": "Scotland"}
EDINBURGH = {"latitude": 55.9533, "longitude": -3.1883, "country": "Scotland"}


class Resp:
    def __init__(self, status, result=None):
        self.status_code, self._result = status, result

    def json(self):
        return {"result": self._result}


class Api:
    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def get(self, url, timeout):
        self.asked.append(url)
        for fragment, answer in self.answers.items():
            if fragment in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return Resp(404)


class Base(unittest.TestCase):
    def setUp(self):
        geo._memory.clear()
        self.addCleanup(geo._memory.clear)
        self.addCleanup(setattr, geo, "store", None)
        geo.store = None


class TheQuestion(Base):
    def test_a_postcode(self):
        api = Api({"/postcodes/AB101XG": Resp(200, ABERDEEN)})
        self.assertEqual(geo.locate("ab10 1xg", get=api.get)["country"],
                         "Scotland")

    def test_a_postcode_inside_a_longer_location(self):
        api = Api({"/postcodes/AB210BH": Resp(200, ABERDEEN)})
        self.assertIsNotNone(geo.locate("Dyce, Aberdeen AB21 0BH", get=api.get))

    def test_an_outward_code(self):
        api = Api({"/outcodes/AB10": Resp(200, ABERDEEN)})
        self.assertIsNotNone(geo.locate("AB10", get=api.get))

    def test_a_town_uses_its_first_part(self):
        api = Api({"/places?q=Westhill": Resp(200, [ABERDEEN])})
        self.assertIsNotNone(geo.locate("Westhill, Aberdeenshire", get=api.get))

    def test_nowhere_is_none(self):
        api = Api({"/places": Resp(200, [])})
        self.assertIsNone(geo.locate("United Kingdom", get=api.get))


class Remembering(Base):
    def test_asked_once(self):
        api = Api({"/places": Resp(200, [ABERDEEN])})
        geo.locate("Aberdeen", get=api.get)
        geo.locate("aberdeen", get=api.get)
        self.assertEqual(len(api.asked), 1)

    def test_not_found_is_remembered_too(self):
        api = Api({})
        geo.locate("Nowhere", get=api.get)
        geo.locate("Nowhere", get=api.get)
        self.assertEqual(len(api.asked), 1)

    def test_a_network_failure_is_asked_again(self):
        api = Api({"/places": OSError("down")})
        self.assertIsNone(geo.locate("Aberdeen", get=api.get))
        geo.locate("Aberdeen", get=api.get)
        self.assertEqual(len(api.asked), 2)

    def test_the_store_carries_it_across_runs(self):
        kept = {}

        class Store:
            def get(self, key):
                return kept.get(key)

            def put(self, key, value):
                kept[key] = value
        geo.store = Store()
        geo.locate("Aberdeen", get=Api({"/places": Resp(200, [ABERDEEN])}).get)
        geo._memory.clear()
        later = Api({})
        self.assertIsNotNone(geo.locate("Aberdeen", get=later.get))
        self.assertEqual(later.asked, [])


class TheDistance(Base):
    def test_aberdeen_to_edinburgh(self):
        a = {"lat": ABERDEEN["latitude"], "lon": ABERDEEN["longitude"]}
        b = {"lat": EDINBURGH["latitude"], "lon": EDINBURGH["longitude"]}
        self.assertAlmostEqual(geo.miles(a, b), 92, delta=2)

    def test_board_coordinates_are_used_first(self):
        class L:
            latitude, longitude, location = 57.0, -2.0, "Somewhere"
        api = Api({})
        self.assertEqual(geo.where_listing_is(L(), get=api.get)["lat"], 57.0)
        self.assertEqual(api.asked, [])


if __name__ == "__main__":
    unittest.main()

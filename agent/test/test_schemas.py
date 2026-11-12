import unittest

from pydantic import ValidationError

from app.schemas import GotoRequest, MovementRequest


class SchemaTests(unittest.TestCase):
    def test_movement_request(self):
        self.assertEqual(MovementRequest(username="Steve").username, "Steve")
        with self.assertRaises(ValidationError):
            MovementRequest(username="bad name")
        with self.assertRaises(ValidationError):
            MovementRequest(username="Steve", extra="rejected")

    def test_goto_requires_finite_numeric_coordinates(self):
        self.assertEqual(GotoRequest(username="Steve", x=1, y=64, z=-2).x, 1)
        with self.assertRaises(ValidationError):
            GotoRequest(username="Steve", x="1", y=64, z=2)
        with self.assertRaises(ValidationError):
            GotoRequest(username="Steve", x=float("inf"), y=64, z=2)

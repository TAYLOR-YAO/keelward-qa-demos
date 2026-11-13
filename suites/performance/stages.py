"""The load profile's stages: (end second, shoppers, name). Shared by the Locust file and the tests."""
STAGES = [(10, 10, "Normal day"), (22, 50, "Expected peak"), (34, 100, "Sale day"), (44, 10, "Recovery")]

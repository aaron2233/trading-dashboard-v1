from indicators import IndicatorProtocol


def test_protocol_importable():
    assert IndicatorProtocol is not None


def test_protocol_has_expected_surface():
    assert hasattr(IndicatorProtocol, "compute")

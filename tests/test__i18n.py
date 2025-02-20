import pytest
from redbot.core._i18n import (
    set_contextual_locale,
)


def test_invalid_contextual_locale_verification():
    """
    Test that set_contextual_locale raises a ValueError when verify_language_code is True
    and the language code is invalid (missing the country/territory part).
    """
    with pytest.raises(
        ValueError, match="Invalid format - language code has to include country code"
    ):
        set_contextual_locale("en", verify_language_code=True)

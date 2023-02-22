import pytest

from discoverx.dx import DX


def test_dx_instantiation():

    dx = DX()
    assert dx.column_type_classification_threshold == 0.95

    # The validation should fail if the database does not exist
    with pytest.raises(ValueError) as e_error:
        dx = DX()
        dx.database = "testdb"
        dx._validate_database()
    assert e_error.value.args[0] == "The given database testdb does not exist."

    # The validation should fail if threshold is outside of [0,1]
    with pytest.raises(ValueError) as e_threshold_error_plus:
        dx = DX(column_type_classification_threshold=1.4)

    with pytest.raises(ValueError) as e_threshold_error_minus:
        dx = DX(column_type_classification_threshold=-1.0)

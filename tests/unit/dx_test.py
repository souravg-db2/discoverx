import pytest
from delta.tables import DeltaTable

from discoverx.dx import DX
from discoverx.dx import Scanner
from discoverx.dx import Classifier
from discoverx import logging

logger = logging.Logging()

@pytest.fixture(scope='module')
def monkeymodule():
    """
    Required for monkeypatching with module scope.
    For more info see
    https://stackoverflow.com/questions/53963822/python-monkeypatch-setattr-with-pytest-fixture-at-module-scope
    """
    with pytest.MonkeyPatch.context() as mp:
        yield mp

@pytest.fixture(autouse=True, scope="module")
def mock_uc_functionality(spark, monkeymodule):
    # apply the monkeypatch for the columns_table_name
    monkeymodule.setattr(Scanner, "COLUMNS_TABLE_NAME", "default.columns_mock")

    # mock classifier method _get_classification_table_from_delta as we don't
    # have catalogs in open source spark
    def get_classification_table_mock(self):
        (schema, table) = self.classification_table_name.split(".")
        self.spark.sql(f"CREATE DATABASE IF NOT EXISTS {schema}")
        self.spark.sql(
            f"""
                CREATE TABLE IF NOT EXISTS {schema + '.' + table} (table_catalog string, table_schema string, table_name string, column_name string, rule_name string, tag_status string, effective_timestamp timestamp, current boolean, end_timestamp timestamp) USING DELTA
                """
        )
        return DeltaTable.forName(self.spark, self.classification_table_name)

    monkeymodule.setattr(Classifier, "_get_classification_table_from_delta", get_classification_table_mock)

    # mock UC's tag functionality
    def set_uc_tags(self, series):
        if (series.action == "set") & (series.tag_status == "active"):
            logger.debug(
                f"Set tag {series.rule_name} for column {series.column_name} of table {series.table_catalog}.{series.table_schema}.{series.table_name}"
            )
        if (series.action == "unset") | (series.tag_status == "inactive"):
            logger.debug(
                f"Unset tag {series.rule_name} for column {series.column_name} of table {series.table_catalog}.{series.table_schema}.{series.table_name}"
            )

    monkeymodule.setattr(Classifier, "_set_tag_uc", set_uc_tags)

    yield

    spark.sql("DROP TABLE IF EXISTS _discoverx.tags")
    spark.sql("DROP SCHEMA IF EXISTS _discoverx")

@pytest.fixture(scope="module", name="dx_ip")
def scan_ip_in_tb1(spark, mock_uc_functionality):
    dx = DX(spark=spark, classification_table_name="_discoverx.tags")
    dx.scan(tables="tb_1", rules="ip_*")
    dx.publish()
    yield dx

    spark.sql("DROP TABLE IF EXISTS _discoverx.tags")


def test_dx_instantiation(spark):

    dx = DX(spark=spark)
    assert dx.column_type_classification_threshold == 0.95

    # The validation should fail if threshold is outside of [0,1]
    with pytest.raises(ValueError) as e_threshold_error_plus:
        dx = DX(column_type_classification_threshold=1.4, spark=spark)

    with pytest.raises(ValueError) as e_threshold_error_minus:
        dx = DX(column_type_classification_threshold=-1.0, spark=spark)

    # simple test for displaying rules
    try:
        dx.display_rules()
    except Exception as display_rules_error:
        pytest.fail(f"Displaying rules failed with {display_rules_error}")


def test_scan_and_msql(spark, dx_ip):
    """
    End-2-end test involving both scanning and searching
    """

    result = dx_ip.msql_experimental("SELECT [ip_v4] as ip FROM *.*.*").collect()

    assert {row.ip for row in result} == {"1.2.3.4", "3.4.5.60"}

    # test what-if
    try:
        _ = dx_ip.msql_experimental("SELECT [ip_v4] as ip FROM *.*.*", what_if=True)
    except Exception as e:
        pytest.fail(f"Test failed with exception {e}")

def test_search(spark, dx_ip):

    # search a specific term and auto-detect matching tags/rules
    result = dx_ip.search("1.2.3.4")
    assert result.collect()[0].table == 'tb_1'
    assert result.collect()[0].search_result.ip_v4.column == 'ip'

    # search all records for specific tag
    result_tags_only = dx_ip.search(search_tags='ip_v4')
    assert {row.search_result.ip_v4.value for row in result_tags_only.collect()} == {"1.2.3.4", "3.4.5.60"}

    # specify catalog, database and table
    result_tags_namespace = dx_ip.search(search_tags='ip_v4', catalog="*", database="default", table="tb_*")
    assert {row.search_result.ip_v4.value for row in result_tags_namespace.collect()} == {"1.2.3.4", "3.4.5.60"}

    # search specific term for list of specified tags
    result_term_tag = dx_ip.search(search_term="3.4.5.60", search_tags=['ip_v4'])
    assert result_term_tag.collect()[0].table == 'tb_1'
    assert result_term_tag.collect()[0].search_result.ip_v4.value == "3.4.5.60"

    with pytest.raises(ValueError) as no_tags_no_terms_error:
        dx_ip.search()
    assert no_tags_no_terms_error.value.args[0] == "Neither search_term nor search_tags have been provided. At least one of them need to be specified."

    with pytest.raises(ValueError) as list_with_ints:
        dx_ip.search(search_tags=[1, 3, 'ip'])
    assert list_with_ints.value.args[0] == "The provided search_tags [1, 3, 'ip'] have the wrong type. Please provide either a str or List[str]."

    with pytest.raises(ValueError) as single_bool:
        dx_ip.search(search_tags=True)
    assert single_bool.value.args[0] == "The provided search_tags True have the wrong type. Please provide either a str or List[str]."


# test multiple tags
def test_search_multiple(spark, mock_uc_functionality):
    dx = DX(spark=spark, classification_table_name="_discoverx.tags")
    dx.scan(tables="tb_1", rules="*")
    dx.publish()

    # search a specific term and auto-detect matching tags/rules
    result = dx.search(search_tags=["ip_v4", "mac"])
    assert result.collect()[0].table == 'tb_1'
    assert result.collect()[0].search_result.ip_v4.column == 'ip'
    assert result.collect()[0].search_result.mac.column == 'mac'

    spark.sql("DROP TABLE IF EXISTS _discoverx.tags")

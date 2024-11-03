import pytest
import sys

if sys.version_info >= (3, 11):
    from tomllib import load as tom_load
    from tomli_w import dump as tom_dump
else:
    import tomli.load as tom_load
    import tomli.dump as tom_dump

from pathlib import Path

from gourmand.prefs import (
    Prefs,
    update_preferences_file_format
)


def test_singleton():
    prefs = Prefs.instance()
    pprefs = Prefs.instance()
    assert prefs == pprefs


def test_get_sets_default():
    """Test that using get with a default value adds it to the dictionnary"""
    prefs = Prefs.instance()

    val = prefs.get('key', 'value')
    assert val == val

    assert prefs['key'] == val  # The value was inserted

    val = prefs.get('anotherkey')
    assert val is None

    with pytest.raises(KeyError):
        prefs['anotherkey']


def test_update_preferences_file_format(tmpdir):
    """Test the update of preferences file format."""

    filename = tmpdir.join('preferences.toml')

    with open(filename, 'wb') as fout:
        tom_dump({'sort_by': {'column': 'title', 'ascending': True}}, fout)

    update_preferences_file_format(Path(tmpdir))

    with open(filename, 'rb') as fin:
        d = tom_load(fin)

    assert 'category' not in d['sort_by'].keys()
    assert d['sort_by']['title'] == True

    with open(filename, 'wb') as fout:
        tom_dump({}, fout)

    update_preferences_file_format(Path(tmpdir))

    with open(filename, 'rb') as fin:
        d = tom_load(fin)

    assert d == {}

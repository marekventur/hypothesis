# coding=utf-8

# Copyright (C) 2013-2015 David R. MacIver (david@drmaciver.com)

# This file is part of Hypothesis (https://github.com/DRMacIver/hypothesis)

# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file, You can
# obtain one at http://mozilla.org/MPL/2.0/.

# END HEADER

from __future__ import division, print_function, absolute_import, \
    unicode_literals

import time

import pytest
import hypothesis.settings as hs
from hypothesis import given, strategy
from hypothesis.errors import Timeout
from hypothesis.database import ExampleDatabase
from hypothesis.internal.compat import hrange, text_type, integer_types
from hypothesis.database.backend import Backend, SQLiteBackend
from hypothesis.database.formats import Format, JSONFormat


def run_round_trip(specifier, value, format=None, backend=None):
    if backend is not None:
        backend = backend()
    else:
        backend = SQLiteBackend()
    db = ExampleDatabase(format=format, backend=backend)
    try:
        storage = db.storage_for(specifier)
        storage.save(value)
        saved = list(storage.fetch())
        assert len(saved) == 1
        strat = strategy(specifier)
        assert strat.to_basic(saved[0]) == strat.to_basic(value)
    finally:
        db.close()


class InMemoryBackend(Backend):

    def __init__(self):
        self.data = {}

    def data_type(self):
        return object

    def save(self, key, value):
        self.data.setdefault(key, set()).add(value)

    def fetch(self, key):
        for v in self.data.get(key, ()):
            yield v


class ObjectFormat(Format):

    def data_type(self):
        return object

    def serialize_basic(self, value):
        if isinstance(value, list):
            return tuple(
                map(self.serialize_basic, value)
            )
        else:
            assert value is None or isinstance(
                value,
                (bool, float, text_type) + integer_types
            )
            return value

    def deserialize_data(self, data):
        if isinstance(data, tuple):
            return list(
                map(self.deserialize_data, data)
            )
        else:
            return data

backend_format_pairs = (
    (SQLiteBackend, None),
    (InMemoryBackend, ObjectFormat()),
)


settings = hs.Settings(
    max_examples=500,
    average_list_length=3.0,
)


def test_errors_if_given_incompatible_format_and_backend():
    with pytest.raises(ValueError):
        ExampleDatabase(
            backend=InMemoryBackend()
        )


def test_storage_does_not_error_if_the_database_is_invalid():
    database = ExampleDatabase()
    ints = database.storage_for(int)
    database.backend.save(ints.key, '["hi", "there"]')
    assert list(ints.fetch()) == []


def test_storage_cleans_up_invalid_data_from_the_db():
    database = ExampleDatabase()
    ints = database.storage_for(int)
    database.backend.save(ints.key, '[false, false, true]')
    assert list(database.backend.fetch(ints.key)) != []
    assert list(ints.fetch()) == []
    assert list(database.backend.fetch(ints.key)) == []


@pytest.mark.parametrize('s', ['', 'abcdefg', '☃'])
def test_can_save_all_strings(s):
    db = ExampleDatabase()
    storage = db.storage_for(text_type)
    storage.save(tuple(s))


def test_db_has_path_in_repr():
    backend = SQLiteBackend(':memory:')
    db = ExampleDatabase(backend=backend)
    assert ':memory:' in repr(db)


def test_storage_has_specifier_in_repr():
    db = ExampleDatabase()
    d = (int, int)
    s = db.storage_for(d)
    assert repr(d) in repr(s)


def test_json_format_repr_is_nice():
    assert repr(JSONFormat()) == 'JSONFormat()'


def test_can_time_out_when_reading_from_database():
    should_timeout = False
    limit = 0
    examples = []
    db = ExampleDatabase()

    try:
        @given(int, settings=hs.Settings(timeout=0.1, database=db))
        def test_run_test(x):
            examples.append(x)
            if should_timeout:
                time.sleep(0.5)
            assert x >= limit

        for i in hrange(10):
            limit = -i
            examples = []
            with pytest.raises(AssertionError):
                test_run_test()

        examples = []

        limit = 0

        with pytest.raises(AssertionError):
            test_run_test()

        limit = min(examples) - 1

        should_timeout = True
        examples = []

        with pytest.raises(Timeout):
            test_run_test()

        assert len(examples) == 1
    finally:
        db.close()


def test_can_handle_more_than_max_examples_values_in_db():
    """This is checking that if we store a large number of examples in the DB
    and then subsequently reduce max_examples below that count, we a) don't
    error (which is how this bug was found) and b) stop at max_examples rather
    than continuing onwards."""
    db = ExampleDatabase()

    try:
        settings = hs.Settings(database=db, max_examples=2)
        seen = []
        first = [True]
        for _ in range(10):
            first[0] = True

            @given(int, settings=settings)
            def test_seen(x):
                if x not in seen:
                    if first[0]:
                        first[0] = False
                        seen.append(x)
                assert x in seen

            try:
                test_seen()
            except AssertionError:
                pass

        assert len(seen) >= 2

        seen = []

        @given(int, settings=hs.Settings(max_examples=1, database=db))
        def test_seen(x):
            seen.append(x)
        test_seen()
        assert len(seen) == 1
    finally:
        db.close()

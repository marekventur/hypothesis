# coding=utf-8

# Copyright (C) 2013-2015 David R. MacIver (david@drmaciver.com)

# This file is part of Hypothesis (https://github.com/DRMacIver/hypothesis)

# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file, You can
# obtain one at http://mozilla.org/MPL/2.0/.

# END HEADER

from __future__ import division, print_function, absolute_import, \
    unicode_literals

import sys
import contextlib
from io import StringIO

import pytest
from hypothesis.reporting import default, with_reporter
from hypothesis.internal.reflection import proxies


@contextlib.contextmanager
def capture_out():
    old_out = sys.stdout
    try:
        new_out = StringIO()
        sys.stdout = new_out
        with with_reporter(default):
            yield new_out
    finally:
        sys.stdout = old_out


def fails_with(e):
    def accepts(f):
        @proxies(f)
        def inverted_test(*arguments, **kwargs):
            with pytest.raises(e):
                f(*arguments, **kwargs)
        return inverted_test
    return accepts

fails = fails_with(AssertionError)

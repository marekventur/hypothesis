# coding=utf-8

# Copyright (C) 2013-2015 David R. MacIver (david@drmaciver.com)

# This file is part of Hypothesis (https://github.com/DRMacIver/hypothesis)

# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file, You can
# obtain one at http://mozilla.org/MPL/2.0/.

# END HEADER

from __future__ import division, print_function, absolute_import, \
    unicode_literals

from random import Random

from hypothesis import strategy
from hypothesis.specifiers import one_of


def test_can_apply_simplifiers_to_other_types():
    r = Random(0)
    s = strategy(one_of((bool, [bool])))
    template1 = s.draw_and_produce_from_random(r)
    while True:
        template2 = s.draw_and_produce_from_random(r)
        if template2[0] != template1[0]:
            break
    for simplify in s.simplifiers(r, template1):
        assert list(simplify(r, template2)) == []
